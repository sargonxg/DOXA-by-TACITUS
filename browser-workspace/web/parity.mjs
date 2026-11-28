import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {decodeF32, features, predict} from "./src/inference.ts";

const folder = resolve(process.argv[2] ?? "../release_staging/model");
const json = async name => JSON.parse(await readFile(resolve(folder, name), "utf8"));
const binary = async name => {
  const bytes = await readFile(resolve(folder, name));
  return decodeF32(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
};
const [manifest, spec, vocabulary, idf, coefficients, intercepts] = await Promise.all([
  json("model_manifest.json"), json("feature_spec.json"), json("vocabulary.json"),
  binary("idf.f32"), binary("coefficients.f32"), binary("intercepts.f32"),
]);
const model = {manifest, spec, vocabulary, idf, coefficients, intercepts,
  whitespace: new Set(spec.whitespace_codepoints)};
const fixtures = (await readFile(resolve(folder, "parity_cases.jsonl"), "utf8"))
  .trim().split("\n").map(line => JSON.parse(line));
let maxError = 0;
for (const fixture of fixtures) {
  const actual = predict(model, fixture.text, fixture.context);
  const expected = fixture.output;
  assert.deepEqual({...actual, criteria: []}, {...expected, criteria: []});
  if (!actual.criteria.every(result => result.prediction_status === "invalid_input")) {
    assert.deepEqual(features(model, fixture.text, fixture.context).counts, fixture.counts);
  }
  for (let i = 0; i < expected.criteria.length; i++) {
    const {scores: jsScores, ...jsFields} = actual.criteria[i];
    const {scores: pyScores, ...pyFields} = expected.criteria[i];
    assert.deepEqual(jsFields, pyFields);
    if (jsScores === null || pyScores === null) {
      assert.equal(jsScores, pyScores);
      continue;
    }
    for (const name of manifest.class_order) {
      maxError = Math.max(maxError, Math.abs(jsScores[name] - pyScores[name]));
    }
  }
}
assert(maxError <= 1e-5, `Python/TypeScript error ${maxError} exceeds 1e-5`);
console.log(JSON.stringify({fixtures: fixtures.length, maximum_score_difference: maxError}));
