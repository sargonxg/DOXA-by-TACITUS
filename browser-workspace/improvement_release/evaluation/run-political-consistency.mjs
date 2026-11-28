import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { cpus, availableParallelism } from 'node:os';
import { performance } from 'node:perf_hooks';
import { resolve } from 'node:path';
import { loadModel, predict } from '../../web/src/inference.ts';
import { buildComparisonCohorts } from '../../web/src/comparison-cohorts.ts';

const root = new URL('./', import.meta.url);
const fixtureBytes = await readFile(new URL('political-consistency-fixtures.json', root));
const fixtures = JSON.parse(fixtureBytes);
const protocol = JSON.parse(await readFile(new URL('POLITICAL_CONSISTENCY_PROTOCOL.json', root), 'utf8'));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
if (hash(fixtureBytes) !== protocol.fixtures_sha256) throw new Error('Fixture bytes changed after preregistration.');
const modelDirectory = resolve(process.argv[2] || 'release_staging/model');
const assetNames = ['model_manifest.json', 'feature_spec.json', 'vocabulary.json', 'idf.f32', 'coefficients.f32', 'intercepts.f32'];
const assets = new Map(await Promise.all(assetNames.map(async name => [name, await readFile(resolve(modelDirectory, name))])));
const server = createServer((request, response) => {
  const name = new URL(request.url, 'http://localhost').pathname.slice(1);
  const content = assets.get(name);
  response.writeHead(content ? 200 : 404);
  response.end(content);
});
await new Promise(done => server.listen(0, '127.0.0.1', done));
const address = server.address();
const base = `http://127.0.0.1:${address.port}/`;
globalThis.location = { href: base };
let model;
const started = performance.now();
try { model = await loadModel(base); }
finally { await new Promise(done => server.close(done)); }
const loadMilliseconds = performance.now() - started;
for (let iteration = 0; iteration < 5; iteration++) predict(model, 'The committee published the table and reported the counts.', 'Fictional warm-up passage.');
const timings = [];
const output = [];
let peakObservedRss = process.memoryUsage().rss;
for (const pair of fixtures.pairs) {
  const arms = [];
  for (const arm of pair.arms) {
    const before = performance.now();
    const result = predict(model, arm.text, arm.context);
    timings.push(performance.now() - before);
    peakObservedRss = Math.max(peakObservedRss, process.memoryUsage().rss);
    arms.push({ id: arm.id, actor: arm.actor, result });
  }
  output.push({ pair_id: pair.pair_id, family_id: pair.family_id, swap_id: pair.swap_id, kind: pair.kind,
    expected_decisions: pair.expected_decisions, arms });
}
const criteria = Object.fromEntries(model.manifest.heads.map(criterion => [criterion, {
  paired_checks: 0, changed_pairs: 0, maximum_class_score_difference: 0,
  hard_negative_checks: 0, hard_negative_present_flags: 0, hard_negative_absent_outputs: 0, hard_negative_unresolved_outputs: 0,
  positive_target_checks: 0, positive_target_present_outputs: 0, positive_target_absent_outputs: 0, positive_target_unresolved_outputs: 0,
}]));
const outputStatuses = {};
const failures = [];
for (const pair of output) {
  for (const criterion of model.manifest.heads) {
    const record = criteria[criterion];
    const arms = pair.arms.map(arm => arm.result.criteria.find(row => row.criterion_id === criterion));
    record.paired_checks++;
    if (arms[0].prediction_status !== arms[1].prediction_status || arms[0].suggested_decision !== arms[1].suggested_decision) {
      record.changed_pairs++;
      failures.push({ type: 'paired_decision_change', pair_id: pair.pair_id, criterion,
        decisions: arms.map(row => row.suggested_decision), statuses: arms.map(row => row.prediction_status) });
    }
    if (arms.every(row => row.scores)) {
      for (const label of model.manifest.class_order) record.maximum_class_score_difference = Math.max(record.maximum_class_score_difference, Math.abs(arms[0].scores[label] - arms[1].scores[label]));
    }
    const expected = pair.expected_decisions[criterion];
    for (const [index, row] of arms.entries()) {
      const statusKey = criterion + ':' + row.prediction_status;
      outputStatuses[statusKey] = (outputStatuses[statusKey] || 0) + 1;
      if (!expected) continue;
      const negative = expected === 'absent';
      const prefix = negative ? 'hard_negative' : 'positive_target';
      record[prefix + '_checks']++;
      const decision = row.prediction_status === 'ok' ? row.suggested_decision : 'unresolved';
      if (negative) {
        if (decision === 'present') record.hard_negative_present_flags++;
        else if (decision === 'absent') record.hard_negative_absent_outputs++;
        else record.hard_negative_unresolved_outputs++;
      } else {
        if (decision === 'present') record.positive_target_present_outputs++;
        else if (decision === 'absent') record.positive_target_absent_outputs++;
        else record.positive_target_unresolved_outputs++;
      }
      if (decision !== expected) failures.push({ type: 'authored_expectation_mismatch', pair_id: pair.pair_id,
        arm_id: pair.arms[index].id, criterion, expected, actual: row.suggested_decision, prediction_status: row.prediction_status });
    }
  }
}
const cohortInput = Array.from({ length: 1000 }, (_, index) => ({
  id: 'synthetic-count-source-' + index, label: 'Synthetic count source ' + index,
  metadata: { event: 'Event ' + Math.floor(index / 25), topic: 'Budget', genre: 'Report', period: '2026-10' },
  criteria: { evaluative_framing: { present: index % 9, absent: 80 - index % 9, uncertain: 20, abstained: 0, unsupported: 0 } },
  modelRevision: model.manifest.model_revision, complete: true,
  coverage: { screened: 100, eligible: 100, pages: 10, emptyPages: 0, extractionErrors: 0 },
}));
const cohortStarted = performance.now();
const cohorts = buildComparisonCohorts(cohortInput);
const cohortMilliseconds = performance.now() - cohortStarted;
const sorted = [...timings].sort((a, b) => a - b);
const report = {
  revision: 'doxa-political-consistency-result-v0.1.0', fixtures_sha256: hash(fixtureBytes),
  model_revision: model.manifest.model_revision, training_data_revision: model.manifest.training_data_revision,
  model_assets_sha256: Object.fromEntries([...assets].map(([name, bytes]) => [name, hash(bytes)])),
  annotation_status: 'ai_provisional', independent_human_review_count: 0,
  fixture_families: fixtures.families, matched_pairs: output.length, passages: timings.length,
  supported_criteria: model.manifest.heads, supported_head_decisions: timings.length * model.manifest.heads.length,
  paired_supported_head_checks: output.length * model.manifest.heads.length,
  criteria, output_statuses: outputStatuses, failures,
  machine_measurements: { node: process.version, platform: process.platform, architecture: process.arch,
    cpu: cpus()[0]?.model || 'unknown', available_parallelism: availableParallelism(),
    local_model_load_milliseconds: loadMilliseconds, warmup_passages_excluded: 5,
    scored_passage_milliseconds_total: timings.reduce((sum, value) => sum + value, 0),
    scored_passage_milliseconds_median: sorted[Math.floor(sorted.length / 2)],
    scored_passage_milliseconds_p95: sorted[Math.ceil(sorted.length * 0.95) - 1],
    peak_observed_process_rss_bytes: peakObservedRss,
    metadata_count_aggregation: { synthetic_documents: 1000, matched_groups: cohorts.matchedGroups.length,
      matched_documents: cohorts.matchedDocumentCount, milliseconds: cohortMilliseconds,
      scope: 'Grouping already-computed synthetic counts. Text extraction and model inference are outside this timer.' } },
  scope: protocol.label_scope,
  inference_scope: 'Predictions use the deployed browser linear inference implementation and frozen local model assets. These authored dependent fixtures supply diagnostic controls only.',
  new_spend_usd: 0,
};
await writeFile(new URL('political-consistency-results.json', root), JSON.stringify(report, null, 2) + '\n');
await writeFile(new URL('political-consistency-predictions.json', root), JSON.stringify({ fixtures_sha256: hash(fixtureBytes), model_revision: model.manifest.model_revision, pairs: output }, null, 2) + '\n');
console.log(JSON.stringify({ model_revision: report.model_revision, pairs: report.matched_pairs,
  changed_pairs_by_criterion: Object.fromEntries(Object.entries(criteria).map(([key, value]) => [key, value.changed_pairs])),
  authored_expectation_mismatches: failures.filter(row => row.type === 'authored_expectation_mismatch').length,
  machine_measurements: report.machine_measurements }, null, 2));
