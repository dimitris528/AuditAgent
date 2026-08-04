// Dependency-free helpers safe to import anywhere (Edge middleware included).
//
// Nothing here may import ./backend: middleware runs on the Edge runtime, and
// pulling the backend client in would drag server-only concerns into every
// request just to read a cookie name. apiBase() lives in "@/lib/backend".
export const SESSION_COOKIE = "session";
