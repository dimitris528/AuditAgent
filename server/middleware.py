"""
The two guards that sit in front of every request: is this caller signed in,
and are they asking too often.

Both are middleware rather than dependencies, and that is the point. A
`Depends(get_current_user)` protects the endpoint it is written on; these
protect the ones nobody remembered to write it on. The failure they exist for
is not a hostile request — it is a new route added six months from now that
ships without its guard and is silently public until someone notices.
"""

import fnmatch
import os
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

import auth
from server import tenancy

# --------------------------------------------------------------------------
# Auth gate
# --------------------------------------------------------------------------
#: Paths reachable WITHOUT a bearer token. An explicit allowlist, because the
#: whole value of this middleware is that everything not named here is closed
#: by default — an allowlist that is wrong fails visibly (a 401 on a public
#: page), while a denylist that is wrong fails silently and invisibly, which
#: is the failure mode worth designing against.
#:
#: Every entry is here for a stated reason:
PUBLIC_PATHS = (
    "/",                              # service banner
    "/api/health",                    # Render's health check — no token to give
    "/api/status",                    # setup diagnostics; leaks no data
    "/api/meta",                      # static VAT rates and document types
    "/api/auth/login",                # obviously: this is where tokens come from
    "/api/v1/auth/register",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    "/api/v1/auth/mfa/verify",        # second leg of login; carries a
                                      # challenge token, not a session
    "/api/v1/webhooks/stripe",        # authenticates by Stripe's signature
    "/docs", "/redoc", "/openapi.json",   # opt-in, and off by default
)

#: Prefixes that are not API surface at all (OpenAPI's static assets).
PUBLIC_PREFIXES = ("/docs/", "/static/")


def is_public(path):
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


class AuthGateMiddleware(BaseHTTPMiddleware):
    """Refuse anything not on the allowlist without a VALID bearer token.

    Deliberately duplicates what get_current_user already does per endpoint,
    and the duplication is the feature: this one cannot be forgotten. It
    validates the signature rather than merely checking a header is present,
    so a malformed or expired token is turned away here instead of reaching
    application code.

    It does NOT replace the per-endpoint dependency. That one still resolves
    the token to a real account, applies the subscription gate and — through
    resolve_user_state — declares the database tenant. This is a gate; that is
    the identity.
    """

    async def dispatch(self, request, call_next):
        path = request.url.path
        # CORS preflight carries no Authorization header by design.
        if request.method == "OPTIONS" or is_public(path):
            return await call_next(request)

        header = request.headers.get("authorization") or ""
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return JSONResponse({"detail": "Απαιτείται σύνδεση."}, status_code=401)
        try:
            auth.decode_token(token.strip())
        except auth.AuthError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=exc.status)
        except Exception:
            # Never surface the library's own message: it distinguishes
            # "signature invalid" from "expired" from "malformed", which tells
            # someone probing exactly which part of a forged token to fix.
            return JSONResponse({"detail": "Μη έγκυρη συνεδρία."}, status_code=401)

        # Cleared on the way in AND on the way out. Under an ASGI server each
        # request runs in its own task and gets its own copy of the context, so
        # this is belt-and-braces — but the belt is cheap and the failure it
        # guards against is a request inheriting the previous caller's tenant,
        # which is the one bug this whole module exists to make impossible.
        #
        # Reset to None rather than to the previous value: "before this
        # request" is not a state anything should return to, and restoring a
        # stale tenant is exactly what must not happen.
        tenancy.set_current_tenant(None)
        try:
            return await call_next(request)
        finally:
            tenancy.set_current_tenant(None)


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------
#: (requests, seconds) per client, by what the endpoint costs to abuse.
#:
#: `auth` is tuned against password guessing rather than load: ten attempts a
#: minute is invisible to someone typing their own password and useless to
#: someone working through a list.
#:
#: `write` covers imports and bulk deletes — each one is a file parse or a few
#: hundred statements, so a handful a minute is generous for a person and a
#: hard ceiling for a script.
LIMITS = {
    "auth": (10, 60),
    "write": (20, 60),
    "default": (240, 60),
}

#: Which bucket a path falls in. Longest match wins, so
#: /api/v1/auth/mfa/verify is `auth` and not merely `default`.
_BUCKETS = (
    ("/api/auth/login", "auth"),
    ("/api/v1/auth/", "auth"),
    ("/api/import/", "write"),
    ("/api/clients/bulk-", "write"),
    ("/api/transactions/bulk-", "write"),
    ("/api/v1/documents/scan", "write"),
)

#: Off by default in tests, where several hundred requests a second is normal
#: and a limiter would make the suite flaky rather than safe.
ENABLED = (os.getenv("RATE_LIMIT_ENABLED", "1").strip().lower()
           not in ("0", "false", "no", "off"))


def bucket_for(path):
    best = "default"
    longest = 0
    for prefix, name in _BUCKETS:
        if path.startswith(prefix) and len(prefix) > longest:
            best, longest = name, len(prefix)
    return best


class RateLimitMiddleware(BaseHTTPMiddleware):
    """A sliding window per (client, bucket), held in memory.

    Worth being explicit about the limits of this, because a rate limiter that
    is trusted further than it works is worse than none:

      * it is PER PROCESS. Two web workers mean two windows and twice the
        effective limit. Correct enforcement across instances needs shared
        state (Redis), and this is the version that works with no new
        infrastructure — which is the difference between having it and not.
      * the client is identified by IP, taken from X-Forwarded-For where a
        proxy set one. Behind a NAT that groups people together; the limits
        above are set high enough that ordinary shared egress is unaffected.

    What it does reliably is stop one host hammering login or replaying an
    import a thousand times, which is the threat it is here for.
    """

    def __init__(self, app):
        super().__init__(app)
        # {(client, bucket): deque[timestamp]}. Trimmed on every touch, so it
        # cannot grow without bound for a caller that stops calling.
        self._hits = {}

    @staticmethod
    def client_key(request):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            # The left-most entry is the original client; everything after it
            # was appended by proxies.
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _allow(self, key, limit, window, now):
        seen = self._hits.setdefault(key, deque())
        cutoff = now - window
        while seen and seen[0] <= cutoff:
            seen.popleft()
        if len(seen) >= limit:
            return False, int(seen[0] + window - now) + 1
        seen.append(now)
        if not seen:
            self._hits.pop(key, None)
        return True, 0

    async def dispatch(self, request, call_next):
        if not ENABLED or request.method == "OPTIONS":
            return await call_next(request)

        bucket = bucket_for(request.url.path)
        # Reads are cheap and the dashboard makes several per page; only the
        # expensive verbs are metered outside the auth bucket.
        if bucket == "default" and request.method in ("GET", "HEAD"):
            return await call_next(request)

        limit, window = LIMITS[bucket]
        key = (self.client_key(request), bucket)
        allowed, retry_after = self._allow(key, limit, window, time.monotonic())
        if not allowed:
            return JSONResponse(
                {"detail": "Πάρα πολλές αιτήσεις. Δοκιμάστε ξανά σε λίγο."},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
