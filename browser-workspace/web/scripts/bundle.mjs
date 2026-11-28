import { mkdir, copyFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const web = resolve(import.meta.dirname,'..');
const model = resolve(web,'../release_staging/model');
const publicDir = resolve(web,'public');

async function copyGroup(folder,files) {
  const target=resolve(publicDir,folder);
  await mkdir(target,{recursive:true});
  await Promise.all(files.map(([source,name])=>copyFile(resolve(web,source),resolve(target,name))));
}

const modelFiles=['model_manifest.json','feature_spec.json','vocabulary.json','idf.f32','coefficients.f32','intercepts.f32'];
const verifierFiles=['adapter.json','selector.json','ORT-LICENSE','THIRD_PARTY_NOTICES.txt'];
const wasmFiles=['ort-wasm-simd-threaded.wasm','ort-wasm-simd-threaded.mjs'];
const ocrCoreFiles=['tesseract-core-lstm.wasm.js','tesseract-core-lstm.wasm','tesseract-core-simd-lstm.wasm.js','tesseract-core-simd-lstm.wasm'];
await Promise.all([
  copyGroup('model',modelFiles.map(file=>[resolve(model,file),file])),
  copyGroup('pdf',[
    ['node_modules/pdfjs-dist/build/pdf.worker.min.mjs','pdf.worker.min.mjs'],
    ['node_modules/pdfjs-dist/LICENSE','LICENSE'],
  ]),
  copyGroup('verifier',[
    ...verifierFiles.map(file=>[`data/verifier/${file}`,file]),
    ...wasmFiles.map(file=>[`node_modules/onnxruntime-web/dist/${file}`,file]),
    ['node_modules/@huggingface/tokenizers/LICENSE','TOKENIZERS-LICENSE'],
  ]),
  copyGroup('ocr',[
    ['node_modules/tesseract.js/dist/worker.min.js','worker.min.js'],
    ['node_modules/tesseract.js/LICENSE.md','TESSERACT-LICENSE.md'],
    ['node_modules/tesseract.js-core/LICENSE','CORE-LICENSE'],
    ['node_modules/@tesseract.js-data/eng/README.md','ENGLISH-DATA.md'],
  ]),
  copyGroup('ocr/core',ocrCoreFiles.map(file=>['node_modules/tesseract.js-core/'+file,file])),
  copyGroup('ocr/lang',[
    ['node_modules/@tesseract.js-data/eng/4.0.0_best_int/eng.traineddata.gz','eng.traineddata.gz'],
  ]),
  writeFile(resolve(publicDir,'release-revisions.json'),'{}\n'),
]);
