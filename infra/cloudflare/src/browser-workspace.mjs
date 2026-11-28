import {browserPrincipal} from './browser-auth.mjs';
import {localAssets, upstreamAssets, release} from './browser-assets.mjs';

const types = {html:'text/html; charset=utf-8',js:'text/javascript; charset=utf-8',mjs:'text/javascript; charset=utf-8',css:'text/css; charset=utf-8',json:'application/json',svg:'image/svg+xml',webp:'image/webp',pdf:'application/pdf',wasm:'application/wasm',gz:'application/gzip'};
const policy = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self' https://huggingface.co https://*.huggingface.co https://*.hf.co https://*.xethub.hf.co; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'";

export function secureBrowserResponse(response, request) {
  const headers = new Headers(response.headers);
  headers.set('Cache-Control', 'private, no-store');
  headers.set('Content-Security-Policy', policy);
  headers.set('X-Content-Type-Options', 'nosniff');
  headers.set('Referrer-Policy', 'no-referrer');
  headers.set('X-Frame-Options', 'DENY');
  headers.set('X-Robots-Tag', 'noindex, nofollow');
  headers.set('Permissions-Policy', 'camera=(), microphone=(), geolocation=()');
  headers.set('Strict-Transport-Security', 'max-age=31536000');
  headers.set('X-Tacitus-Product', 'doxa-authenticated-browser');
  headers.set('X-Doxa-Source-Revision', release.source_revision);
  headers.set('X-Doxa-Client-Revision', release.client_revision);
  headers.delete('set-cookie');
  return new Response(request.method === 'HEAD' ? null : response.body, {status:response.status, headers});
}

export function accountMarkup(html, principal) {
  const meta = `<meta name="doxa-account" content="${principal.account}">`;
  const navigation = '<div class="wrap"><p class="muted">Signed-in DOXA workspace · Projects are saved in this browser for your account. <a href="/">Document review</a> · <a href="/scorecard/">Evidence scorecard</a> · <a href="/cdn-cgi/access/logout">Sign out</a></p></div>';
  return html.replace('</head>', meta + '</head>').replace(/<body([^>]*)>/, '$&' + navigation);
}

export async function serveBrowserWorkspace(request, env, {fetchImpl=fetch, principalCheck=browserPrincipal}={}) {
  const url = new URL(request.url);
  if (url.hostname !== 'doxa.tacitus.me') return new Response('Unknown product hostname', {status:404});
  const principal = await principalCheck(request, env);
  const secure = response => secureBrowserResponse(response, request);
  if (!principal) return secure(Response.json({error:'sign_in_required'}, {status:401}));
  if (!['GET', 'HEAD'].includes(request.method)) return secure(new Response(null, {status:405,headers:{Allow:'GET, HEAD'}}));
  let path;
  try { path=decodeURIComponent(url.pathname); } catch { return secure(new Response('Invalid path', {status:400})); }
  if (/[\\\u0000-\u001f]/.test(path) || path.includes('..') || path.includes('//')) return secure(new Response('Invalid path', {status:400}));
  if (path === '/api/session') return secure(Response.json({authenticated:true,storage:'account-scoped browser projects',cloud_documents:false}));
  if (path === '/healthz') return secure(Response.json({status:'ok',authentication:'required'}));
  if (path === '/') path='/index.html';
  if (path === '/scorecard') return secure(new Response(null,{status:308,headers:{Location:'/scorecard/'}}));
  if (path === '/scorecard/') path='/scorecard/index.html';
  const local=localAssets[path];
  if (local) {
    const bytes=Uint8Array.from(atob(local.data), c=>c.charCodeAt(0));
    const html=path.endsWith('.html');
    const body=html?accountMarkup(new TextDecoder().decode(bytes),principal):bytes;
    return secure(new Response(body,{headers:{'Content-Type':local.type}}));
  }
  const source=upstreamAssets[path];
  if (!source) return secure(new Response('Product route not found', {status:404}));
  try {
    // Source is a release-pinned, code-generated allowlist. Never forward user
    // identity, cookies, authorization, arbitrary destinations or query strings.
    const headers=new Headers();
    const range=request.headers.get('range');
    if (range && /^bytes=\d+-\d*$/.test(range)) headers.set('Range',range);
    const response=await fetchImpl(source,{headers,redirect:'follow',signal:AbortSignal.timeout(45000)});
    if (![200,206].includes(response.status)) return secure(new Response('Product asset temporarily unavailable', {status:502}));
    const type=types[path.split('.').pop()] || 'application/octet-stream';
    if (path.endsWith('.html')) return secure(new Response(accountMarkup(await response.text(),principal),{headers:{'Content-Type':type}}));
    const out=new Headers({'Content-Type':type});
    for(const name of ['Content-Range','Accept-Ranges'])if(response.headers.has(name))out.set(name,response.headers.get(name));
    return secure(new Response(response.body,{status:response.status,headers:out}));
  } catch { return secure(new Response('Product asset temporarily unavailable. Try again.', {status:502})); }
}

export default {fetch(request,env){return serveBrowserWorkspace(request,env);}};
