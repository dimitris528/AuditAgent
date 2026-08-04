import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { SESSION_COOKIE } from "@/lib/constants";

// Route protection: without a session cookie, everything except /login is
// redirected to /login. Presence is checked here for a fast redirect; the JWT
// is actually VALIDATED server-side by FastAPI on every data call (an expired
// token yields 401 → the page redirects to /login).
//
// The SUBSCRIPTION paywall is deliberately NOT enforced here. This middleware
// runs on the Edge runtime with nothing but the cookie: it cannot read the
// database, and the JWT carries no subscription claim (nor should it — a token
// minted during a trial would keep asserting "trialing" for its full 12 hours
// after the trial ended). The gate therefore lives where the answer is
// current: FastAPI returns 402 on writes, and the dashboard page redirects to
// /billing. /billing itself only needs a session, which this already ensures.
//
// Pages reachable without a session. /register must be here or a new visitor
// is redirected to /login before they can ever sign up.
const PUBLIC_PATHS = new Set(["/login", "/register"]);

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const hasSession = Boolean(req.cookies.get(SESSION_COOKIE)?.value);
  const isLogin = PUBLIC_PATHS.has(pathname);

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
