"""
The two guards that sit in front of every request: is this caller signed in,
and are they asking too often.

Both are middleware rather than dependencies, and that is the point. A
`Depends(get_current_user)` protects the endpoint it is written on; these
protect the ones nobody remembered to write it on. The failure they exist for
is not a hostile request — it is a new route added six months from now that
ships without its guard and is silently public until someone notices.
"""

import os
import secrets
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
#:
#: `register` is the strictest, and metered over an HOUR rather than a minute.
#: Signup is the one endpoint an anonymous caller can use to CREATE rows, and
#: each one costs a 600 000-iteration PBKDF2 hash plus a permanent tenant that
#: somebody has to look at afterwards. A per-minute window is the wrong shape
#: for that: a script sleeping 61 seconds between attempts would walk straight
#: through it and still mint 1 400 accounts a day. An hour-long window is one a
#: real person never notices — nobody registers five offices in an afternoon —
#: and one that makes bulk signup pointless rather than merely slow.
LIMITS = {
    "register": (5, 3600),
    "auth": (10, 60),
    "write": (20, 60),
    "default": (240, 60),
}

#: Which bucket a path falls in. Longest match wins, so
#: /api/v1/auth/mfa/verify is `auth` and /api/v1/auth/register is `register`,
#: rather than both falling back to `default`.
_BUCKETS = (
    ("/api/auth/login", "auth"),
    ("/api/v1/auth/", "auth"),
    ("/api/v1/auth/register", "register"),
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


#: Where the counters live. Set REDIS_URL and every instance shares one
#: window; leave it unset and each process keeps its own.
REDIS_URL = (os.getenv("REDIS_URL") or "").strip()


class MemoryWindow:
    """A sliding window per key, in this process and no further.

    The fallback, and an honest one: two web workers mean two windows and twice
    the effective limit, which is the whole reason the Redis backend below
    exists. It still does the thing it is mainly here for — stopping one host
    hammering login from one process — and it needs no infrastructure at all,
    which is the difference between having a limiter on a small deployment and
    not having one.
    """

    name = "memory"

    def __init__(self):
        # {key: deque[timestamp]}. Trimmed on every touch, so it cannot grow
        # without bound for a caller that stops calling.
        self._hits = {}

    async def hit(self, key, limit, window):
        now = time.monotonic()
        seen = self._hits.setdefault(key, deque())
        cutoff = now - window
        while seen and seen[0] <= cutoff:
            seen.popleft()
        if len(seen) >= limit:
            return False, int(seen[0] + window - now) + 1
        seen.append(now)
        return True, 0


class RedisWindow:
    """The same sliding window, in Redis, shared by every instance.

    A sorted set per key: drop what has aged out, count what is left, add this
    request. Run in one pipeline, so the four commands make a single round
    trip. The window is genuinely sliding rather than a fixed bucket, which
    matters at the edges — a fixed window lets twice the limit through across
    a boundary, and a login limiter that can be doubled by waiting for the
    minute to tick is not much of a limiter.

    Small races remain (two instances can both read a count of limit-1 and
    both admit a request). Left alone deliberately: closing them needs a Lua
    script and a watch loop to shave one request off a limit of ten, and the
    cost of that complexity is worse than the overrun.

    Falls back to memory on ANY Redis failure — see `_fail_open`. A limiter
    that 500s when its store blips has turned a defence into an outage.
    """

    name = "redis"

    def __init__(self, url):
        self.url = url
        self._client = None
        self._fallback = MemoryWindow()
        self.degraded = False

    def _connect(self):
        if self._client is None:
            import redis.asyncio as redis  # imported late: optional dependency

            # decode_responses off: the members written are timestamps and are
            # never read back as text.
            self._client = redis.from_url(self.url, socket_timeout=1,
                                          socket_connect_timeout=1)
        return self._client

    async def hit(self, key, limit, window):
        import time as _time

        now = _time.time()          # wall clock: shared across instances,
                                    # unlike monotonic(), which is per process
        redis_key = f"ratelimit:{key}"
        try:
            client = self._connect()
            pipe = client.pipeline(transaction=True)
            pipe.zremrangebyscore(redis_key, 0, now - window)
            pipe.zcard(redis_key)
            # A random member, not the timestamp: two requests in the same
            # microsecond would otherwise be one member and one of them would
            # not be counted.
            pipe.zadd(redis_key, {f"{now}:{secrets.token_hex(4)}": now})
            pipe.expire(redis_key, int(window) + 1)
            _, used, _, _ = await pipe.execute()
        except Exception as exc:
            return await self._fail_open(key, limit, window, exc)

        self.degraded = False
        if used >= limit:
            return False, int(window)
        return True, 0

    async def _fail_open(self, key, limit, window, exc):
        """Redis is unreachable. Meter in memory and carry on.

        Logged once per outage rather than per request: a Redis that is down
        is down for every request, and a line each would bury everything else
        in the log at exactly the moment somebody is reading it.
        """
        if not self.degraded:
            self.degraded = True
            print(f"[WARN] Rate limiter falling back to in-memory counters: "
                  f"{type(exc).__name__}: {exc}")
        # Drop the client so the next request reconnects rather than reusing a
        # socket that has already failed.
        self._client = None
        return await self._fallback.hit(key, limit, window)


def make_backend(url=None):
    """The counter store: Redis when a URL is configured, memory otherwise."""
    url = REDIS_URL if url is None else url
    return RedisWindow(url) if url else MemoryWindow()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Meters requests per (identity, bucket).

    Identity is the AUTHENTICATED USER where there is one and the IP
    otherwise, and the split matters in both directions. Metering an
    authenticated caller by IP punishes everyone behind one office NAT for the
    heaviest user among them; metering login by user id is impossible, because
    a brute-force attempt has no user id yet — that is precisely what it is
    trying to find. So each request is metered by whichever it actually has.

    The token is read but NOT verified here: this runs before the auth gate,
    and a forged token is only a grouping key. The worst it buys is being
    metered as somebody else, which is not an escalation — and an invalid
    token gets turned away by the gate a few lines later anyway.
    """

    def __init__(self, app, backend=None):
        super().__init__(app)
        self.backend = backend or make_backend()

    @staticmethod
    def client_ip(request):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            # The left-most entry is the original client; everything after it
            # was appended by proxies.
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    @classmethod
    def identity(cls, request):
        header = request.headers.get("authorization") or ""
        scheme, _, token = header.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            try:
                sub = auth.decode_token(token.strip()).get("sub")
                if sub:
                    return f"u:{sub}"
            except Exception:
                pass
        return f"ip:{cls.client_ip(request)}"

    async def dispatch(self, request, call_next):
        if not ENABLED or request.method == "OPTIONS":
            return await call_next(request)

        bucket = bucket_for(request.url.path)
        # Reads are cheap and the dashboard makes several per page; only the
        # expensive verbs are metered outside the auth bucket.
        if bucket == "default" and request.method in ("GET", "HEAD"):
            return await call_next(request)

        limit, window = LIMITS[bucket]
        key = f"{bucket}:{self.identity(request)}"
        allowed, retry_after = await self.backend.hit(key, limit, window)
        if not allowed:
            return JSONResponse(
                {"detail": "Πάρα πολλές αιτήσεις. Δοκιμάστε ξανά σε λίγο."},
                status_code=429,
                headers={"Retry-After": str(max(1, retry_after))},
            )
        return await call_next(request)
