// Dependency-free helpers safe to import anywhere (Edge middleware included).
export const SESSION_COOKIE = "session";

/**
 * Base URL of the FastAPI backend. Accepts a full URL or a bare host (Render's
 * `fromService` injects just the hostname) — a bare host is upgraded to https.
 */
export function apiBase(): string {
  const raw = process.env.API_BASE_URL || "http://localhost:8000";
  return /^https?:\/\//.test(raw) ? raw : `https://${raw}`;
}
