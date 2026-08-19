"""
Sentry error monitoring for the FastAPI backend.

Two jobs, and the second is the one worth reading:

1. Report crashes. A 500 in production is currently a line in Render's log that
   nobody is looking at; here it becomes an issue with a stack trace, the
   request that caused it, and a count of how many tenants hit it.

2. Report NOTHING ELSE. This is an accounting product, so an unfiltered error
   payload is a GDPR incident waiting to happen: it can carry a client's ΑΦΜ, a
   counterparty's name, a login email, a session JWT, or the password field of
   the form that just failed. None of that is needed to fix a stack trace, and
   all of it would leave the boundary the privacy policy promises. So every
   event is walked and scrubbed before the SDK is allowed to send it, and the
   4xx responses this application raises ON PURPOSE are dropped entirely.

Optional by construction
------------------------
`sentry_sdk` is imported inside a try, and nothing is initialised without
SENTRY_DSN. A deployment without either runs exactly as it did before this
module existed — which is also how the test suite runs, so no test ever emits a
real event.
"""

import re

from config import (SENTRY_DSN, SENTRY_ENVIRONMENT, SENTRY_RELEASE,
                    SENTRY_TRACES_SAMPLE_RATE)

try:  # pragma: no cover - exercised by whether the package is installed
    import sentry_sdk
except ImportError:  # pragma: no cover
    sentry_sdk = None


#: Set once `init()` has actually initialised the SDK, so /api/status can report
#: the truth rather than "a DSN is configured" (which is not the same thing when
#: the package is missing).
_initialised = False


def is_available():
    """True when the sentry-sdk package is importable."""
    return sentry_sdk is not None


def is_configured():
    """True when this process is actually reporting to Sentry."""
    return _initialised


# --------------------------------------------------------------------------
# What is NOT worth reporting
# --------------------------------------------------------------------------
#: HTTP statuses this application raises deliberately as part of normal
#: operation. Every one of them is the product working correctly:
#:
#:      401  the session expired, or nobody is signed in
#:      402  the trial lapsed — the paywall, doing its job
#:      403  refused on purpose
#:      404  a row that does not exist, or belongs to another tenant (the two
#:           are deliberately indistinguishable — see server/main.py)
#:      409  a duplicate invoice or a taken username
#:      422  a body that failed validation
#:      429  the rate limiter turning a flood away
#:
#: Reporting these would turn Sentry into a log of ordinary user behaviour. The
#: 401s alone — every expired session, every logged-out visitor — would bury the
#: crashes this exists to surface, and the 429s would spike hardest during
#: exactly the incident somebody is trying to read the dashboard about.
#:
#: The rule is by RANGE, not by an enumerated list: anything 4xx is a verdict we
#: chose to return, and anything 5xx is ours and always reported. A future
#: endpoint answering 451 is covered without anyone remembering to add it.
def is_expected_http_error(exc):
    """True when `exc` is an HTTPException carrying a deliberate 4xx."""
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        return False
    # Duck-typed rather than isinstance(HTTPException): Starlette's and
    # FastAPI's are different classes, and importing either here would pull the
    # web framework into a module that otherwise needs nothing.
    if not hasattr(exc, "detail"):
        return False
    return 400 <= status < 500


# --------------------------------------------------------------------------
# Scrubbing
# --------------------------------------------------------------------------
#: Keys whose VALUE is dropped outright, matched case-insensitively anywhere in
#: the key — so "user_password", "X-Api-Key" and "access_token" all match.
_SENSITIVE_KEY = re.compile(
    r"pass|secret|token|auth|cookie|session|jwt|otp|mfa|api[-_]?key"
    r"|dsn|credential|signature|card|iban",
    re.IGNORECASE)

#: Keys that are personal data rather than credentials. Replaced by a marker
#: instead of removed, because "the email was rejected" is a useful thing to
#: know from an error and the address itself is not.
_PII_KEY = re.compile(
    r"email|username|full[-_]?name|company|phone|contact|address|afm|counterparty",
    re.IGNORECASE)

REDACTED = "[redacted]"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
#: A JWT, or any long opaque blob that reads like a credential — a reset token
#: quoted in a message, a bearer token spliced into a URL.
_TOKEN = re.compile(
    r"\b(?:ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"
    r"|[A-Za-z0-9_-]{40,})\b")
#: The connection string, which carries the database password. A libpq error
#: routinely quotes it verbatim.
_DSN_URL = re.compile(r"(?i)\b([a-z0-9+]+://)[^\s:@/]+:[^\s@/]+@")

#: How deep the walk goes before it gives up. A Sentry event is a tree built
#: partly from user data, and an unbounded walk over a cyclic structure would
#: hang the process INSIDE an error handler — the hardest possible place to
#: diagnose a hang.
_MAX_DEPTH = 8


def scrub_text(value):
    """Mask what a free-text value may have swallowed."""
    if not isinstance(value, str):
        return value
    value = _DSN_URL.sub(r"\1[redacted]:[redacted]@", value)
    value = _EMAIL.sub("[email]", value)
    return _TOKEN.sub("[token]", value)


def scrub(value, depth=0):
    """Recursively redact a payload. Returns a new value; the input is never
    mutated, so a caller cannot accidentally scrub the live request."""
    if depth > _MAX_DEPTH:
        return REDACTED
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            name = str(key)
            if _SENSITIVE_KEY.search(name):
                out[key] = REDACTED
            elif _PII_KEY.search(name):
                out[key] = REDACTED if isinstance(item, str) else scrub(item, depth + 1)
            else:
                out[key] = scrub(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        scrubbed = [scrub(item, depth + 1) for item in value]
        return type(value)(scrubbed) if isinstance(value, tuple) else scrubbed
    return value


def before_send(event, hint):
    """The gate every error passes through on its way out.

    Returning None DISCARDS the event — the SDK sends nothing — so this is a
    real filter and not a display preference.
    """
    exc_info = (hint or {}).get("exc_info")
    if exc_info and is_expected_http_error(exc_info[1]):
        return None

    request = event.get("request")
    if isinstance(request, dict):
        # Dropped wholesale rather than filtered: cookies carry the session and
        # the trusted-device bypass, and there is nothing in either worth a bug
        # report.
        request.pop("cookies", None)
        for key in ("headers", "query_string", "data", "env"):
            if key in request:
                request[key] = scrub(request[key])
        if isinstance(request.get("url"), str):
            request["url"] = scrub_text(request["url"])

    # The tenant is identified by their opaque id and nothing else — never the
    # email or the username, both of which name a real person or business.
    user = event.get("user")
    if isinstance(user, dict):
        event["user"] = {"id": user.get("id")} if user.get("id") else {}

    for key in ("extra", "contexts", "tags"):
        if key in event:
            event[key] = scrub(event[key])

    if isinstance(event.get("message"), str):
        event["message"] = scrub_text(event["message"])

    for entry in (event.get("exception") or {}).get("values") or []:
        if isinstance(entry, dict) and isinstance(entry.get("value"), str):
            entry["value"] = scrub_text(entry["value"])
        # Local variables are the richest source of leaked data in a Python
        # stack trace: the frame that raised inside the register endpoint holds
        # the plaintext password in a local. Scrubbed per frame rather than
        # switched off wholesale, because the locals are also what makes a
        # Python traceback worth having.
        for frame in ((entry.get("stacktrace") or {}).get("frames") or []):
            if isinstance(frame, dict) and isinstance(frame.get("vars"), dict):
                frame["vars"] = scrub(frame["vars"])

    for crumb in event.get("breadcrumbs", {}).get("values", []) or []:
        if isinstance(crumb, dict):
            if isinstance(crumb.get("message"), str):
                crumb["message"] = scrub_text(crumb["message"])
            if isinstance(crumb.get("data"), dict):
                crumb["data"] = scrub(crumb["data"])

    return event


def before_send_transaction(event, hint):
    """Performance events carry a URL and its query string, and nothing else
    worth keeping — so they get the same scrub without the exception handling."""
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("cookies", None)
        for key in ("headers", "query_string", "data"):
            if key in request:
                request[key] = scrub(request[key])
        if isinstance(request.get("url"), str):
            request["url"] = scrub_text(request["url"])
    return event


# --------------------------------------------------------------------------
# Initialisation
# --------------------------------------------------------------------------
def init(dsn=None, environment=None):
    """Start reporting, if there is anywhere to report to.

    Returns True when the SDK was initialised. Deliberately silent-and-false in
    every other case: a missing DSN is the normal local state, and a missing
    package is a deployment that chose not to install it. Neither is a reason
    to refuse to boot — the whole point of an error reporter is that it cannot
    itself become the outage.
    """
    global _initialised

    dsn = (dsn if dsn is not None else SENTRY_DSN) or ""
    if not dsn:
        return False
    if sentry_sdk is None:
        print("[WARN] SENTRY_DSN is set but the sentry-sdk package is not "
              "installed — error monitoring is OFF. Add sentry-sdk to "
              "requirements.txt.")
        return False

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment or SENTRY_ENVIRONMENT,
            release=SENTRY_RELEASE or None,
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            # OFF. With it on the SDK attaches request bodies, IP addresses and
            # cookies BEFORE before_send ever runs — so this flag is the first
            # line of the same defence, and before_send is the second, for the
            # data our own code puts on an event.
            send_default_pii=False,
            # Local variables per stack frame. Kept because they are most of
            # what makes a Python traceback actionable, and safe because
            # before_send scrubs them (see there — the register endpoint holds
            # a plaintext password in a local at the moment it could raise).
            include_local_variables=True,
            max_request_body_size="never",
            before_send=before_send,
            before_send_transaction=before_send_transaction,
        )
    except Exception as exc:  # pragma: no cover - defensive
        # A malformed DSN raises here. Reported and swallowed: monitoring that
        # cannot start must not stop the service it monitors.
        print(f"[ERROR] Sentry could not be initialised "
              f"({type(exc).__name__}: {exc}) — error monitoring is OFF.")
        return False

    _initialised = True
    print(f"[INFO] Sentry error monitoring is on "
          f"(environment={environment or SENTRY_ENVIRONMENT}, "
          f"traces={SENTRY_TRACES_SAMPLE_RATE}).")
    return True
