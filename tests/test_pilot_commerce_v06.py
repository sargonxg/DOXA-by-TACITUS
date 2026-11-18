"""Fixed pilot prices are distinct from real/conservative internal usage.

Synthetic contracts exercise software accounting, not actual customer payment,
commercial viability, expert qualifications or interpretation quality.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest

from doxa.access import LOCAL_OPERATOR, Principal, ROLE_PERMISSIONS
from doxa.pilot_commerce import (CommerceError, accept_quote, admit_quote_work, create_quote,
    get_quote, register_commerce_api, reserve_usage, settle_usage, usage_summary)
from doxa.store import Store, utcnow


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv('DOXA_CLOUD_STATE_URL', raising=False)
    monkeypatch.delenv('DOXA_REQUIRE_ACCEPTED_QUOTE', raising=False)
    value = Store(tmp_path)
    value.init()
    for identity in ('project-a', 'project-b'):
        value.execute('INSERT INTO projects(id,name,created_at) VALUES(?,?,?)', [identity, identity, utcnow()])
    yield value
    value.close()


def body(**extra):
    return {'idempotency_key': 'offer-1', 'profile_id': 'company', 'fixed_price_cents': 50000,
            'processing_cap_microusd': 100_000, 'scope': {'documents': 20, 'characters': 200_000,
            'criteria': 19, 'ocr_pages': 0}, **extra}


def accepted(store, **extra):
    quote = create_quote(store, 'project-a', body(**extra), operator_id='service-operator')
    return accept_quote(store, 'project-a', quote['id'], actor_id='customer-owner', scope_sha256=quote['scope_sha256'])


def test_operator_quote_is_idempotent_exact_scope_and_not_payment(store):
    first = create_quote(store, 'project-a', body(), operator_id='service-operator')
    assert first['fixed_price_cents'] == 50000 and first['processing_cap_microusd'] == 100_000
    assert first['payment_status'] == 'not_recorded' and first['status'] == 'offered'
    assert first['scope_basis'] == 'per_operation_limits'
    assert first['processing_cap_basis'] == 'aggregate_across_quote'
    again = create_quote(store, 'project-a', body(), operator_id='service-operator')
    assert again['duplicate'] and first['id'] == again['id']
    with pytest.raises(CommerceError, match='different quote'):
        create_quote(store, 'project-a', body(fixed_price_cents=1), operator_id='service-operator')
    with pytest.raises(CommerceError, match='scope hash'):
        accept_quote(store, 'project-a', first['id'], actor_id='customer', scope_sha256='forged')
    accepted_quote = accept_quote(store, 'project-a', first['id'], actor_id='customer', scope_sha256=first['scope_sha256'])
    assert accepted_quote['accepted_by'] == 'customer'
    assert accept_quote(store, 'project-a', first['id'], actor_id='another', scope_sha256=first['scope_sha256'])['duplicate']
    assert get_quote(store, 'project-a', first['id'])['accepted_by'] == 'customer'


@pytest.mark.parametrize('changes', [
    {'fixed_price_cents': True}, {'fixed_price_cents': -1}, {'processing_cap_microusd': 0},
    {'valid_days': 91}, {'idempotency_key': 'unsafe path/key'}, {'profile_id': 'universal-bias'},
    {'scope': {'documents': 20}}, {'currency': 'USD'}, {'operator_id': 'browser-owner'},
    {'scope': {'documents': 20, 'characters': 200_000, 'criteria': 19, 'ocr_pages': False}},
])
def test_quote_rejects_ambiguous_or_client_authority_fields(store, changes):
    with pytest.raises(CommerceError):
        create_quote(store, 'project-a', body(**changes), operator_id='operator')


def test_project_binding_expired_offer_and_scope_admission(store):
    quote = accepted(store)
    with pytest.raises(CommerceError) as error:
        get_quote(store, 'project-b', quote['id'])
    assert error.value.status_code == 404
    assert admit_quote_work(store, 'project-a', quote['id'], profile_id='company', documents=20,
                            characters=200_000, criteria=19) == get_quote(store, 'project-a', quote['id'])
    for extra in ({'documents': 21}, {'criteria': 20}, {'ocr_pages': 1}, {'profile_id': 'personal'}):
        values = {'profile_id': 'company', 'documents': 20, 'characters': 200_000, 'criteria': 19, **extra}
        with pytest.raises(CommerceError):
            admit_quote_work(store, 'project-a', quote['id'], **values)
    offered = create_quote(store, 'project-a', body(idempotency_key='expired'), operator_id='operator')
    raw = store.one('SELECT payload_json FROM pilot_quotes WHERE id=?', [offered['id']])
    payload = json.loads(raw['payload_json'])
    payload['expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    store.execute('UPDATE pilot_quotes SET payload_json=? WHERE id=?', [json.dumps(payload), offered['id']])
    with pytest.raises(CommerceError, match='expired'):
        accept_quote(store, 'project-a', offered['id'], actor_id='owner', scope_sha256=offered['scope_sha256'])


def test_optional_local_work_does_not_silently_become_paid(store, monkeypatch):
    args = {'profile_id': 'company', 'documents': 1, 'characters': 100, 'criteria': 1}
    assert admit_quote_work(store, 'project-a', None, **args) is None
    monkeypatch.setenv('DOXA_REQUIRE_ACCEPTED_QUOTE', 'true')
    with pytest.raises(CommerceError, match='requires an accepted'):
        admit_quote_work(store, 'project-a', None, **args)


def test_atomic_cap_and_redelivery_prevent_duplicate_reservations(store):
    quote = accepted(store)
    def reserve(key):
        try:
            return reserve_usage(store, 'project-a', quote['id'], event_key=key, category='inference', reserved_microusd=60_000)
        except CommerceError as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ['request-1', 'request-2']))
    assert sum(isinstance(item, CommerceError) for item in results) == 1
    winner = next(item for item in results if not isinstance(item, CommerceError))
    repeated = reserve(winner['event_key'])
    assert repeated['duplicate'] and repeated['id'] == winner['id']
    assert usage_summary(store, 'project-a', quote['id'])['reserved_microusd'] == 60_000
    with pytest.raises(CommerceError, match='different delivery'):
        reserve_usage(store, 'project-a', quote['id'], event_key=winner['event_key'], category='inference', reserved_microusd=1)


def test_real_cost_is_not_erased_when_a_vendor_exceeds_estimate(store):
    quote = accepted(store)
    reserve_usage(store, 'project-a', quote['id'], event_key='request', category='inference', reserved_microusd=90_000)
    settled = settle_usage(store, 'project-a', 'request', actual_microusd=110_000)
    assert settled['actual_microusd'] == 110_000
    assert get_quote(store, 'project-a', quote['id'])['status'] == 'cap_exceeded'
    summary = usage_summary(store, 'project-a')
    assert summary['actual_microusd'] == 110_000 and summary['reserved_microusd'] == 0
    assert not summary['customer_charge'] and not summary['provider_invoice']
    assert settle_usage(store, 'project-a', 'request', actual_microusd=110_000)['duplicate']
    with pytest.raises(CommerceError, match='immutable'):
        settle_usage(store, 'project-a', 'request', actual_microusd=0)
    with pytest.raises(CommerceError, match='accepted quote'):
        reserve_usage(store, 'project-a', quote['id'], event_key='further', category='export', reserved_microusd=0)


def test_all_delivery_categories_retained_separately(store):
    quote = accepted(store)
    for category in ('inference', 'ocr', 'compute', 'storage', 'export', 'review', 'support'):
        reserve_usage(store, 'project-a', quote['id'], event_key=category, category=category,
                      reserved_microusd=1, quantity=2, reference_id='task-1')
        settle_usage(store, 'project-a', category, actual_microusd=1)
    summary = usage_summary(store, 'project-a')
    assert len(summary['categories']) == 7 and summary['actual_microusd'] == 7
    assert all(row['quantity'] == 2 for row in summary['categories'])
    with pytest.raises(CommerceError):
        reserve_usage(store, 'project-a', quote['id'], event_key='bad', category='unknown', reserved_microusd=1)


def test_http_principal_and_operator_authority_are_server_owned(store, monkeypatch):
    app = FastAPI()
    app.state.test_principal = LOCAL_OPERATOR
    @app.middleware('http')
    async def trusted_test_identity(request: Request, call_next):
        request.state.doxa_principal = app.state.test_principal
        return await call_next(request)
    register_commerce_api(app, lambda: store)
    path = '/api/pilot/projects/project-a'
    with TestClient(app) as client:
        issued = client.post(path + '/quotes', json=body())
        assert issued.status_code == 201
        quote = issued.json()
        forged = client.post(path + f"/quotes/{quote['id']}/accept", json={'scope_sha256': quote['scope_sha256'], 'accepted_by': 'forged'})
        assert forged.status_code == 422
        app.state.test_principal = Principal('viewer', 'viewer', ('project-a',), False, ROLE_PERMISSIONS['viewer'], True)
        assert client.get(path + '/quotes').status_code == 200
        assert client.post(path + f"/quotes/{quote['id']}/accept", json={'scope_sha256': quote['scope_sha256']}).status_code == 403
        app.state.test_principal = replace(app.state.test_principal, id='customer-owner', role='owner', permissions=ROLE_PERMISSIONS['owner'])
        assert client.post(path + '/quotes', json=body(idempotency_key='browser-price')).status_code == 403
        accepted_response = client.post(path + f"/quotes/{quote['id']}/accept", json={'scope_sha256': quote['scope_sha256']})
        assert accepted_response.status_code == 200
        assert accepted_response.json()['accepted_by'] == 'customer-owner'
        monkeypatch.setenv('DOXA_COMMERCE_OPERATOR_IDS', 'service-operator')
        app.state.test_principal = replace(app.state.test_principal, id='service-operator')
        assert client.post(path + '/quotes', json=body(idempotency_key='operator-price')).status_code == 201
        assert client.post(path + '/usage', json={'actual_microusd': 0}).status_code == 405


def test_provider_ledger_and_contract_reservations_commit_together(tmp_path, monkeypatch):
    configured_pricing(tmp_path, monkeypatch)
    from doxa.api import create_app
    from test_api import BudgetProvider, finish, project, start, upload
    monkeypatch.delenv('DOXA_ACCESS_MODE', raising=False)
    monkeypatch.delenv('DOXA_ACCESS_CONFIG_FILE', raising=False)
    monkeypatch.delenv('DOXA_REQUIRE_ACCEPTED_QUOTE', raising=False)
    monkeypatch.delenv('DOXA_CLOUD_STATE_URL', raising=False)
    provider = BudgetProvider()
    monkeypatch.setattr('doxa.providers.get_provider', lambda _: provider)
    with TestClient(create_app(tmp_path, start_workers=False, use_saved_showcase=False)) as client:
        identity = project(client)
        upload(client, identity)
        store = client.app.state.store
        quote = create_quote(store, identity, body(processing_cap_microusd=150_000, profile_id='public'), operator_id='operator')
        accept_quote(store, identity, quote['id'], actor_id='customer', scope_sha256=quote['scope_sha256'])
        runs = [start(client, identity, provider='openai', model='job-budget-test', quote_id=quote['id'], lens_ids=['ihra'], budget_usd=1) for _ in range(2)]
        for run in runs:
            config = dict(run['config'], quote_id=quote['id'])
            store.execute('UPDATE runs SET config_json=? WHERE id=?', [json.dumps(config), run['id']])
        assert finish(client, runs[0]['id'])['status'] == 'completed'
        assert finish(client, runs[1]['id'])['status'] == 'paused_budget'
        assert provider.calls == 1
        summary = usage_summary(store, identity, quote['id'])
        assert summary['actual_microusd'] == 100_000 and summary['reserved_microusd'] == 0
        assert summary['admitted_unique_source_versions'] == 1
        rows = store.query('SELECT * FROM budget_reservations')
        assert len(rows) == 1 and rows[0]['status'] == 'settled'
        assert store.one('SELECT COUNT(*) n FROM pilot_usage_events')['n'] == 1
        assert client.get('/api/settings').json()['budget']['reserved_usd'] == 0


def test_unknown_provider_usage_is_conservatively_charged_to_contract(tmp_path, monkeypatch):
    configured_pricing(tmp_path, monkeypatch)
    from doxa.api import create_app
    from test_api import BudgetProvider, finish, project, start, upload
    class UnknownUsageProvider(BudgetProvider):
        async def generate(self, *args, **kwargs):
            response = await super().generate(*args, **kwargs)
            response['usage']['cost_usd'] = None
            return response
    for key in ('DOXA_ACCESS_MODE', 'DOXA_ACCESS_CONFIG_FILE', 'DOXA_REQUIRE_ACCEPTED_QUOTE', 'DOXA_CLOUD_STATE_URL'):
        monkeypatch.delenv(key, raising=False)
    provider = UnknownUsageProvider()
    monkeypatch.setattr('doxa.providers.get_provider', lambda _: provider)
    with TestClient(create_app(tmp_path, start_workers=False, use_saved_showcase=False)) as client:
        identity = project(client)
        upload(client, identity)
        store = client.app.state.store
        quote = create_quote(store, identity, body(processing_cap_microusd=200_000, profile_id='public'), operator_id='operator')
        accept_quote(store, identity, quote['id'], actor_id='customer', scope_sha256=quote['scope_sha256'])
        run = start(client, identity, provider='openai', model='job-budget-test', quote_id=quote['id'], lens_ids=['ihra'], budget_usd=1)
        store.execute('UPDATE runs SET config_json=? WHERE id=?', [json.dumps(dict(run['config'], quote_id=quote['id'])), run['id']])
        assert finish(client, run['id'])['status'] == 'completed'
        event = store.one('SELECT * FROM pilot_usage_events')
        assert event['actual_microusd'] == 100_000 and event['state'] == 'settled'
        reservation = store.one('SELECT * FROM budget_reservations')
        assert json.loads(reservation['usage_json'])['cost_usd'] is None


def test_erasure_barrier_blocks_every_contract_mutation(store):
    from doxa.privacy_lifecycle import create_erase_job
    quote = accepted(store)
    reserve_usage(store, 'project-a', quote['id'], event_key='before-erasure', category='export', reserved_microusd=1)
    create_erase_job(store, 'project-a', 'operator', 'erase-contract-project')
    for mutate in (
        lambda: create_quote(store, 'project-a', body(idempotency_key='too-late'), operator_id='operator'),
        lambda: accept_quote(store, 'project-a', quote['id'], actor_id='owner', scope_sha256=quote['scope_sha256']),
        lambda: reserve_usage(store, 'project-a', quote['id'], event_key='too-late', category='export', reserved_microusd=1),
        lambda: settle_usage(store, 'project-a', 'before-erasure', actual_microusd=1),
    ):
        with pytest.raises(CommerceError, match='erasure') as error:
            mutate()
        assert error.value.status_code == 409
    assert store.one("SELECT COUNT(*) n FROM pilot_usage_events WHERE project_id='project-a'")['n'] == 1
    assert store.one("SELECT state FROM pilot_usage_events WHERE event_key='before-erasure'")['state'] == 'reserved'


def test_erasure_between_quote_preflight_and_write_cannot_orphan_private_records(store, monkeypatch):
    from doxa import pilot_commerce
    from doxa.privacy_lifecycle import create_erase_job
    original = pilot_commerce._project
    calls = []
    def raced_project(selected_store, identity):
        original(selected_store, identity)
        calls.append(identity)
        if len(calls) == 1:
            create_erase_job(selected_store, identity, 'operator', 'racing-erase-contract')
    monkeypatch.setattr(pilot_commerce, '_project', raced_project)
    with pytest.raises(CommerceError, match='erasure'):
        create_quote(store, 'project-a', body(), operator_id='operator')
    assert store.one("SELECT COUNT(*) n FROM pilot_quotes WHERE project_id='project-a'")['n'] == 0


def configured_pricing(tmp_path, monkeypatch, *, input_rate=1, output_rate=2):
    path = tmp_path / 'pricing.json'
    value = {'version': 1, 'revision': 'synthetic-test-only-1', 'rates': [{
        'provider': 'openai', 'model': 'job-budget-test', 'endpoint': 'https://api.openai.com/v1',
        'input_price_per_million': input_rate, 'output_price_per_million': output_rate}]}
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    monkeypatch.setenv('DOXA_COMMERCE_RATES_FILE', str(path))
    monkeypatch.setenv('DOXA_OPENAI_BASE_URL', 'https://api.openai.com/v1')
    monkeypatch.delenv('DOXA_OPENAI_MODE', raising=False)
    return path


def test_quoted_pricing_is_server_owned_and_frozen_for_worker(tmp_path, monkeypatch):
    from doxa.pilot_commerce import apply_server_pricing
    path = configured_pricing(tmp_path, monkeypatch)
    config = {'provider': 'openai', 'model': 'job-budget-test', 'quote_id': 'quote-1'}
    frozen = apply_server_pricing(config, submitted_fields={'provider', 'model', 'quote_id'})
    assert 'input_price_per_million' not in config
    assert frozen['input_price_per_million'] == 1 and frozen['output_price_per_million'] == 2
    assert frozen['commercial_pricing']['kind'] == 'server_token_rates'
    assert frozen['inference_mode'] == 'remote'
    assert apply_server_pricing(frozen)['input_price_per_million'] == 1
    changed = json.loads(path.read_text())
    changed['rates'][0]['input_price_per_million'] = 3
    path.write_text(json.dumps(changed))
    with pytest.raises(CommerceError, match='prices changed'):
        apply_server_pricing(frozen)
    for field in ('input_price_per_million', 'output_price_per_million'):
        with pytest.raises(CommerceError, match='server-configured'):
            apply_server_pricing(dict(config, **{field: 0}), submitted_fields={field})
    assert apply_server_pricing({'provider': 'openai', 'input_price_per_million': 0})['input_price_per_million'] == 0


def test_quoted_pricing_rejects_untrusted_files_and_unknown_destinations(tmp_path, monkeypatch):
    from doxa.pilot_commerce import apply_server_pricing
    path = configured_pricing(tmp_path, monkeypatch)
    config = {'provider': 'openai', 'model': 'job-budget-test', 'quote_id': 'quote-1'}
    path.chmod(0o644)
    with pytest.raises(CommerceError, match='private server pricing'):
        apply_server_pricing(config, submitted_fields=set())
    path.chmod(0o600)
    for changed in ({'model': 'another-model'}, {'base_url': 'https://different.example/v1'}):
        with pytest.raises(CommerceError, match='private server pricing'):
            apply_server_pricing(dict(config, **changed), submitted_fields=set())
    with pytest.raises(CommerceError, match='metered compatible'):
        apply_server_pricing({'provider': 'ollama', 'base_url': 'https://remote.example', 'quote_id': 'quote-1'}, submitted_fields=set())
    with pytest.raises(CommerceError, match='HTTPS'):
        apply_server_pricing(dict(config, base_url='http://127.0.0.1:9999/v1'), submitted_fields=set())
    monkeypatch.setenv('DOXA_OPENAI_MODE', 'local')
    local = apply_server_pricing(dict(config, base_url='http://127.0.0.1:9999/v1'), submitted_fields=set())
    assert local['commercial_pricing']['kind'] == 'local_compute_excluded'
    assert local['input_price_per_million'] == 0


def test_browser_cannot_make_paid_analysis_free_before_health_or_requests(tmp_path, monkeypatch):
    from doxa.api import create_app
    from test_api import project, upload
    configured_pricing(tmp_path, monkeypatch)
    for key in ('DOXA_ACCESS_MODE', 'DOXA_ACCESS_CONFIG_FILE', 'DOXA_CLOUD_STATE_URL', 'DOXA_REQUIRE_ACCEPTED_QUOTE'):
        monkeypatch.delenv(key, raising=False)
    with TestClient(create_app(tmp_path / 'workspace', start_workers=False, use_saved_showcase=False)) as client:
        identity = project(client)
        upload(client, identity)
        quote = create_quote(client.app.state.store, identity, body(profile_id='public'), operator_id='operator')
        accept_quote(client.app.state.store, identity, quote['id'], actor_id='customer', scope_sha256=quote['scope_sha256'])
        outbound = []
        def forbidden_provider(config):
            outbound.append(config)
            raise AssertionError('Provider must not be constructed for rejected client pricing')
        monkeypatch.setattr('doxa.providers.get_provider', forbidden_provider)
        response = client.post(f'/api/projects/{identity}/runs', json={
            'provider': 'openai', 'model': 'job-budget-test', 'quote_id': quote['id'],
            'input_price_per_million': 0, 'output_price_per_million': 0, 'lens_ids': ['ihra']})
        assert response.status_code == 422 and 'server-configured' in response.text
        assert outbound == []
        assert client.app.state.store.one('SELECT COUNT(*) n FROM budget_reservations')['n'] == 0


def test_server_price_change_pauses_worker_before_provider_creation(tmp_path, monkeypatch):
    from doxa.api import create_app
    from test_api import BudgetProvider, finish, project, start, upload
    path = configured_pricing(tmp_path, monkeypatch)
    for key in ('DOXA_ACCESS_MODE', 'DOXA_ACCESS_CONFIG_FILE', 'DOXA_CLOUD_STATE_URL', 'DOXA_REQUIRE_ACCEPTED_QUOTE'):
        monkeypatch.delenv(key, raising=False)
    provider = BudgetProvider()
    monkeypatch.setattr('doxa.providers.get_provider', lambda _: provider)
    with TestClient(create_app(tmp_path / 'workspace', start_workers=False, use_saved_showcase=False)) as client:
        identity = project(client)
        upload(client, identity)
        quote = create_quote(client.app.state.store, identity, body(profile_id='public'), operator_id='operator')
        accept_quote(client.app.state.store, identity, quote['id'], actor_id='customer', scope_sha256=quote['scope_sha256'])
        run = start(client, identity, provider='openai', model='job-budget-test', quote_id=quote['id'], lens_ids=['ihra'])
        updated = json.loads(path.read_text())
        updated['rates'][0]['input_price_per_million'] = 3
        path.write_text(json.dumps(updated))
        assert finish(client, run['id'])['status'] == 'paused_budget'
        assert provider.calls == 0
        assert client.app.state.store.one('SELECT COUNT(*) n FROM budget_reservations')['n'] == 0


def test_revoked_quote_blocks_future_work_without_erasing_known_costs(store):
    from doxa.pilot_commerce import revoke_quote
    quote = accepted(store)
    reserve_usage(store, 'project-a', quote['id'], event_key='already-sent', category='inference', reserved_microusd=1)
    revoked = revoke_quote(store, 'project-a', quote['id'], actor_id='customer-owner', reason_code='customer_cancelled')
    assert revoked['status'] == 'revoked' and revoked['revoked_by'] == 'customer-owner'
    assert revoke_quote(store, 'project-a', quote['id'], actor_id='operator', reason_code='security_review')['duplicate']
    with pytest.raises(CommerceError, match='accepted quote'):
        reserve_usage(store, 'project-a', quote['id'], event_key='future', category='inference', reserved_microusd=1)
    assert settle_usage(store, 'project-a', 'already-sent', actual_microusd=2)['state'] == 'settled'
    assert get_quote(store, 'project-a', quote['id'])['status'] == 'revoked'
    assert usage_summary(store, 'project-a')['actual_microusd'] == 2
