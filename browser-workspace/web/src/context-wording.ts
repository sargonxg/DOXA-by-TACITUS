export type WordingDecision = 'wording_flag' | 'no_wording_flag' | 'unresolved';

export type WordingResult = {
  method: 'doxa-wording-minilm-v1';
  task: 'biased_wording';
  decision: WordingDecision;
  model_score: number | null;
  class_scores: { no_biased_wording: number; biased_wording: number } | null;
  score_calibration: string;
  decision_threshold: number;
  token_count: number;
  maximum_tokens: number;
  model_id: string;
  model_revision: string;
  context_used: false;
  reason: 'below_resolution_threshold' | 'input_too_long' | null;
  scope: string;
};

export type WordingCommand = {
  kind: 'check';
  id: string;
  text: string;
  /** Folder containing the checked manifest.json. */
  base: string;
  /** Existing ORT WASM files. Defaults to the sibling verifier folder. */
  wasmBase?: string;
};

export type WordingEvent =
  | { kind: 'status'; id: string; text: string }
  | { kind: 'result'; id: string; result: WordingResult }
  | { kind: 'error'; id: string; text: string };

export function createWordingWorker() {
  return new Worker(new URL('./context-wording-worker.ts', import.meta.url), { type: 'module' });
}
