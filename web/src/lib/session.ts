// Server-only session helpers. NEVER import this from a Client Component —
// `next/headers` is server-only. The session token lives in an httpOnly cookie
// (set by the /api/auth/login route handler), unreadable by browser JS.
import { cookies } from "next/headers";
import { SESSION_COOKIE } from "./constants";
import { apiBase } from "./backend";

export { SESSION_COOKIE, apiBase };

// Async since Next 15: cookies() returns a Promise there, so every caller must
// await this.
export async function getToken(): Promise<string | undefined> {
  return (await cookies()).get(SESSION_COOKIE)?.value;
}

/**
 * The signed-in username, read out of the token's `sub` claim — for DISPLAY
 * ONLY.
 *
 * The signature is deliberately NOT checked here, and this value must never
 * decide what anyone is allowed to see. It cannot: every piece of data on
 * every page comes from FastAPI, which verifies the same token on each request
 * and scopes the rows to the `sub` IT read. A forged cookie changes the name in
 * the corner of the header and nothing else.
 *
 * Decoded rather than fetched from /api/auth/me because this runs in the app
 * layout, i.e. on every single page render — a network round trip to learn a
 * name we are already holding would be latency on every navigation.
 */
export async function getSessionUsername(): Promise<string | null> {
  const token = await getToken();
  if (!token) return null;
  const payload = token.split(".")[1];
  if (!payload) return null;
  try {
    // base64url -> base64. Buffer tolerates missing padding.
    const json = Buffer.from(payload.replace(/-/g, "+").replace(/_/g, "/"),
      "base64").toString("utf8");
    const sub = (JSON.parse(json) as { sub?: unknown }).sub;
    return typeof sub === "string" && sub ? sub : null;
  } catch {
    // A malformed cookie is not worth an error page — the header simply falls
    // back to the generic label.
    return null;
  }
}
