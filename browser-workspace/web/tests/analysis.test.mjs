import assert from 'node:assert/strict';
import test from 'node:test';
import { countSummary, poolCounts, percent } from '../src/report-analysis.ts';
import { claimLeads } from '../src/research-claims.ts';

test('keeps the common denominator when most checks need context', () => {
  const counts = { present: 7, absent: 5, uncertain: 60, abstained: 0, unsupported: 0 };
  const summary = countSummary(counts);
  assert.deepEqual(summary, { total: 72, resolved: 12, unresolved: 60 });
  assert.equal(percent(counts.present, summary.total), '9.7%');
  assert.equal(percent(counts.present, summary.resolved), '58.3%');
});

test('pools counts before computing author rates and conserves all outcomes', () => {
  const pool = poolCounts([
    { present: 7, absent: 5, uncertain: 60, abstained: 0, unsupported: 0 },
    undefined,
    { present: 5, absent: 14, uncertain: 54, abstained: 0, unsupported: 0 },
  ]);
  assert.deepEqual(pool, { present: 12, absent: 19, uncertain: 114, abstained: 0, unsupported: 0 });
  const summary = countSummary(pool);
  assert.equal(summary.total, 145);
  assert.equal(summary.resolved + summary.unresolved, 145);
  assert.equal(percent(pool.present, summary.resolved), '38.7%');
});

test('preserves abstained and unsupported decisions with zero resolved checks', () => {
  const summary = countSummary({ present: 0, absent: 0, uncertain: 0, abstained: 40, unsupported: 2 });
  assert.deepEqual(summary, { total: 42, resolved: 0, unresolved: 42 });
  assert.equal(percent(0, 0), '—');
  assert.equal(countSummary(poolCounts([])).total, 0);
});

test('keeps a wrapped PDF sentence and its citation together with source offsets', () => {
  const text='The observational study may have led\nto a rise in reported turnout\namong the surveyed residents [12]. Further evidence is needed.';
  const [lead]=claimLeads(text);
  assert.equal(lead.kind,'qualified');
  assert.equal(text.slice(lead.start,lead.end),'The observational study may have led\nto a rise in reported turnout\namong the surveyed residents [12].');
  assert.equal(text.slice(lead.matchStart,lead.matchEnd),'led\nto');
  assert.equal(text.slice(lead.citation.start,lead.citation.end),'[12]');
});

test('recognizes spaced PDF citations and marks an incomplete source fragment', () => {
  const text='The policy caused an increase\nin the survey response [ 5 , 6 ].';
  const [lead]=claimLeads(text);
  assert.equal(lead.kind,'direct_with_marker');
  assert.equal(text.slice(lead.citation.start,lead.citation.end),'[ 5 , 6 ]');
  assert.equal(lead.partial,false);
  assert.equal(claimLeads('The policy caused the increase among')[0].partial,true);
});
