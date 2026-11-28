/** Browser implementation of DOXA's portable character TF-IDF classifier. */
const labels = ["present", "absent", "insufficient_context"] as const;
type Decision = typeof labels[number];
type Scores = Record<Decision, number>;
export interface AssessmentResult {
  criterion_id: string;
  prediction_status: "ok" | "abstained" | "unsupported_criterion" | "invalid_input";
  reason: string | null;
  suggested_decision: Decision | null;
  scores: Scores | null;
  score_calibration: "not_validated";
  evidence: [];
  evidence_status: "not_generated_by_this_model";
  model_cues: [];
  severity_assessment: {status: "not_assessed"; descriptor: null; source: null};
  suggested_review_priority: "P0" | "P1" | "P2" | "P3" | "unassigned";
  recommendations: string[];
  human_review_status: "unreviewed";
}
interface Manifest {
  class_order: Decision[];
  heads: string[];
  criteria: string[];
  shapes: {idf: number[]; coefficients: number[]; intercepts: number[]};
  model_revision: string;
  training_data_revision: string;
}
interface Spec {
  ngram_lengths: number[];
  whitespace_codepoints: number[];
  max_text_codepoints: number;
  max_context_codepoints: number;
}
export interface Model {
  manifest: Manifest;
  spec: Spec;
  vocabulary: Record<string, number>;
  whitespace: Set<number>;
  idf: Float32Array;
  coefficients: Float32Array;
  intercepts: Float32Array;
}

export function decodeF32(buffer: ArrayBuffer): Float32Array {
  if (buffer.byteLength % 4) throw new Error("Invalid float32 asset length");
  const view = new DataView(buffer);
  return Float32Array.from({length: buffer.byteLength / 4}, (_, index) =>
    view.getFloat32(index * 4, true));
}

export async function loadModel(baseUrl: string | URL): Promise<Model> {
  const base = new URL(baseUrl, globalThis.location.href);
  async function read(file: string) {
    const response = await fetch(new URL(file, base));
    if (!response.ok) throw new Error(`Could not load model asset ${file} (${response.status})`);
    return response;
  }
  const [manifest, spec, vocabulary, idf, coefficients, intercepts] = await Promise.all([
    read("model_manifest.json").then(r => r.json() as Promise<Manifest>),
    read("feature_spec.json").then(r => r.json() as Promise<Spec>),
    read("vocabulary.json").then(r => r.json() as Promise<Record<string, number>>),
    ...["idf.f32", "coefficients.f32", "intercepts.f32"].map(file =>
      read(file).then(r => r.arrayBuffer()).then(decodeF32)) as
      [Promise<Float32Array>, Promise<Float32Array>, Promise<Float32Array>],
  ] as const);
  if (manifest.class_order.join(",") !== labels.join(",") ||
      Object.keys(vocabulary).length !== idf.length ||
      coefficients.length !== manifest.heads.length * labels.length * idf.length ||
      intercepts.length !== manifest.heads.length * labels.length) {
    throw new Error("Model dimensions do not match the manifest");
  }
  return {manifest, spec, vocabulary, idf, coefficients, intercepts,
    whitespace: new Set(spec.whitespace_codepoints)};
}

function preprocess(value: string, whitespace: Set<number>): string[] {
  const chars = Array.from(value.normalize("NFC"), char => {
    const point = char.codePointAt(0)!;
    if (point >= 65 && point <= 90) return String.fromCodePoint(point + 32);
    return whitespace.has(point) ? " " : char;
  });
  return Array.from(chars.join("").replace(/ +/g, " ").replace(/^ | $/g, ""));
}

export function features(model: Model, text: string, context: string) {
  const counts = new Map<number, number>();
  let knownPassage = false;
  for (const [channel, value] of [["t", text], ["c", context]] as const) {
    const chars = preprocess(value, model.whitespace);
    for (const length of model.spec.ngram_lengths) {
      for (let i = 0; i <= chars.length - length; i++) {
        const key = channel + ":" + chars.slice(i, i + length).join("");
        const index = model.vocabulary[key];
        if (index === undefined) continue;
        counts.set(index, (counts.get(index) ?? 0) + 1);
        if (channel === "t") knownPassage = true;
      }
    }
  }
  const sorted = [...counts.entries()].sort(([a], [b]) => a - b);
  const weights = sorted.map(([index, count]) =>
    [index, (1 + Math.log(count)) * model.idf[index]] as [number, number]);
  const norm = Math.sqrt(weights.reduce((sum, [, w]) => sum + w * w, 0));
  if (norm) weights.forEach(pair => { pair[1] /= norm; });
  return {counts: sorted, weights, knownPassage};
}

function inputError(model: Model, text: string, context: string): string | null {
  if (typeof text !== "string" || typeof context !== "string") return "expected_text";
  const passage = Array.from(text);
  const surrounding = Array.from(context);
  if (passage.length > model.spec.max_text_codepoints ||
      surrounding.length > model.spec.max_context_codepoints) return "input_limit_exceeded";
  if ([...passage, ...surrounding].some(char => {
    const point = char.codePointAt(0)!;
    return point >= 0xD800 && point <= 0xDFFF;
  })) return "invalid_unicode";
  return preprocess(text, model.whitespace).length ? null : "empty_passage";
}

const actions: Record<string, string[]> = {
  evaluative_framing: ["offer_optional_rewording", "add_attribution"],
  evidential_overclaim: ["request_source", "qualify_causal_claim"],
  collective_blame: ["specialist_review", "specify_responsible_actor"],
  identity_stereotyping: ["specialist_review", "remove_collective_generalization"],
  identity_derogation_exclusion: ["specialist_review"],
  group_conspiracy: ["specialist_review", "request_source"],
};

export function predict(model: Model, text: string, context = "",
    criterionIds = model.manifest.criteria, recordId = "local-input") {
  const error = inputError(model, text, context);
  const {counts, weights, knownPassage} = error
    ? {counts: [], weights: [], knownPassage: false}
    : features(model, text, context);
  const criteria: AssessmentResult[] = criterionIds.map(criterion => {
    const result: AssessmentResult = {
      criterion_id: criterion, prediction_status: "ok", reason: null,
      suggested_decision: null, scores: null, score_calibration: "not_validated", evidence: [],
      evidence_status: "not_generated_by_this_model", model_cues: [],
      severity_assessment: {status: "not_assessed", descriptor: null, source: null},
      suggested_review_priority: "unassigned", recommendations: [], human_review_status: "unreviewed",
    };
    const head = model.manifest.heads.indexOf(criterion);
    if (error) {
      result.prediction_status = "invalid_input";
      result.reason = error;
    } else if (head < 0) {
      result.prediction_status = "unsupported_criterion";
      result.reason = "no_deployed_head";
      result.recommendations = ["request_context"];
    } else if (!knownPassage) {
      result.prediction_status = "abstained";
      result.reason = counts.length ? "context_only_features" : "no_known_features";
      result.recommendations = ["request_context"];
    } else {
      const logits = labels.map((_, k) => {
        const row = head * labels.length + k;
        const contribution = weights.reduce((sum, [index, weight]) =>
          sum + weight * model.coefficients[row * model.idf.length + index], 0);
        return model.intercepts[row] + contribution;
      });
      const maximum = Math.max(...logits);
      const exp = logits.map(logit => Math.exp(logit - maximum));
      const denominator = exp.reduce((sum, value) => sum + value, 0);
      result.scores = Object.fromEntries(labels.map((label, i) =>
        [label, exp[i] / denominator])) as Scores;
      const best = labels[logits.indexOf(maximum)];
      result.suggested_decision = best;
      if (best === "insufficient_context") {
        result.suggested_review_priority = "P2";
        result.recommendations = ["request_context"];
      } else if (best === "present") {
        result.suggested_review_priority = criterion === "evaluative_framing" ? "P1"
          : criterion === "evidential_overclaim" ? "P2" : "P3";
        result.recommendations = actions[criterion] ?? ["request_context"];
      } else {
        result.recommendations = ["no_change"];
      }
    }
    return result;
  });
  if (criteria.length && criteria.every(result => result.suggested_decision === "absent")) {
    criteria.forEach(result => { result.suggested_review_priority = "P0"; });
  }
  return {
    schema_version: "1.0.0", record_id: recordId, assessment_stage: "screening", criteria,
    model_revision: model.manifest.model_revision,
    training_data_revision: model.manifest.training_data_revision,
    review_policy_version: "doxa-review-v0.1",
    limitations: [
      "experimental_provisional_pilot", "english_only",
      "scores_are_uncalibrated", "user_context_is_not_independently_verified",
    ],
  };
}
