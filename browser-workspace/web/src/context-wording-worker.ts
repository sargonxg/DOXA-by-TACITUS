import { Tokenizer } from '@huggingface/tokenizers';
import { env, InferenceSession, Tensor } from 'onnxruntime-web/wasm';
import type { WordingCommand, WordingEvent, WordingResult } from './context-wording';

type Asset = { path: string; sha256: string };
type Manifest = {
  method: 'doxa-wording-minilm-v1';
  task: 'biased_wording';
  model_id: string;
  revision: string;
  maximum_tokens: number;
  temperature: number;
  threshold: number;
  score_calibration: string;
  scope: string;
  files: { tokenizer: Asset; weights: Asset };
};
type Engine = { manifest: Manifest; tokenizer: Tokenizer; session: InferenceSession };

const emit = (event: WordingEvent) => self.postMessage(event);
let pendingEngine: Promise<Engine> | undefined;
let engineBase = '';
let busy = false;

async function checkedAsset(root: string, asset: Asset) {
  const response = await fetch(new URL(asset.path, root), { credentials: 'omit', referrerPolicy: 'no-referrer' });
  if (!response.ok) throw new Error('The pinned wording model could not download.');
  const bytes = await response.arrayBuffer();
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
    value => value.toString(16).padStart(2, '0')).join('');
  if (hash !== asset.sha256) throw new Error('The wording model did not match its published hash.');
  return bytes;
}

async function load(base: string, wasmBase: string) {
  const response = await fetch(new URL('manifest.json', base));
  if (!response.ok) throw new Error('The wording model configuration is unavailable.');
  const manifest = await response.json() as Manifest;
  if (manifest.method !== 'doxa-wording-minilm-v1' || manifest.task !== 'biased_wording'
      || !(manifest.temperature > 0) || manifest.threshold < .5 || manifest.threshold > 1
      || manifest.maximum_tokens < 2 || manifest.maximum_tokens > 512) {
    throw new Error('The wording model configuration is invalid.');
  }
  const source = `https://huggingface.co/${manifest.model_id}/resolve/${manifest.revision}/`;
  env.wasm.numThreads = 1;
  env.wasm.wasmPaths = {
    mjs: new URL('ort-wasm-simd-threaded.mjs', wasmBase),
    wasm: new URL('ort-wasm-simd-threaded.wasm', wasmBase),
  };
  const [tokenizerBytes, modelBytes] = await Promise.all([
    checkedAsset(source, manifest.files.tokenizer), checkedAsset(source, manifest.files.weights),
  ]);
  const tokenizerData = JSON.parse(new TextDecoder().decode(tokenizerBytes));
  if (tokenizerData.truncation) throw new Error('The wording tokenizer must preserve full input length.');
  const tokenizer = new Tokenizer(tokenizerData, {});
  const session = await InferenceSession.create(modelBytes, { executionProviders: ['wasm'], graphOptimizationLevel: 'all' });
  return { manifest, tokenizer, session };
}

async function check(engine: Engine, text: string): Promise<WordingResult> {
  const { manifest, tokenizer, session } = engine;
  const tokens = tokenizer.encode(text, { return_token_type_ids: true });
  const length = tokens.ids.length;
  const result: WordingResult = {
    method: manifest.method, task: manifest.task, decision: 'unresolved', model_score: null, class_scores: null,
    score_calibration: manifest.score_calibration, decision_threshold: manifest.threshold,
    token_count: length, maximum_tokens: manifest.maximum_tokens, model_id: manifest.model_id,
    model_revision: manifest.revision, context_used: false, reason: null, scope: manifest.scope,
  };
  if (length > manifest.maximum_tokens) return { ...result, reason: 'input_too_long' };
  const values: Record<string, number[]> = {
    input_ids: tokens.ids, attention_mask: tokens.attention_mask, token_type_ids: tokens.token_type_ids,
  };
  const tensors = Object.fromEntries(session.inputNames.map(name => [
    name, new Tensor('int64', BigInt64Array.from(values[name], BigInt), [1, length]),
  ]));
  const output = await session.run(tensors);
  const logits = output[session.outputNames[0]].data;
  const left = Number(logits[0]) / manifest.temperature;
  const right = Number(logits[1]) / manifest.temperature;
  const offset = Math.max(left, right);
  const positive = Math.exp(right - offset) / (Math.exp(left - offset) + Math.exp(right - offset));
  result.model_score = positive;
  result.class_scores = { no_biased_wording: 1 - positive, biased_wording: positive };
  if (Math.max(positive, 1 - positive) < manifest.threshold) result.reason = 'below_resolution_threshold';
  else result.decision = positive >= .5 ? 'wording_flag' : 'no_wording_flag';
  return result;
}

self.addEventListener('message', async ({ data }: MessageEvent<WordingCommand>) => {
  if (data.kind !== 'check') return;
  if (busy) { emit({ kind: 'error', id: data.id, text: 'Another wording check is running.' }); return; }
  if (!data.text.trim()) { emit({ kind: 'error', id: data.id, text: 'Choose a non-empty passage.' }); return; }
  busy = true;
  try {
    if (!pendingEngine || engineBase !== data.base) {
      emit({ kind: 'status', id: data.id, text: 'Loading the pinned wording model for local inference…' });
      engineBase = data.base;
      pendingEngine = load(data.base, data.wasmBase ?? new URL('../verifier/', data.base).href);
    }
    const engine = await pendingEngine;
    emit({ kind: 'status', id: data.id, text: 'Checking the wording locally…' });
    emit({ kind: 'result', id: data.id, result: await check(engine, data.text) });
  } catch (error) {
    pendingEngine = undefined;
    emit({ kind: 'error', id: data.id, text: error instanceof Error ? error.message : 'The wording check could not finish.' });
  } finally { busy = false; }
});
