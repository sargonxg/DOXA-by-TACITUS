const encoder = new TextEncoder();
let cache;
let pending;

const decode = part => {
  if (!/^[A-Za-z0-9_-]+$/.test(part)) throw Error('Invalid token');
  return Uint8Array.from(atob(part.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
};

/** Access is checked at the edge and again in the Worker. Email headers are never credentials. */
export async function browserPrincipal(request, env, fetchImpl = fetch, now = Date.now()) {
  const team = env.CF_ACCESS_TEAM_DOMAIN, audience = env.CF_ACCESS_AUD;
  if (!/^[a-z0-9-]+\.cloudflareaccess\.com$/.test(team || '') || !/^[a-f0-9]{64}$/.test(audience || '')) return null;
  const token = request.headers.get('cf-access-jwt-assertion');
  if (!token || token.length > 16384) return null;
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    const header = JSON.parse(new TextDecoder().decode(decode(parts[0])));
    const claims = JSON.parse(new TextDecoder().decode(decode(parts[1])));
    if (header.alg !== 'RS256' || typeof header.kid !== 'string' || header.kid.length > 200 ||
        claims.iss !== `https://${team}` || !(Array.isArray(claims.aud) ? claims.aud : [claims.aud]).includes(audience) ||
        !Number.isInteger(claims.exp) || claims.exp <= Math.floor(now / 1000) ||
        !Number.isInteger(claims.iat) || claims.iat > Math.floor(now / 1000) + 60 ||
        typeof claims.sub !== 'string' || !claims.sub.length || claims.sub.length > 200 ||
        typeof claims.email !== 'string' || !/^[^\s@]+@[^\s@]+$/.test(claims.email)) return null;
    if (!cache || cache.team !== team || cache.expires <= now || (now - cache.fetched > 60000 && !cache.keys.some(key => key.kid === header.kid))) {
      if (!pending) pending = (async () => {
        const response = await fetchImpl(`https://${team}/cdn-cgi/access/certs`, { redirect: 'error', signal: AbortSignal.timeout(5000) });
        if (!response.ok) throw Error('Identity verification unavailable');
        const body = await response.json();
        if (!Array.isArray(body.keys) || !body.keys.length || body.keys.length > 30) throw Error('Invalid identity keys');
        cache = { team, keys: body.keys, fetched: now, expires: now + 300000 };
      })().finally(() => { pending = undefined; });
      await pending;
    }
    const key = cache.keys.find(key => key.kid === header.kid && key.kty === 'RSA');
    if (!key) return null;
    const imported = await crypto.subtle.importKey('jwk', key, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify']);
    if (!await crypto.subtle.verify('RSASSA-PKCS1-v1_5', imported, decode(parts[2]), encoder.encode(parts[0] + '.' + parts[1]))) return null;
    const digest = await crypto.subtle.digest('SHA-256', encoder.encode(claims.iss + '\0' + claims.sub));
    return { account: [...new Uint8Array(digest)].map(v => v.toString(16).padStart(2, '0')).join('') };
  } catch { return null; }
}
