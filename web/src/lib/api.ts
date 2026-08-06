// Client-side fetchers. These hit SAME-ORIGIN Next.js route handlers, which
// read the httpOnly session cookie and forward to FastAPI with the Bearer
// token — the browser never sees the token. (No server-only imports here, so
// this module is safe to import from Client Components.)
import { ApiError, DuplicateError } from "./errors";
import type {
  ClientDetail,
  ClientDetailPayload,
  DebtPaymentRow,
  DuplicateClient,
  DuplicateTransaction,
  ScanResult,
  SettlementResult,
} from "./types";

export { ApiError, DuplicateError };

export interface NewTransaction {
  client: string;
  /** Set when an existing client was picked, so a near-duplicate name cannot
   *  mis-file the row. Omitted when creating a client inline by name. */
  client_id?: number;
  amount: number;
  /** Which side of the VAT line `amount` is on. The server converts, so both
   *  entry modes round identically. */
  amount_basis?: "gross" | "net";
  type: string; // "Έσοδο" | "Έξοδο" | "Χρεωστούμενο"
  /** Τύπος παραστατικού (see DocTypeInfo). */
  doc_type?: string;
  /** Χρεωστούμενα only — when payment falls due. */
  due_date?: string;
  vat_rate: number;
  /** The ΦΠΑ printed on the document, when a scan read one. Stored verbatim in
   *  preference to the figure derived from `amount` and `vat_rate`. */
  vat_amount?: number;
  date?: string;
  description?: string;
  doc_number?: string;
  counterparty_afm?: string;
  /** Save despite a duplicate — set only after the user has seen it. */
  force?: boolean;
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
    // A duplicate arrives as a 409 whose detail carries the row it collided
    // with, so the form can show it and offer to save anyway.
    const detail = data?.detail;
    if (res.status === 409 && detail?.duplicate) {
      throw new DuplicateError(
        detail.message || "Το παραστατικό υπάρχει ήδη.",
        detail.duplicate as DuplicateTransaction,
      );
    }
    throw new ApiError(
      (typeof detail === "string" ? detail : detail?.message) ||
        data?.error ||
        `Create failed (${res.status})`,
      res.status,
    );
  }
  return data;
}

/** Look for the same invoice without writing anything. */
export async function checkTransactionDuplicate(body: {
  doc_number?: string;
  counterparty_afm?: string;
  client?: string;
  date?: string;
}): Promise<{ duplicate: DuplicateTransaction | null }> {
  const res = await fetch(`/api/transactions/check-duplicate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return unwrap(res, "Αποτυχία ελέγχου διπλοεγγραφής");
}

export async function checkClientDuplicate(body: {
  name?: string;
  afm?: string;
  exclude_id?: number;
}): Promise<{ duplicate: DuplicateClient | null }> {
  const res = await fetch(`/api/clients/check-duplicate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return unwrap(res, "Αποτυχία ελέγχου διπλοεγγραφής");
}

/**
 * Εξόφληση χρέους. Omit `amount` to pay off the whole remaining balance —
 * the server knows it better than the browser does.
 */
export async function settleDebt(
  id: string,
  body: { amount?: number; vat_rate?: number; date?: string; note?: string },
): Promise<SettlementResult> {
  const res = await fetch(`/api/transactions/${id}/settle`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return unwrap(res, "Αποτυχία εξόφλησης");
}

export async function getDebtPayments(
  id: string,
): Promise<{ id: string; paid: number; remaining: number; payments: DebtPaymentRow[] }> {
  const res = await fetch(`/api/transactions/${id}/payments`, {
    cache: "no-store",
  });
  return unwrap(res, "Αποτυχία φόρτωσης ιστορικού");
}

/**
 * Send a PDF / photo of an invoice for OCR. Writes nothing — the extraction
 * comes back for the user to review in the form.
 */
export async function scanDocument(file: File): Promise<ScanResult> {
  const form = new FormData();
  form.append("file", file, file.name || "document");
  const res = await fetch(`/api/documents/scan`, {
    method: "POST",
    body: form,
  });
  return unwrap(res, "Αποτυχία σάρωσης παραστατικού");
}

export async function login(
  username: string,
  password: string,
): Promise<{ ok: boolean; username?: string }> {
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

/**
 * Free-trial length, for the signup copy only.
 *
 * The trial is granted and timed entirely by the backend
 * (server/subscription.py); this constant never decides anything, so a
 * deployment that overrides TRIAL_DAYS gets the right trial and slightly stale
 * marketing copy — not a mismatch that matters. Every page that shows a live
 * countdown reads `trial_days` off the API instead.
 */
export const TRIAL_DAYS = 14;

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

/**
 * Open a Stripe Checkout session and return the URL to send the browser to.
 *
 * The redirect is the CALLER's job: `window.location.assign(url)` rather than
 * a router push, because the destination is Stripe's own domain and the Next
 * router would refuse it.
 */
export async function startCheckout(): Promise<{ url: string }> {
  const res = await fetch(`/api/billing/checkout`, { method: "POST" });
  return unwrap(res, "Αποτυχία έναρξης πληρωμής");
}

/**
 * Open Stripe's hosted customer portal — card, invoices, cancellation.
 *
 * Same contract as startCheckout: the URL is single-use and short-lived, so it
 * is fetched on the click rather than rendered into the page, and the caller
 * navigates to it with `window.location.assign`.
 */
export async function openBillingPortal(): Promise<{ url: string }> {
  const res = await fetch(`/api/billing/portal`, { method: "POST" });
  return unwrap(res, "Αποτυχία ανοίγματος διαχείρισης συνδρομής");
}

/**
 * Schedule the subscription to end when the paid period does.
 *
 * Returns the effective date rather than a bare ok: the account stays active
 * until then, and the one thing the user needs told afterwards is when access
 * actually stops.
 */
export async function cancelSubscription(): Promise<{
  pending_cancellation: boolean;
  cancel_at: string | null;
}> {
  const res = await fetch(`/api/billing/cancel`, { method: "POST" });
  return unwrap(res, "Αποτυχία ακύρωσης συνδρομής");
}

/**
 * Start a password reset.
 *
 * Always resolves for a well-formed address, whether or not an account exists —
 * the backend answers identically on purpose, so the UI must not imply
 * otherwise in its success message either.
 */
export async function forgotPassword(email: string): Promise<{ ok: boolean }> {
  const res = await fetch(`/api/auth/forgot-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  return unwrap(res, "Αποτυχία αποστολής συνδέσμου");
}

/** Spend a reset token and set the new password. */
export async function resetPassword(
  token: string,
  password: string,
): Promise<{ ok: boolean; username?: string }> {
  const res = await fetch(`/api/auth/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, password }),
  });
  return unwrap(res, "Αποτυχία επαναφοράς κωδικού");
}
