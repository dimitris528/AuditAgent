// Server-only session helpers. NEVER import this from a Client Component —
// `next/headers` is server-only. The session token lives in an httpOnly cookie
// (set by the /api/auth/login route handler), unreadable by browser JS.
import { cookies } from "next/headers";
import { SESSION_COOKIE, apiBase } from "./constants";

export { SESSION_COOKIE };
export const API_BASE_URL = apiBase();

export function getToken(): string | undefined {
  return cookies().get(SESSION_COOKIE)?.value;
}
