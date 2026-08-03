// Server-side fetchers (used by Server Components). Attaches the tenant's
// Bearer token from the httpOnly cookie so the FastAPI backend scopes data.
import { getToken, API_BASE_URL } from "./session";
import { ApiError } from "./errors";
import type { DashboardData } from "./types";

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
    let detail = `Dashboard request failed (${res.status})`;
    try {
      const j = await res.json();
      if (j?.detail) detail = j.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as DashboardData;
}
