"""Scoped panel import uses synthetic fixtures, never real benchmark labels."""
import json

import pytest

from doxa import evaluation
from doxa.reference_panels import assign_splits, import_reference_panel, validate_manifest
from test_evaluation_v04 import labels, reviewed_store  # noqa: F401


def manifest_for(store):
    cases = []
    for index, row in enumerate(store.query('SELECT document_id,unit_id,lens_id,criterion_id FROM v3_assessments ORDER BY id')):
        cases.append({**row, 'primary_cluster_id': 'story-' + str(index), 'split_group_id': 'group-' + str(max(0, index - 1)),
                      'inference_cluster_id': 'story-' + str(index), 'inclusion_probability': 0.1, 'rights_record_sha256': 'b' * 64})
    return {'profile_id': 'public', 'domain': 'Synthetic test-only collection',
            'population_description': 'No real population claims; tests simulate an external collection declaration.',
            'sampling_design': 'probability_sample', 'sampling_frame_sha256': 'a' * 64,
            'inference_cluster_key': 'story_group', 'seed': 20261118, 'cases': cases}


def protocol_for(manifest):
    return {'profile_id': manifest['profile_id'], 'domain': manifest['domain'], 'language': 'en',
            'population_description': manifest['population_description'], 'sampling_design': 'probability_sample',
            'sampling_frame_sha256': manifest['sampling_frame_sha256'], 'reviewer_protocol_sha256': 'c' * 64,
            'confidence_method': 'exact_binomial', 'cluster_key': manifest['inference_cluster_key']}


def test_import_whole_group_splits_blank_references_and_frozen_model_payloads(reviewed_store):  # noqa: F811
    manifest = manifest_for(reviewed_store)
    partitions = assign_splits(validate_manifest(manifest))
    assert partitions == assign_splits({**manifest, 'cases': list(reversed(manifest['cases']))})
    created = import_reference_panel(reviewed_store, 'p', 'r', manifest, actor_id='test-operator')
    assert created['case_count'] == 8
    assert all(created[field] > 0 for field in ('development_count', 'calibration_count', 'heldout_count'))
    assert created['sampling']['representative_population_sample']
    assert not created['sampling']['sampling_design_independently_verified']
    by_group = {}
    for row in reviewed_store.query('SELECT payload_json FROM v4_evaluation_cases WHERE evaluation_id=?', [created['id']]):
        case = json.loads(row['payload_json'])
        assert case['reference_labels'] is None
        by_group.setdefault(case['split_group_id'], set()).add(case['split'])
        original = json.loads(reviewed_store.one('SELECT payload_json FROM v3_assessments WHERE document_id=?', [case['source']['id']])['payload_json'])
        assert original == case['model_assessment']
    assert all(len(splits) == 1 for splits in by_group.values())
    for split in ('development', 'calibration', 'heldout'):
        form = evaluation.export_review_form(reviewed_store, 'p', created['id'], split=split)
        assert 'SECRET_MODEL_EXPLANATION' not in json.dumps(form)
        assert all(case['scored_unit']['sentences'] for case in form['cases'])
    assert import_reference_panel(reviewed_store, 'p', 'r', manifest, actor_id='test-operator')['idempotent']


@pytest.mark.parametrize('mutation', ['reference', 'duplicate_source', 'duplicate_cluster', 'probability', 'unequal_probability', 'rights', 'unknown_source', 'unknown_unit', 'cross_split_cluster'])
def test_panel_import_rejects_forged_or_invalid_collection_without_partial_rows(reviewed_store, mutation):  # noqa: F811
    manifest = manifest_for(reviewed_store)
    if mutation == 'reference':
        manifest['cases'][0]['reference_labels'] = labels()
    elif mutation == 'duplicate_source':
        manifest['cases'][1]['document_id'] = manifest['cases'][0]['document_id']
    elif mutation == 'duplicate_cluster':
        manifest['cases'][1]['primary_cluster_id'] = manifest['cases'][0]['primary_cluster_id']
    elif mutation == 'probability':
        manifest['cases'][0]['inclusion_probability'] = True
    elif mutation == 'unequal_probability':
        manifest['cases'][0]['inclusion_probability'] = 0.5
    elif mutation == 'rights':
        manifest['cases'][0]['rights_record_sha256'] = 'unverified'
    elif mutation == 'unknown_source':
        manifest['cases'][0]['document_id'] = 'outside-project'
    elif mutation == 'unknown_unit':
        manifest['cases'][0]['unit_id'] = 'invented-unit'
    elif mutation == 'cross_split_cluster':
        manifest['cases'][2]['inference_cluster_id'] = manifest['cases'][0]['inference_cluster_id']
    with pytest.raises(ValueError):
        import_reference_panel(reviewed_store, 'p', 'r', manifest, actor_id='test-operator')
    assert reviewed_store.one('SELECT COUNT(*) AS n FROM v4_evaluations')['n'] == 0
    assert reviewed_store.one('SELECT COUNT(*) AS n FROM v4_evaluation_cases')['n'] == 0


def test_run_membership_preserves_unprocessed_cases_in_panel_denominator(reviewed_store):  # noqa: F811
    manifest = manifest_for(reviewed_store)
    source = manifest['cases'][0]['document_id']
    digest = reviewed_store.one('SELECT sha256 FROM documents WHERE id=?', [source])['sha256']
    reviewed_store.execute('INSERT INTO v3_run_sources(run_id,document_id,sha256) VALUES(?,?,?)', ['r', source, digest])
    reviewed_store.execute('DELETE FROM v3_assessments WHERE document_id=?', [source])
    created = import_reference_panel(reviewed_store, 'p', 'r', manifest, actor_id='test-operator')
    case = json.loads(reviewed_store.one('SELECT payload_json FROM v4_evaluation_cases WHERE evaluation_id=? AND document_id=?', [created['id'], source])['payload_json'])
    assert case['model_assessment']['result'] is None
    assert case['model_assessment']['execution_state'] == 'unprocessed'


def test_real_panel_workflow_requires_preregistration_separate_adjudication_and_test_lock(reviewed_store):  # noqa: F811
    manifest = manifest_for(reviewed_store)
    created = import_reference_panel(reviewed_store, 'p', 'r', manifest, actor_id='test-operator')
    case = evaluation.export_review_form(reviewed_store, 'p', created['id'], split='heldout')['cases'][0]
    row = {'case_id': case['id'], 'case_sha256': case['case_sha256'], 'source_hash': case['source_hash'],
           'independent_blinded_review': True, 'labels': labels()}
    with pytest.raises(ValueError, match='preregistered'):
        evaluation.import_reviews(reviewed_store, 'p', created['id'], [row], reviewer_id='synthetic-A')
    with pytest.raises(ValueError, match='match the imported panel'):
        evaluation.preregister_protocol(reviewed_store, 'p', created['id'], {**protocol_for(manifest), 'domain': 'Changed domain'}, actor_id='operator')
    evaluation.preregister_protocol(reviewed_store, 'p', created['id'], protocol_for(manifest), actor_id='operator')
    for split in ('calibration', 'heldout'):
        with pytest.raises(ValueError, match='Locked'):
            evaluation.export_review_form(reviewed_store, 'p', created['id'], split=split, include_model=True)
    heldout = evaluation.export_review_form(reviewed_store, 'p', created['id'], split='heldout')['cases']
    for case in heldout:
        row = {'case_id': case['id'], 'case_sha256': case['case_sha256'], 'source_hash': case['source_hash'], 'independent_blinded_review': True, 'labels': labels()}
        for reviewer in ('synthetic-A', 'synthetic-B'):
            evaluation.import_reviews(reviewed_store, 'p', created['id'], [row], reviewer_id=reviewer)
        evaluation.adjudicate_case(reviewed_store, 'p', created['id'], case['id'], labels(), actor_id='synthetic-C')
    report = evaluation.reference_report(reviewed_store, 'p', created['id'])
    assert report['reference_metrics']['heldout'] is not None
    assert not report['release_readiness']['quantitative_supported_finding_gates_met']
    assert all(not result['release_gate']['publication_authorized'] for result in report['reference_metrics']['heldout']['criterion_metrics'])
    calibration = evaluation.export_review_form(reviewed_store, 'p', created['id'], split='calibration')['cases'][0]
    assert evaluation.locked_reference_source(reviewed_store, 'p', calibration['source']['id'], 'unrelated')
