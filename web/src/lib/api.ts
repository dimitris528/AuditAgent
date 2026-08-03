// Client-side fetchers. These hit SAME-ORIGIN Next.js route handlers, which
// read the httpOnly session cookie and forward to FastAPI with the Bearer
// token — the browser never sees the token. (No server-only imports here, so
// this module is safe to import from Client Components.)
import { ApiError } from "./errors";

export { ApiError };

export interface NewTransaction {
  client: string;
  amount: number;
  type: string; // "Έσοδο" | "Έξοδο" | "Χρεωστούμενο"
  vat_rate: number;
  date?: string;
  description?: string;
}

export async function createTransaction(
  body: NewTransaction,
): Promise<{ ok: boolean; id?: string }> {
  const res = await fetch(`/api/transactions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(
      data?.detail || data?.error || `Create failed (${res.status})`,
      res.status,
    );
  }
  return data;
}

export async function login(
  username: string,
  password: string,
): Promise<{ ok: boolean; username?: string; demo?: boolean }> {
  const res = await fetch(`/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(data?.error || `Login failed (${res.status})`, res.status);
  }
  return data;
}

/** Minimum enforced server-side too — this only mirrors it for the form. */
export const MIN_PASSWORD_LENGTH = 8;

export async function register(
  username: string,
  email: string,
  password: string,
): Promise<{ ok: boolean; username?: string; email?: string }> {
  const res = await fetch(`/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, email, password }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(
      data?.error || `Registration failed (${res.status})`,
      res.status,
    );
  }
  return data;
}

export async function logout(): Promise<void> {
  await fetch(`/api/auth/logout`, { method: "POST" });
}
