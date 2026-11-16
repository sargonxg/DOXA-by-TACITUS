import { createRemoteJWKSet, jwtVerify } from "jose";

export interface AuthConfig {
  DOXA_SERVICE_TOKEN?: string;
  CF_ACCESS_TEAM_DOMAIN?: string;
  CF_ACCESS_AUD?: string;
}

export async function equalSecret(provided: string, expected: string): Promise<boolean> {
  const bytes = new TextEncoder();
  const [a, b] = await Promise.all([
    crypto.subtle.digest("SHA-256", bytes.encode(provided)),
    crypto.subtle.digest("SHA-256", bytes.encode(expected)),
  ]);
  return crypto.subtle.timingSafeEqual(a, b);
}

export async function isService(request: Request, env: AuthConfig): Promise<boolean> {
  if (!env.DOXA_SERVICE_TOKEN || env.DOXA_SERVICE_TOKEN.length < 32) return false;
  const bearer = request.headers.get("authorization")?.match(/^Bearer (.+)$/i)?.[1];
  return equalSecret(request.headers.get("x-doxa-service-token") ?? bearer ?? "", env.DOXA_SERVICE_TOKEN);
}

export async function authenticate(request: Request, env: AuthConfig): Promise<boolean> {
  if (await isService(request, env)) return true;
  const team = env.CF_ACCESS_TEAM_DOMAIN;
  const audience = env.CF_ACCESS_AUD;
  if (!team || !audience || !/^[a-z0-9-]+\.cloudflareaccess\.com$/.test(team) || audience.startsWith("REPLACE_")) return false;
  const jwt = request.headers.get("cf-access-jwt-assertion");
  if (!jwt) return false;
  try {
    const issuer = `https://${team}`;
    const jwks = createRemoteJWKSet(new URL(`${issuer}/cdn-cgi/access/certs`));
    await jwtVerify(jwt, jwks, { issuer, audience, algorithms: ["RS256"] });
    return true;
  } catch {
    return false;
  }
}
