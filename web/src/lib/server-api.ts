// Server-side fetchers (used by Server Components). Attaches the tenant's
// Bearer token from the httpOnly cookie so the FastAPI backend scopes data.
import { getToken, API_BASE_URL } from "./session";
import { ApiError } from "./errors";
import type { DashboardData } from "./types";

export async function getDashboard(): Promise<DashboardData> {
  const token = await getToken();
  if (!token) throw new ApiError("unauthenticated", 401);
  const res = await fetch(`${API_BASE_URL}/api/dashboard`, {
    cache: "no-store",
    headers: { Authorization: `Bearer ${token}` },
  });
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
