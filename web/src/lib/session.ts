// Server-only session helpers. NEVER import this from a Client Component —
// `next/headers` is server-only. The session token lives in an httpOnly cookie
// (set by the /api/auth/login route handler), unreadable by browser JS.
import { cookies } from "next/headers";
import { SESSION_COOKIE, apiBase } from "./constants";

export { SESSION_COOKIE };
export const API_BASE_URL = apiBase();

// Async since Next 15: cookies() returns a Promise there, so every caller must
// await this.
export async function getToken(): Promise<string | undefined> {
  return (await cookies()).get(SESSION_COOKIE)?.value;
}
