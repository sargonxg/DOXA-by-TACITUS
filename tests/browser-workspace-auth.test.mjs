import {test, before} from 'node:test';
import assert from 'node:assert/strict';
import {browserPrincipal} from '../infra/cloudflare/src/browser-auth.mjs';
import {serveBrowserWorkspace} from '../infra/cloudflare/src/browser-workspace.mjs';

let keys, publicKey;
const now=1800000000000;
const audience='a'.repeat(64);
const env={CF_ACCESS_TEAM_DOMAIN:'test-team.cloudflareaccess.com',CF_ACCESS_AUD:audience};
const b64=bytes=>Buffer.from(bytes).toString('base64url');
const json=value=>b64(JSON.stringify(value));
before(async()=>{keys=await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']);publicKey=await crypto.subtle.exportKey('jwk',keys.publicKey);});
async function signed(change={},headerChange={}){
  const claims={iss:'https://'+env.CF_ACCESS_TEAM_DOMAIN,aud:[audience],sub:'account-a',email:'fixture@example.invalid',iat:now/1000-10,exp:now/1000+1000,...change};
  const body=json({alg:'RS256',kid:'test',...headerChange})+'.'+json(claims);
  return body+'.'+b64(await crypto.subtle.sign('RSASSA-PKCS1-v1_5',keys.privateKey,new TextEncoder().encode(body)));
}
const fetchKeys=async()=>Response.json({keys:[{...publicKey,kid:'test',alg:'RS256'}]});
const request=(path='/',headers={},method='GET')=>new Request('https://doxa.tacitus.me'+path,{headers,method});
const check=async(change={},header={})=>browserPrincipal(request('/',{'cf-access-jwt-assertion':await signed(change,header)}),env,fetchKeys,now);

test('a signed email identity produces a stable account namespace without exposing the address',async()=>{
 const a=await check(),b=await check();assert.match(a.account,/^[a-f0-9]{64}$/);assert.deepEqual(a,b);assert.notEqual((await check({sub:'account-b'})).account,a.account);assert.equal(Object.keys(a).length,1);
});
for(const [name,change]of [['expired',{exp:now/1000}],['wrong issuer',{iss:'https://attacker.invalid'}],['wrong audience',{aud:['other']}],['future issue',{iat:now/1000+120}],['empty account',{sub:''}],['missing email',{email:null}]])test('rejects '+name,async()=>assert.equal(await check(change),null));
test('rejects forged email headers, altered signatures and unsupported algorithms',async()=>{
 assert.equal(await browserPrincipal(request('/',{'cf-access-authenticated-user-email':'fixture@example.invalid'}),env,fetchKeys,now),null);
 assert.equal(await check({}, {alg:'none'}),null);
 assert.equal(await check({}, {kid:'other'}),null);
 const token=await signed(),broken=token.slice(0,token.lastIndexOf('.')+1)+'A'.repeat(342);
 assert.equal(await browserPrincipal(request('/',{'cf-access-jwt-assertion':broken}),env,fetchKeys,now),null);
});
test('fails closed without the configured Access application audience',async()=>{
 assert.equal(await browserPrincipal(request('/',{'cf-access-jwt-assertion':await signed()}),{...env,CF_ACCESS_AUD:''},fetchKeys,now),null);
});
const principalCheck=async()=>({account:'b'.repeat(64)});
test('unauthenticated requests cannot reach the UI, bundles, scorecard, model assets or APIs',async()=>{
 let calls=0;const fetchImpl=async()=>{calls++;throw Error('Unexpected upstream');};
 for(const path of ['/','/index.html','/assets/index.js','/model/coefficients.f32','/scorecard/','/api/session']){
  const response=await serveBrowserWorkspace(request(path),env,{fetchImpl,principalCheck:async()=>null});
  assert.equal(response.status,401,path);assert.equal(response.headers.get('cache-control'),'private, no-store');
  assert.equal((await response.json()).error,'sign_in_required');
 }
 assert.equal(calls,0);
});
test('authenticated entry injects an account-scoped storage identity into the preserved product',async()=>{
 const response=await serveBrowserWorkspace(request(),env,{principalCheck});const html=await response.text();
 assert.equal(response.status,200);assert.match(html,/Audit political bias in documents/);assert.match(html,/name="doxa-account" content="b{64}"/);
 assert.match(html,/\/cdn-cgi\/access\/logout/);assert.match(response.headers.get('content-security-policy'),/frame-ancestors 'none'/);
 assert.equal(response.headers.get('x-robots-tag'),'noindex, nofollow');
});
test('unknown hosts, paths and write methods fail closed',async()=>{
 assert.equal((await serveBrowserWorkspace(new Request('https://other.invalid/'),env,{principalCheck})).status,404);
 assert.equal((await serveBrowserWorkspace(request('/not-a-tool'),env,{principalCheck})).status,404);
 assert.equal((await serveBrowserWorkspace(request('/api/session',{},'POST'),env,{principalCheck})).status,405);
});
test('pinned asset fetches discard user credentials, query strings and unapproved destinations',async()=>{
 let seen;const fetchImpl=async(url,options)=>{seen={url,options};return new Response('model-bytes');};
 const response=await serveBrowserWorkspace(request('/model/coefficients.f32?url=https://attacker.invalid',{cookie:'private=value',authorization:'Bearer credential'}),env,{principalCheck,fetchImpl});
 assert.equal(response.status,200);assert.match(seen.url,/\/resolve\/b203180eecdfea1ce399a587e1a79bd21816d868\/dist\/model\/coefficients.f32$/);
 assert.equal(seen.options.headers.get('authorization'),null);assert.equal(seen.options.headers.get('cookie'),null);
 assert.equal(await response.text(),'model-bytes');
});
test('upstream failures show a recoverable error without masquerading as a working app',async()=>{
 const response=await serveBrowserWorkspace(request('/model/coefficients.f32'),env,{principalCheck,fetchImpl:async()=>new Response('error',{status:500})});
 assert.equal(response.status,502);assert.equal(response.headers.get('cache-control'),'private, no-store');
});
test('HEAD never includes a product body',async()=>{
 const response=await serveBrowserWorkspace(request('/',{},'HEAD'),env,{principalCheck});assert.equal(response.status,200);assert.equal(await response.text(),'');
});
