// Server-side fetchers (used by Server Components). Attaches the tenant's
// Bearer token from the httpOnly cookie so the FastAPI backend scopes data.
import { getToken } from "./session";
import { BackendUnavailableError, backendFetch } from "./backend";
import { ApiError } from "./errors";
import type {
  BillingStatus,
  ClientDetailPayload,
  DashboardData,
} from "./types";

export interface PeriodQuery {
  year?: number | null;
  quarter?: number | null;
  month?: number | null;
}

export async function getDashboard(period?: PeriodQuery): Promise<DashboardData> {
  const token = await getToken();
  if (!token) throw new ApiError("unauthenticated", 401);
  const qs = new URLSearchParams();
  if (period?.year) qs.set("year", String(period.year));
  if (period?.quarter) qs.set("quarter", String(period.quarter));
  if (period?.month) qs.set("month", String(period.month));
  const res = await authed(
    `/api/dashboard${qs.toString() ? `?${qs}` : ""}`,
    token,
  );
  if (!res.ok) {
    throw new ApiError(await detailOf(res, `Dashboard request failed (${res.status})`),
      res.status);
  }
  return (await res.json()) as DashboardData;
}

/**
 * One authenticated GET, with the cold-start retry.
 *
 * Translates an unreachable backend into ApiError(503) so every caller can
 * treat it like any other failure — and so the page can tell "asleep or
 * misconfigured" (503) apart from "your session expired" (401), which is the
 * distinction that decides whether to redirect to /login or show the outage.
 */
async function authed(path: string, token: string): Promise<Response> {
  try {
    return await backendFetch(path, {
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch (err) {
    if (err instanceof BackendUnavailableError) {
      throw new ApiError(err.message, 503);
    }
    throw err;
  }
}

/**
 * Current subscription for the signed-in tenant.
 *
 * Deliberately its own request rather than something read off the dashboard:
 * /billing is exactly the page a lapsed tenant is sent to, and it has to render
 * for someone the dashboard would bounce.
 */
export async function getBillingStatus(): Promise<BillingStatus> {
  const token = await getToken();
  if (!token) throw new ApiError("unauthenticated", 401);
  const res = await authed("/api/v1/billing/status", token);
  if (!res.ok) {
    throw new ApiError(await detailOf(res, `Billing request failed (${res.status})`),
      res.status);
  }
  return (await res.json()) as BillingStatus;
}

/**
 * One client with its transactions and settlement history — the statement.
 *
 * The same endpoint the drawer uses. Fetched SERVER-side here because the
 * statement page is printed: a client-rendered page can reach the print dialog
 * before its data has loaded, and print an empty statement.
 */
export async function getClientStatement(
  id: number,
  period?: PeriodQuery,
): Promise<ClientDetailPayload> {
  const token = await getToken();
  if (!token) throw new ApiError("unauthenticated", 401);
  const qs = new URLSearchParams();
  if (period?.year) qs.set("year", String(period.year));
  if (period?.quarter) qs.set("quarter", String(period.quarter));
  if (period?.month) qs.set("month", String(period.month));
  const res = await authed(
    `/api/v1/clients/${id}${qs.toString() ? `?${qs}` : ""}`,
    token,
  );
  if (!res.ok) {
    throw new ApiError(
      await detailOf(res, `Client request failed (${res.status})`),
      res.status,
    );
  }
  return (await res.json()) as ClientDetailPayload;
}

/** FastAPI's `detail`, when there is a readable one. */
async function detailOf(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string" && body.detail) return body.detail;
    if (typeof body?.detail?.message === "string") return body.detail.message;
  } catch {
    /* not JSON — fall through */
  }
  return fallback;
}
