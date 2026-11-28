// Local-only acceptance fixture. Never included in the deployed Worker.
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {serveBrowserWorkspace} from '../infra/cloudflare/src/browser-workspace.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));
const assets=process.env.DOXA_QA_ASSETS;
if(!assets)throw Error('Set DOXA_QA_ASSETS to the pinned local model fixture directory');
const server=createServer(async(req,res)=>{
 try{
  const request=new Request('https://doxa.tacitus.me'+req.url,{headers:req.headers,method:req.method});
  const account=(req.headers.cookie||'').match(/(?:^|; )qa-account=([ab]{64})(?:;|$)/)?.[1];
  const response=await serveBrowserWorkspace(request,{}, {
   principalCheck:async()=>account?{account}:null,
   fetchImpl:async url=>{
    const parsed=new URL(url);
    let file;
    if(parsed.hostname==='raw.githubusercontent.com')file=root+'browser-workspace/client/'+parsed.pathname.split('/browser-workspace/client/')[1];
    else file=assets+'/'+parsed.pathname.split('/dist/')[1];
    try{return new Response(await readFile(file));}catch{return new Response('Fixture asset not present',{status:404});}
   },
  });
  res.writeHead(response.status,Object.fromEntries(response.headers));res.end(Buffer.from(await response.arrayBuffer()));
 }catch(error){res.writeHead(500);res.end(String(error));}
});
server.listen(8867,'127.0.0.1',()=>console.log('DOXA isolated account fixture at http://127.0.0.1:8867'));
