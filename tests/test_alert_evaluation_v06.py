"""Prospective scheduling tests with synthetic counts and no model inference."""
from copy import deepcopy
import json

import pytest

from doxa import alert_evaluation as alerts
from test_evaluation_v04 import reviewed_store  # noqa: F401


def monthly():
    rows = []
    for index in range(13):
        year, month = 2025 + (10 + index) // 12, (10 + index) % 12 + 1
        rows.append({'date': f'{year:04d}-{month:02d}', 'positive_documents': 1 if index < 12 else 30,
                     'eligible_documents': 100, 'coverage': 100, 'label_coverage': 100, 'unknown_fraction': 0,
                     'composition': {'SyntheticSource|news|short': 100}})
    return rows


def fake_trajectory(*args, **kwargs):
    return {'monthly': monthly(), 'source_snapshot_available': True, 'spike_candidates': [],
            'spike_policy': {}, 'interpretation_accuracy': None}


def specification():
    return {'run_id': 'r', 'lens_id': 'ihra', 'criterion_id': 'anti_jewish_generalization',
            'start_at': '2026-11-19T00:00:00Z', 'end_at': '2027-03-01T00:00:00Z',
            'look_times': ['2026-12-01T00:00:00Z', '2027-01-01T00:00:00Z'],
            'review_question': 'Synthetic test-only alert utility under the registered source scope.',
            'classification_scenarios': [{'label': 'Hypothetical only', 'positive_reclassification_fraction': 0.1,
                                          'negative_reclassification_fraction': 0.02}]}


@pytest.fixture
def campaign(reviewed_store, monkeypatch):  # noqa: F811
    monkeypatch.setattr(alerts, 'utcnow', lambda: '2026-11-18T12:00:00+00:00')
    monkeypatch.setattr(alerts, 'monthly_trajectories', fake_trajectory)
    created = alerts.create_campaign(reviewed_store, 'p', specification(), actor_id='synthetic-operator')
    return reviewed_store, created


def test_registration_freezes_baseline_and_remains_unmeasured(campaign):
    store, created = campaign
    assert created['sequential_policy']['per_look_alpha'] == 0.005
    assert created['baseline_role'].startswith('Historical baseline only')
    assert alerts.create_campaign(store, 'p', specification(), actor_id='synthetic-operator')['idempotent']
    report = alerts.campaign_report(store, 'p', created['id'])
    assert report['status'] == 'not_measured'
    assert report['operator_assessed_relevance_fraction'] is None
    assert report['unique_prospective_alerts'] == 0
    assert report['independently_validated_alert_quality'] is None


def test_no_backdated_or_unregistered_look_can_be_captured(campaign, monkeypatch):
    store, created = campaign
    with pytest.raises(ValueError, match='not open'):
        alerts.capture_look(store, 'p', created['id'], run_id='r', look_index=0)
    with pytest.raises(ValueError, match='look index'):
        alerts.capture_look(store, 'p', created['id'], run_id='r', look_index=3)
    past = specification()
    past['start_at'] = '2026-10-01T00:00:00Z'
    with pytest.raises(ValueError, match='cannot be registered after'):
        alerts.create_campaign(store, 'p', past, actor_id='synthetic-operator')


def test_capture_reuses_statistics_freezes_results_and_labels_sensitivity(campaign, monkeypatch):
    store, created = campaign
    monkeypatch.setattr(alerts, 'utcnow', lambda: '2026-12-01T12:00:00+00:00')
    first = alerts.capture_look(store, 'p', created['id'], run_id='r', look_index=0)
    assert len(first['prospective_candidates']) == 1
    assert first['prospective_candidates'][0]['date'] == '2026-11'
    assert first['detection']['policy']['alpha'] == 0.005
    assert first['sensitivity']['sensitivity_only']
    assert not first['sensitivity']['calibrated_classification_error']
    assert first['sensitivity']['scenarios'][0]['monthly'][-1]['assumed_reclassified_rate'] == pytest.approx(0.284)
    assert alerts.capture_look(store, 'p', created['id'], run_id='r', look_index=0)['idempotent']
    monkeypatch.setattr(alerts, 'utcnow', lambda: '2027-01-01T12:00:00+00:00')
    alerts.capture_look(store, 'p', created['id'], run_id='r', look_index=1)
    # Repeated snapshots of one event month are not independent new alerts.
    assert alerts.campaign_report(store, 'p', created['id'])['unique_prospective_alerts'] == 1


def test_unknown_alerts_cannot_be_reviewed_and_real_records_retain_disagreement(campaign, monkeypatch):
    store, created = campaign
    review = {'alert_id': 'fabricated', 'relevance': 'relevant', 'context_sufficient': True, 'note': 'Synthetic test reading.'}
    with pytest.raises(ValueError, match='server-generated'):
        alerts.review_alert(store, 'p', created['id'], review, reviewer_id='synthetic-A')
    monkeypatch.setattr(alerts, 'utcnow', lambda: '2026-12-01T12:00:00+00:00')
    captured = alerts.capture_look(store, 'p', created['id'], run_id='r', look_index=0)
    review['alert_id'] = captured['prospective_candidates'][0]['alert_id']
    first = alerts.review_alert(store, 'p', created['id'], review, reviewer_id='synthetic-A')
    assert not first['reviewer_qualifications_independently_verified']
    assert alerts.review_alert(store, 'p', created['id'], review, reviewer_id='synthetic-A')['idempotent']
    with pytest.raises(ValueError, match='immutable'):
        alerts.review_alert(store, 'p', created['id'], {**review, 'note': 'Changed interpretation.'}, reviewer_id='synthetic-A')
    alerts.review_alert(store, 'p', created['id'], {**review, 'relevance': 'not_relevant'}, reviewer_id='synthetic-B')
    report = alerts.campaign_report(store, 'p', created['id'])
    assert report['readings'] == {'disagreement': 1}
    assert report['operator_assessed_relevance_fraction'] is None
    assert report['review_coverage'] == 1
    assert report['independently_validated_alert_quality'] is None


def test_changed_method_or_model_requires_separate_campaign(campaign, monkeypatch):
    store, created = campaign
    monkeypatch.setattr(alerts, 'utcnow', lambda: '2026-12-01T12:00:00+00:00')
    config = json.loads(store.one("SELECT config_json FROM runs WHERE id='r'")['config_json'])
    store.execute("UPDATE runs SET config_json=? WHERE id='r'", [json.dumps({**config, 'question': 'A new analytical question'})])
    with pytest.raises(ValueError, match='changed the registered'):
        alerts.capture_look(store, 'p', created['id'], run_id='r', look_index=0)
    assert store.one('SELECT COUNT(*) AS n FROM v06_alert_snapshots')['n'] == 0


def test_invalid_sensitivity_scenarios_are_not_a_calibrated_correction(campaign):
    store, _ = campaign
    body = deepcopy(specification())
    body['classification_scenarios'][0]['positive_reclassification_fraction'] = True
    with pytest.raises(ValueError, match='fractions'):
        alerts.create_campaign(store, 'p', body, actor_id='synthetic-operator')
