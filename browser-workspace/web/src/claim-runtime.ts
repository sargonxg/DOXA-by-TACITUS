import { Tokenizer } from '@huggingface/tokenizers';
import { env, InferenceSession, Tensor } from 'onnxruntime-web/wasm';

const source = 'https://huggingface.co/cross-encoder/nli-deberta-v3-small/resolve/fa2804872c3b4bd748f38c0185cc85775361e735/';
const files = {
  tokenizer: ['tokenizer.json','5124ef2ead1a10a717703bc436de7f353da76d6340e4587719b42b1693707964'],
  weights: ['onnx/model_quint8_avx2.onnx','03c2221313dc0c3eac9cec1f746d1319d33f2c2901fcce1c0f08f4daac9b6dae'],
} as const;

async function verified(file: readonly [string,string]) {
  const response = await fetch(source+file[0],{credentials:'omit',referrerPolicy:'no-referrer'});
  if (!response.ok) throw new Error('The pinned model download failed.');
  const bytes = await response.arrayBuffer();
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),
    b=>b.toString(16).padStart(2,'0')).join('');
  if (file[1]!==hash) throw new Error('The download did not match the pinned model.');
  return bytes;
}

export async function startVerifier(base: string) {
  env.wasm.numThreads=1;
  env.wasm.wasmPaths={
    mjs:new URL('ort-wasm-simd-threaded.mjs',base),
    wasm:new URL('ort-wasm-simd-threaded.wasm',base),
  };
  const [tokenizerBytes,modelBytes] = await Promise.all([verified(files.tokenizer),verified(files.weights)]);
  const tokenizer = new Tokenizer(JSON.parse(new TextDecoder().decode(tokenizerBytes)),{});
  const engine = await InferenceSession.create(modelBytes,{executionProviders:['wasm'],graphOptimizationLevel:'all'});
  return async (passage: string,claim: string) => {
    const input=tokenizer.encode(passage,{text_pair:claim,return_token_type_ids:true});
    const length=input.ids.length;
    if (length>512) return undefined;
    const values: Record<string,number[]> = {
      input_ids:input.ids,attention_mask:input.attention_mask,token_type_ids:input.token_type_ids,
    };
    const tensors=Object.fromEntries(engine.inputNames.map(name=>[
      name,new Tensor('int64',BigInt64Array.from(values[name],BigInt),[1,length]),
    ]));
    const result=await engine.run(tensors);
    const data=result[engine.outputNames[0]].data;
    return [Number(data[1]),Number(data[0]),Number(data[2])];
  };
}
