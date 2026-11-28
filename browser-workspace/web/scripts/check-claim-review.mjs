import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { claimLeads } from '../src/research-claims.ts';

const stress = process.argv.includes('--stress');
const fixture = stress ? 'claim-review-stress.json' : 'claim-review-checks.json';
const cases = JSON.parse(await readFile(new URL('../data/'+fixture,import.meta.url),'utf8'));
// Reproduce the two v0.4.0 academic co-occurrence patterns as a passage-level control.
const oldCausal = /\b(?:caused|led to|resulted in|drives?|demonstrat(?:e|es|ed) that|proves? that|because of)\b/i;
const oldMarker = /\[(?:\d+[\d,;– -]*)\]|\([^()\n]{1,55},?\s+(?:19|20)\d{2}[a-z]?\)|\b10\.\d{4,9}\/[\w.()/:-]+/i;
const target = 'direct_without_marker';
const results = { legacy:{tp:0,fp:0,fn:0}, sentence:{tp:0,fp:0,fn:0} };
let exact = 0;
for (const {id,text,kinds} of cases) {
  const leads = claimLeads(text);
  for (const {start,end,matchStart,matchEnd,citation} of leads) {
    assert(start <= matchStart && matchStart < matchEnd && matchEnd <= end && end <= text.length,id);
    if (citation) assert(start <= citation.start && citation.start < citation.end && citation.end <= end,id);
  }
  const actual = leads.map(x=>x.kind);
  if (actual.join()===kinds.join()) exact++;
  else console.log('Case difference',id,{expected:kinds,actual});
  const expected = kinds.includes(target);
  for (const [name,prediction] of Object.entries({
    legacy:oldCausal.test(text) && !oldMarker.test(text),
    sentence:actual.includes(target),
  })) {
    const metric = prediction ? (expected ? 'tp' : 'fp') : (expected ? 'fn' : null);
    if (metric) results[name][metric]++;
  }
}
for (const counts of Object.values(results)) {
  const {tp,fp,fn}=counts;
  counts.precision=tp/(tp+fp);
  counts.recall=tp/(tp+fn);
}
console.log(JSON.stringify({fixture,cases:cases.length,exact,results},null,2));
if (!stress) {
  assert.equal(exact,cases.length,'The development checks must remain exact');
  assert(results.sentence.fp < results.legacy.fp && results.sentence.fn < results.legacy.fn);
}
