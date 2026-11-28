import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { explainFinding, checkMitigation } from '../../web/src/finding-explanation.ts';

const fixtures = JSON.parse(await readFile(new URL('./fixtures.json', import.meta.url), 'utf8'));
const findingResults = [];
const mitigationResults = [];

for (const item of fixtures.findingCases) {
  const before = JSON.stringify(item.finding);
  const result = explainFinding(item.finding, item.report);
  const expected = item.expected;
  assert.equal(JSON.stringify(item.finding), before, item.id + ' source preservation');
  assert.equal(result.source.text, item.finding.text, item.id + ' original text');
  assert.equal(result.source.context, item.finding.context, item.id + ' original context');
  assert.equal(result.basis.kind, expected.basis, item.id + ' basis');
  assert.equal(result.basis.learnedEvidenceSpan, false, item.id + ' no invented learned span');
  assert.equal(result.roles.speaker.status, 'requires_review', item.id + ' speaker scope');
  assert.equal(result.roles.target.status, 'requires_review', item.id + ' target scope');
  for (const observation of result.observations) {
    assert(Number.isInteger(observation.start) && Number.isInteger(observation.end), item.id + ' integral offsets');
    assert(observation.start >= 0 && observation.end <= item.finding.text.length, item.id + ' bounds');
    assert.equal(item.finding.text.slice(observation.start, observation.end), observation.text, item.id + ' literal anchoring');
    assert(observation.readingQuestion.endsWith('?'), item.id + ' reading prompt');
  }
  for (const kind of expected.observationKinds ?? []) {
    assert(result.observations.some(observation => observation.kind === kind), item.id + ' ' + kind);
  }
  for (const kind of expected.forbiddenObservationKinds ?? []) {
    assert(!result.observations.some(observation => observation.kind === kind), item.id + ' unsupported ' + kind);
  }
  for (const id of expected.questionIds ?? []) {
    assert(result.questions.some(question => question.id === id && question.evidenceNeeded.length), item.id + ' ' + id);
  }
  if (expected.boundaryText) assert(result.boundaries.join(' ').includes(expected.boundaryText), item.id + ' boundary');
  for (const text of [...(expected.limitationTexts ?? []), ...(expected.limitationText ? [expected.limitationText] : [])]) {
    assert(result.limitations.join(' ').includes(text), item.id + ' ' + text);
  }
  if (expected.focusedText) assert.equal(result.source.focusedText, expected.focusedText, item.id + ' selected quote');
  if (expected.noFocusedText) assert.equal(result.source.focusedText, undefined, item.id + ' invalid focus');
  if (item.finding.score !== undefined) {
    assert.equal(result.basis.modelScore, item.finding.score);
    assert.equal(result.basis.modelScoreStatus, 'uncalibrated_class_score');
  }
  findingResults.push({ id: item.id, passed: true, anchoredObservations: result.observations.length });
}

for (const item of fixtures.mitigationCases) {
  const result = checkMitigation(item.original, item.proposed);
  assert.equal(result.status, 'review_required', item.id + ' manual semantic assessment');
  assert.equal(result.originalText, item.original, item.id + ' original preservation');
  assert.equal(result.proposedText, item.proposed, item.id + ' draft preservation');
  assert.equal(result.checks.length, 5, item.id + ' separate change checks');
  for (const id of item.expected.changed ?? []) {
    assert.equal(result.checks.find(check => check.id === id)?.status, 'changed', item.id + ' ' + id);
  }
  for (const id of item.expected.noChange ?? []) {
    assert.equal(result.checks.find(check => check.id === id)?.status, 'no_change_detected', item.id + ' ' + id);
  }
  if (item.expected.noChanged) assert(result.checks.every(check => check.status === 'no_change_detected'), item.id);
  if (item.expected.allNotAssessed) {
    assert.equal(result.hasDraft, false, item.id);
    assert(result.checks.every(check => check.status === 'not_assessed'), item.id);
  }
  if (item.expected.requiresManualSemanticReview) {
    assert(result.questions.some(question => question.includes('scope, conditions, comparison or attribution')), item.id);
    assert(result.limitations.some(text => text.includes('different propositions')), item.id);
  }
  mitigationResults.push({ id: item.id, passed: true, changed: result.checks.filter(check => check.status === 'changed').map(check => check.id) });
}

const result = {
  method: fixtures.method,
  fixtureOrigin: fixtures.origin,
  annotationProvenance: fixtures.annotation_provenance,
  independentHumanReviewCount: fixtures.independent_human_review_count,
  scope: fixtures.scope,
  findingCases: findingResults,
  mitigationCases: mitigationResults,
  passed: findingResults.length + mitigationResults.length,
  failed: 0,
  sourceObservationsChecked: findingResults.reduce((sum, item) => sum + item.anchoredObservations, 0),
};
await writeFile(new URL('./evaluation.json', import.meta.url), JSON.stringify(result, null, 2) + '\n');
console.log(JSON.stringify({ passed: result.passed, failed: result.failed, sourceObservationsChecked: result.sourceObservationsChecked }));
