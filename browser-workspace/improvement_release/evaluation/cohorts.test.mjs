import assert from 'node:assert/strict';
import test from 'node:test';
import { buildComparisonCohorts } from '../../web/src/comparison-cohorts.ts';

const metadata = { event: 'Election debate', topic: 'Transport', genre: 'News report', period: '2026-10' };
const counts = (present, absent, uncertain = 0, abstained = 0) => ({ present, absent, uncertain, abstained, unsupported: 0 });
const source = (id, overrides = {}) => ({
  id, label: id, metadata: { ...metadata }, criteria: { evaluative_framing: counts(1, 9) },
  modelRevision: 'frozen-model', complete: true,
  coverage: { screened: 10, eligible: 10, pages: 3, emptyPages: 0, extractionErrors: 0 },
  ...overrides,
});

test('matches explicit labels while keeping another period and missing labels unmatched', () => {
  const result = buildComparisonCohorts([
    source('a'), source('b', { metadata: { ...metadata, event: '  ELECTION   DEBATE ' } }),
    source('c', { metadata: { ...metadata, period: '2026-11' } }),
    source('d', { metadata: { ...metadata, event: 'unknown', topic: '' } }),
  ]);
  assert.equal(result.matchedDocumentCount, 2);
  assert.deepEqual(result.matchedGroups[0].documentIds, ['a', 'b']);
  assert.deepEqual(result.unmatched, [
    { id: 'd', label: 'd', reason: 'missing_metadata', missingFields: ['event', 'topic'] },
    { id: 'c', label: 'c', reason: 'no_matching_peer', missingFields: [] },
  ]);
});

test('pools raw counts before rates and exposes unresolved coverage', () => {
  const result = buildComparisonCohorts([
    source('a', { criteria: { evaluative_framing: counts(7, 5, 60) } }),
    source('b', { criteria: { evaluative_framing: counts(5, 14, 54) } }),
  ]);
  const rate = result.matchedGroups[0].criteria.evaluative_framing;
  assert.equal(rate.assessed, 145);
  assert.equal(rate.resolved, 31);
  assert.equal(rate.unresolved, 114);
  assert.equal(rate.flaggedAmongAssessed, 12 / 145);
  assert.equal(rate.flaggedAmongResolved, 12 / 31);
  assert.equal(rate.resolutionCoverage, 31 / 145);
});

test('does not turn missing heads or absent source coverage into complete observations', () => {
  const result = buildComparisonCohorts([
    source('a', { criteria: { evaluative_framing: counts(0, 0, 0, 10) } }),
    source('b', { criteria: {}, coverage: undefined, complete: undefined, modelRevision: undefined }),
  ]);
  const group = result.matchedGroups[0];
  assert.equal(group.criteria.evaluative_framing.flaggedAmongResolved, null);
  assert.equal(group.criteria.evaluative_framing.resolutionCoverage, 0);
  assert.deepEqual(group.criteria.evaluative_framing.missingDocumentIds, ['b']);
  assert.deepEqual(group.coverage.unknownDocumentIds, ['b']);
  assert.deepEqual(group.coverage.unknownCompletionDocumentIds, ['b']);
  assert.deepEqual(group.missingModelRevisionDocumentIds, ['b']);
});

test('reports mixed model revisions and leaves input counts unchanged', () => {
  const input = [source('a'), source('b', { modelRevision: 'another-model', complete: false })];
  const before = JSON.stringify(input);
  const group = buildComparisonCohorts(input).matchedGroups[0];
  assert.deepEqual(group.modelRevisions, ['another-model', 'frozen-model']);
  assert(group.cautions.some(text => text.includes('different model revisions')));
  assert.deepEqual(group.coverage.partialDocumentIds, ['b']);
  group.criteria.evaluative_framing.counts.present = 500;
  assert.equal(JSON.stringify(input), before);
});

test('rejects duplicate IDs, invalid counts and empty comparison conditions', () => {
  assert.throws(() => buildComparisonCohorts([source('a'), source('a')]), /Duplicate/);
  assert.throws(() => buildComparisonCohorts([source('a', { criteria: { evaluative_framing: counts(-1, 10) } })]), /Invalid/);
  assert.throws(() => buildComparisonCohorts([source('a')], []), /comparison fields/);
});
