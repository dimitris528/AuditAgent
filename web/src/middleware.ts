import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { SESSION_COOKIE } from "@/lib/constants";

// Route protection: without a session cookie, everything except /login is
// redirected to /login. Presence is checked here for a fast redirect; the JWT
// is actually VALIDATED server-side by FastAPI on every data call (an expired
// token yields 401 → the page redirects to /login).
export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const hasSession = Boolean(req.cookies.get(SESSION_COOKIE)?.value);
  const isLogin = pathname === "/login";

  if (!hasSession && !isLogin) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    if (pathname !== "/") url.searchParams.set("from", pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

// Exclude API route handlers and static assets so login/logout keep working
// and assets aren't gated.
export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
