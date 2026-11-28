import { cp, mkdir, readdir, rm } from 'node:fs/promises';
import { relative, resolve } from 'node:path';
const web = resolve(import.meta.dirname,'..');
const target = resolve(web,'../release_staging/space/dist');
await mkdir(resolve(target,'..'), {recursive:true});
await rm(target,{recursive:true,force:true});
const dist=resolve(web,'dist');
await cp(dist,target,{recursive:true,filter:path=>{
  const name=relative(dist,path);
  return name!=='generated' && name!=='brand/console.css';
}});
// The runtime is explicitly loaded from ./verifier/. Drop Vite's unused
// hashed fallback so static deployment ships one copy of the WASM binary.
for (const file of await readdir(resolve(target,'assets'))) {
  if (/^ort-wasm-simd-threaded-[\w-]+\.wasm$/.test(file)) await rm(resolve(target,'assets',file));
}
