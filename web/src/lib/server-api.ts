// Server-side fetchers (used by Server Components). Attaches the tenant's
// Bearer token from the httpOnly cookie so the FastAPI backend scopes data.
import { getToken, API_BASE_URL } from "./session";
import { ApiError } from "./errors";
import type { BillingStatus, DashboardData } from "./types";

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
  const res = await fetch(
    `${API_BASE_URL}/api/dashboard${qs.toString() ? `?${qs}` : ""}`,
    {
      cache: "no-store",
      headers: { Authorization: `Bearer ${token}` },
    },
  );
  if (!res.ok) {
    throw new ApiError(await detailOf(res, `Dashboard request failed (${res.status})`),
      res.status);
  }
  return (await res.json()) as DashboardData;
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
  const res = await fetch(`${API_BASE_URL}/api/v1/billing/status`, {
    cache: "no-store",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) {
    throw new ApiError(await detailOf(res, `Billing request failed (${res.status})`),
      res.status);
  }
  return (await res.json()) as BillingStatus;
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
