"""Deployment contracts and ledger transport, without touching any cloud account."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / 'infra/gcp'
spec = importlib.util.spec_from_file_location('doxa_gcp_cloud_store', INFRA / 'cloud_store.py')
cloud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud)


class MemoryObjects:
    def __init__(self):
        self.objects = {}
    def list(self, bucket, prefix):
        return [{'name': name, 'size': len(raw)} for (stored_bucket, name), raw in self.objects.items()
                if stored_bucket == bucket and name.startswith(prefix)]
    def read(self, bucket, name):
        return self.objects[(bucket, name)]
    def write_once(self, bucket, name, raw):
        key = (bucket, name)
        if key in self.objects:
            return False
        self.objects[key] = raw
        return True


def ledger(count, *, identity='installation-001', change=None):
    events = [{'sequence': number, 'kind': 'fixture-event'} for number in range(1, count + 1)]
    if change is not None:
        events[0] = change
    # Transport does not establish HMAC validity; application-level tests verify
    # real signatures. This fixture only exercises immutable cloud publication.
    return json.dumps({'payload': {'schema_version': 'doxa-deletion-ledger-1',
            'installation_id': identity, 'sequence': count, 'events': events},
            'signature': 'a' * 64}, sort_keys=True, separators=(',', ':')).encode() + b'\n'


def test_remote_ledger_retries_are_exact_and_cross_installation_isolated():
    storage = MemoryObjects()
    for number in range(3):
        result = cloud.publish_ledger(ledger(number), storage, 'private-control', 'installation-001')
        assert result['sequence'] == number and not result['duplicate']
        assert cloud.publish_ledger(ledger(number), storage, 'private-control', 'installation-001')['duplicate']
    assert len(storage.objects) == 3
    assert cloud.latest(storage, 'private-control', 'installation-001')[1]['sequence'] == 2
    assert cloud.latest(storage, 'private-control', 'different-installation') is None
    with pytest.raises(cloud.CloudStoreError, match='identity'):
        cloud.publish_ledger(ledger(2), storage, 'private-control', 'different-installation')


def test_remote_ledger_rejects_rollback_forks_and_sequence_gaps():
    storage = MemoryObjects()
    with pytest.raises(cloud.CloudStoreError, match='sequence zero'):
        cloud.publish_ledger(ledger(1), storage, 'private-control', 'installation-001')
    cloud.publish_ledger(ledger(0), storage, 'private-control', 'installation-001')
    with pytest.raises(cloud.CloudStoreError, match='without gaps'):
        cloud.publish_ledger(ledger(2), storage, 'private-control', 'installation-001')
    cloud.publish_ledger(ledger(1), storage, 'private-control', 'installation-001')
    with pytest.raises(cloud.CloudStoreError, match='rollback'):
        cloud.publish_ledger(ledger(0), storage, 'private-control', 'installation-001')
    with pytest.raises(cloud.CloudStoreError, match='history conflict'):
        cloud.publish_ledger(ledger(2, change={'sequence': 1, 'kind': 'rewritten'}), storage, 'private-control', 'installation-001')
    changed = json.loads(ledger(1))
    changed['signature'] = 'b' * 64
    with pytest.raises(cloud.CloudStoreError, match='Same-sequence'):
        cloud.publish_ledger(json.dumps(changed).encode(), storage, 'private-control', 'installation-001')


def test_concurrent_cloud_collision_cannot_be_acknowledged_as_success():
    class ConflictingStore(MemoryObjects):
        def write_once(self, bucket, name, raw):
            if name.endswith('00000000000000000001.json'):
                conflicting = json.loads(raw)
                conflicting['signature'] = 'b' * 64
                self.objects[(bucket, name)] = json.dumps(conflicting).encode()
                return False
            return super().write_once(bucket, name, raw)
    storage = ConflictingStore()
    cloud.publish_ledger(ledger(0), storage, 'private-control', 'installation-001')
    with pytest.raises(cloud.CloudStoreError, match='Concurrent same-sequence'):
        cloud.publish_ledger(ledger(1), storage, 'private-control', 'installation-001')


def test_failed_remote_write_or_verification_never_returns_success():
    class UnavailableStore(MemoryObjects):
        def write_once(self, *args):
            raise OSError('simulated cloud outage')
    with pytest.raises(OSError):
        cloud.publish_ledger(ledger(0), UnavailableStore(), 'private-control', 'installation-001')
    class CorruptStore(MemoryObjects):
        def write_once(self, bucket, name, raw):
            self.objects[(bucket, name)] = b'corrupt'
            return True
    with pytest.raises(cloud.CloudStoreError, match='bytes did not verify'):
        cloud.publish_ledger(ledger(0), CorruptStore(), 'private-control', 'installation-001')


@pytest.mark.parametrize('raw', [b'not-json', b'[]', b'{}', b'x' * (cloud.MAX_LEDGER + 1)])
def test_invalid_cloud_ledger_fails_closed(raw):
    with pytest.raises(cloud.CloudStoreError):
        cloud.publish_ledger(raw, MemoryObjects(), 'private-control', 'installation-001')


def test_private_bootstrap_write_has_private_permissions_and_no_secret_stdout(tmp_path, capsys):
    path = tmp_path / 'private' / 'ledger.json'
    cloud.private_write(path, ledger(0))
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.read_bytes() == ledger(0)
    assert capsys.readouterr().out == ''


def test_deployment_recipe_contains_no_application_origin_port_or_secret_payload():
    main = (INFRA / 'main.tf').read_text()
    startup = (INFRA / 'startup.sh.tftpl').read_text()
    assert 'secret_data' not in main and 'google_service_account_key' not in main
    assert 'public_access_prevention' in main and '"enforced"' in main
    assert 'roles/storage.objectAdmin' not in main
    assert '"35.235.240.0/20"' in main and '"22"' in main
    assert 'ports    = ["80", "443"]' in main
    assert "ports: ['80:80', '443:443']" in startup
    assert '8000:8000' not in startup
    assert "'--forwarded-allow-ips', '172.30.0.2'" in startup
    assert "'--no-access-log'" in startup
    assert 'DOXA_DELETION_LEDGER_PUBLISH_ARGV' in startup
    assert "DOXA_PUBLIC_ORIGIN: 'https://${domain}'" in startup
    assert startup.index('restore-check --data-dir /data') < startup.index('systemctl enable --now doxa-pilot.service')
    assert (INFRA / '.terraform.lock.hcl').is_file()


def test_operator_scripts_parse_and_restore_guard_precedes_traffic():
    for name in ('backup.sh', 'restore.sh', 'startup.sh.tftpl'):
        subprocess.run(['bash', '-n', str(INFRA / name)], check=True, capture_output=True)
    restore = (INFRA / 'restore.sh').read_text()
    assert restore.index('latest-ledger') < restore.index('backup-download') < restore.index('restore-check') < restore.index('up -d app')
    assert "member.isdir() or member.isreg()" in restore and "'..' in relative.parts" in restore
    backup = (INFRA / 'backup.sh').read_text()
    assert 'stop app' in backup and '--hard-dereference' in backup
    assert 'Content-Range' in (INFRA / 'cloud_store.py').read_text()


def secrets_module():
    spec = importlib.util.spec_from_file_location('doxa_gcp_prepare_secrets', INFRA / 'prepare_secrets.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def provisioned_inputs(tmp_path):
    import base64
    from doxa.access import issue_token, token_hash
    source = tmp_path / 'private-source'
    source.mkdir(mode=0o700)
    config = {'version': 1, 'session_secret': 'synthetic-private-session-secret-long-enough', 'users': [
        {'id': 'operator', 'role': 'owner', 'all_projects': True, 'token_sha256': token_hash(issue_token())}]}
    (source / 'access.json').write_text(json.dumps(config))
    (source / 'access.json').chmod(0o600)
    vault = base64.b64encode(b'v' * 32).decode()
    (source / 'pilot.env').write_text('DOXA_PRIVACY_VAULT_KEY=' + vault + '\nDOXA_PILOT_WORKSPACE_KIND=organization\n')
    (source / 'pilot.env').chmod(0o600)
    pricing = tmp_path / 'private-pricing.json'
    pricing.write_bytes((INFRA / 'pricing.example.json').read_bytes())
    pricing.chmod(0o600)
    return source, pricing, vault


def scope_private_fixture(module, tmp_path, monkeypatch):
    original = module._inside_checkout
    # The managed harness checkpoints /tmp itself in Git. Scope only temporary
    # fixture ancestors; keep real source-checkout rejection intact elsewhere.
    def fixture_checkout(path):
        if not path.is_relative_to(tmp_path):
            return original(path)
        return any((parent / '.git').exists() for parent in (path, *path.parents)
                   if parent == tmp_path or parent.is_relative_to(tmp_path))
    monkeypatch.setattr(module, '_inside_checkout', fixture_checkout)


def test_hosted_secret_preparation_preserves_vault_and_never_copies_plain_credentials(tmp_path, capsys, monkeypatch):
    from dotenv import dotenv_values
    module = secrets_module()
    scope_private_fixture(module, tmp_path, monkeypatch)
    source, pricing, vault = provisioned_inputs(tmp_path)
    (source / 'credentials.json').write_text('{"private":"MUST NEVER BE COPIED"}')
    output = tmp_path / 'new-hosted-private'
    result = module.prepare(source, output, pricing, ['operator'], new_installation=True)
    assert result['cloud_resources_created'] is False and not result['credentials_printed']
    assert {path.name for path in output.iterdir()} == {'access.json', 'pilot.env', 'pricing.json'}
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir())
    env = dotenv_values(output / 'pilot.env')
    assert env['DOXA_PRIVACY_VAULT_KEY'] == vault
    assert env['DOXA_REQUIRE_ACCEPTED_QUOTE'] == 'true'
    assert env['DOXA_COMMERCE_OPERATOR_IDS'] == 'operator'
    assert len(env['DOXA_INSTALLATION_ID']) == 32
    assert 'MUST NEVER BE COPIED' not in (output / 'access.json').read_text()
    assert capsys.readouterr().out == ''
    with pytest.raises(ValueError, match='never overwritten'):
        module.prepare(source, output, pricing, ['operator'], new_installation=True)
    preserved = tmp_path / 'retained-hosted-private'
    module.prepare(output, preserved, pricing, ['operator'])
    next_env = dotenv_values(preserved / 'pilot.env')
    assert next_env['DOXA_INSTALLATION_ID'] == env['DOXA_INSTALLATION_ID']
    assert next_env['DOXA_DELETION_LEDGER_KEY'] == env['DOXA_DELETION_LEDGER_KEY']


def test_hosted_secret_preparation_requires_explicit_newness_and_valid_admin(tmp_path, monkeypatch):
    module = secrets_module()
    scope_private_fixture(module, tmp_path, monkeypatch)
    source, pricing, _ = provisioned_inputs(tmp_path)
    with pytest.raises(ValueError, match='explicit --new-installation'):
        module.prepare(source, tmp_path / 'missing-confirmation', pricing, ['operator'])
    with pytest.raises(ValueError, match='administrator identities'):
        module.prepare(source, tmp_path / 'missing-admin', pricing, ['browser-claimed-owner'], new_installation=True)
    with pytest.raises(ValueError, match='outside source checkouts'):
        module.prepare(source, ROOT / 'unsafe-secrets-fixture', pricing, ['operator'], new_installation=True)
    assert not (ROOT / 'unsafe-secrets-fixture').exists()
    pricing.chmod(0o644)
    with pytest.raises(ValueError, match='private'):
        module.prepare(source, tmp_path / 'unsafe-prices', pricing, ['operator'], new_installation=True)


def test_restore_merge_preserves_unpublished_newer_local_intent(tmp_path):
    storage = MemoryObjects()
    cloud.publish_ledger(ledger(0), storage, 'private-control', 'installation-001')
    local = tmp_path / 'private' / 'ledger.json'
    cloud.private_write(local, ledger(1))
    assert cloud.merge_ledger(ledger(0), local, 'installation-001') == 'kept_newer_local'
    assert local.read_bytes() == ledger(1)
    assert cloud.publish_ledger(local.read_bytes(), storage, 'private-control', 'installation-001')['sequence'] == 1
    assert cloud.latest(storage, 'private-control', 'installation-001')[0] == ledger(1)
    assert cloud.merge_ledger(ledger(2), local, 'installation-001') == 'recovered_newer_remote'
    assert local.read_bytes() == ledger(2)
    with pytest.raises(cloud.CloudStoreError, match='histories conflict'):
        cloud.merge_ledger(ledger(2, change={'sequence': 1, 'kind': 'different'}), local, 'installation-001')
    assert local.read_bytes() == ledger(2)
