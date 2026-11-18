"""Execution-contract tests use an injected synthetic engine, never a model."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from doxa.functional_evaluation import execute_casebook, prepare_execution

ROOT = Path(__file__).resolve().parents[1]


def casebook():
    return json.loads((ROOT / 'benchmarks/evaluation-v04/functional-casebook.json').read_text())


def review_for(book, count=1):
    return {'casebook_sha256': book['casebook_sha256'], 'origin': 'human_rubric_review', 'reviewer_id': 'synthetic-test-reviewer',
            'fixtures': [{'fixture_id': row['id'], 'fixture_sha256': row['fixture_sha256'], 'approved': True,
                          'note': 'Synthetic software approval only, not a real reference study.'} for row in book['fixtures'][:count]]}


def test_preparation_is_offline_and_checks_frozen_inputs():
    book = casebook()
    result = prepare_execution(book, limit=5)
    assert result['status'] == 'prepared_not_run'
    assert result['available_fixtures'] == 120
    assert result['network_requests'] == result['model_requests'] == 0
    assert not result['independent_reference_labels']
    changed = deepcopy(book)
    changed['fixtures'][0]['text'] += 'Changed'
    with pytest.raises(ValueError, match='casebook hash'):
        prepare_execution(changed)


@pytest.mark.asyncio
async def test_no_execute_flag_cannot_construct_provider():
    async def forbidden(*args):
        raise AssertionError('A provider must not be constructed during preparation')
    result = await execute_casebook(casebook(), None, {}, provider_factory=forbidden)
    assert result['status'] == 'prepared_not_run'


@pytest.mark.asyncio
async def test_reviews_remote_permission_and_https_checked_before_provider():
    book = casebook()
    async def forbidden(*args):
        raise AssertionError('Invalid input must not create a provider')
    with pytest.raises(ValueError, match='human rubric review'):
        await execute_casebook(book, {}, {'provider': 'ollama'}, execute=True, limit=1, provider_factory=forbidden)
    with pytest.raises(ValueError, match='permission and HTTPS'):
        await execute_casebook(book, review_for(book), {'provider': 'openai', 'base_url': 'https://example.com/v1'}, execute=True, limit=1, provider_factory=forbidden)
    # A loopback gateway is still remote unless explicitly declared local.
    with pytest.raises(ValueError, match='permission and HTTPS'):
        await execute_casebook(book, review_for(book), {'provider': 'openai', 'base_url': 'http://127.0.0.1:9999/v1'}, execute=True, limit=1, provider_factory=forbidden)


@pytest.mark.asyncio
async def test_explicit_execution_preserves_partial_results_and_spend_cap():
    class SyntheticProvider:
        def estimate_cost(self, *args):
            return 0.01
    async def factory(*args):
        return SyntheticProvider()
    async def engine(document, lenses, provider, *, units, config):
        assert document['metadata']['data_origin'] == 'derived_functional_fixture'
        assert all(sentence['document_id'] == document['id'] for unit in units for sentence in unit['context_before'])
        reserved = await config['_reserve_budget'](0.01)
        return {'assessments': [], 'coverage': {'complete': reserved},
                'errors': [] if reserved else [{'code': 'budget_exhausted'}], 'usage': {'requests': int(reserved)}}
    book = casebook()
    result = await execute_casebook(book, review_for(book, 2), {'provider': 'ollama'}, execute=True, limit=2,
                                    budget_usd=0.01, provider_factory=factory, engine=engine)
    assert result['status'] == 'partial'
    assert result['budget']['conservative_reserved_usd'] == 0.01
    assert all(row['passed'] is None for row in result['results'])
    assert result['interpretation_accuracy'] is None
    assert not result['reviewer_qualification_verified']
