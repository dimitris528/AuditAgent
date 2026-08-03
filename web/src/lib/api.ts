// Client-side fetchers. These hit SAME-ORIGIN Next.js route handlers, which
// read the httpOnly session cookie and forward to FastAPI with the Bearer
// token — the browser never sees the token. (No server-only imports here, so
// this module is safe to import from Client Components.)
import { ApiError } from "./errors";
import type { ClientDetail, ClientDetailPayload } from "./types";

export { ApiError };

export interface NewTransaction {
  client: string;
  /** Set when an existing client was picked, so a near-duplicate name cannot
   *  mis-file the row. Omitted when creating a client inline by name. */
  client_id?: number;
  amount: number;
  type: string; // "Έσοδο" | "Έξοδο" | "Χρεωστούμενο"
  vat_rate: number;
  date?: string;
  description?: string;
}

async function unwrap<T>(res: Response, fallback: string): Promise<T> {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(
      (data as { error?: string })?.error || `${fallback} (${res.status})`,
      res.status,
    );
  }
  return data as T;
}

export async function listClients(
  includeArchived = true,
): Promise<{ clients: ClientDetail[] }> {
  const res = await fetch(`/api/clients?include_archived=${includeArchived}`, {
    cache: "no-store",
  });
  return unwrap(res, "Αποτυχία φόρτωσης πελατών");
}

export async function createClient(body: {
  name: string;
  afm?: string;
  contact?: string;
  notes?: string;
}): Promise<ClientDetail> {
  const res = await fetch(`/api/clients`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return unwrap(res, "Αποτυχία δημιουργίας πελάτη");
}

export async function getClientDetail(
  id: number,
  period?: { year?: number | null; quarter?: number | null; month?: number | null },
): Promise<ClientDetailPayload> {
  const qs = new URLSearchParams();
  if (period?.year) qs.set("year", String(period.year));
  if (period?.quarter) qs.set("quarter", String(period.quarter));
  if (period?.month) qs.set("month", String(period.month));
  const res = await fetch(
    `/api/clients/${id}${qs.toString() ? `?${qs}` : ""}`,
    { cache: "no-store" },
  );
  return unwrap(res, "Αποτυχία φόρτωσης πελάτη");
}

/** Partial update — only the keys sent are changed server-side. */
export async function updateClient(
  id: number,
  body: Partial<{
    name: string;
    afm: string;
    contact: string;
    notes: string;
    archived: boolean;
  }>,
): Promise<ClientDetail> {
  const res = await fetch(`/api/clients/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return unwrap(res, "Αποτυχία αποθήκευσης");
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
