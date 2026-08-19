"""
Sentry: what leaves the process, and what must never.

The value of an error reporter is entirely in what it does NOT send. This is an
accounting product, so an unfiltered event can carry a client's ΑΦΜ, a login
email, a session JWT, the database password quoted back by libpq, or the
plaintext password sitting in a local variable of the frame that raised. All of
it would leave the boundary the privacy policy promises, and none of it is
needed to fix a stack trace.

So `before_send` is tested as a pure function, on the event shapes the SDK
actually builds. No network, no DSN, no SDK initialisation — those tests would
either send real events or assert nothing.
"""

import pytest
from fastapi import HTTPException

from server import monitoring


# ==========================================================================
# What is not worth reporting
# ==========================================================================
@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 409, 422, 429])
def test_a_deliberate_client_error_is_never_reported(status):
    """Every one of these is the product working correctly. The 401s alone —
    each expired session, each logged-out visitor — would bury the crashes this
    exists to surface."""
    exc = HTTPException(status_code=status, detail="…")
    event = {"exception": {"values": [{"type": "HTTPException"}]}}
    assert monitoring.before_send(event, {"exc_info": (type(exc), exc, None)}) is None


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_a_server_error_is_always_reported(status):
    """5xx is ours, whatever raised it."""
    exc = HTTPException(status_code=status, detail="…")
    event = {"exception": {"values": [{"type": "HTTPException"}]}}
    assert monitoring.before_send(event, {"exc_info": (type(exc), exc, None)}) is not None


def test_an_ordinary_crash_is_reported():
    exc = ValueError("the code was wrong")
    event = {"exception": {"values": [{"type": "ValueError",
                                       "value": "the code was wrong"}]}}
    assert monitoring.before_send(event, {"exc_info": (type(exc), exc, None)}) is not None


def test_an_event_with_no_exception_is_reported():
    """capture_message and the like. Nothing to classify, so nothing to drop."""
    assert monitoring.before_send({"message": "hello"}, {}) is not None
    assert monitoring.before_send({"message": "hello"}, None) is not None


def test_a_plain_object_with_a_status_is_not_mistaken_for_an_http_error():
    """The check is duck-typed (Starlette's HTTPException and FastAPI's are
    different classes), so it has to be narrow enough not to swallow a real
    crash that happens to carry a `status_code`."""
    class ResponseLike:
        status_code = 404

    assert monitoring.is_expected_http_error(ResponseLike()) is False
    assert monitoring.is_expected_http_error(ValueError("nope")) is False
    assert monitoring.is_expected_http_error(None) is False


# ==========================================================================
# Scrubbing
# ==========================================================================
@pytest.mark.parametrize("key", [
    "password", "user_password", "new_password",
    "access_token", "reset_token", "token",
    "authorization", "Authorization", "cookie", "Set-Cookie",
    "jwt_secret", "STRIPE_SECRET_KEY", "api_key", "X-Api-Key",
    "mfa_secret", "otp", "DATABASE_URL_credential", "signature",
])
def test_a_credential_shaped_key_is_dropped(key):
    assert monitoring.scrub({key: "the-actual-secret"})[key] == monitoring.REDACTED


@pytest.mark.parametrize("key", [
    "email", "username", "full_name", "company_name", "afm",
    "counterparty_afm", "phone", "contact",
])
def test_personal_data_is_dropped_too(key):
    """Not a credential, but still a named person or business. "The email was
    rejected" is the useful half; the address itself is not."""
    assert monitoring.scrub({key: "Μαρία"})[key] == monitoring.REDACTED


def test_an_ordinary_key_survives():
    """The scrub has to leave enough behind to debug with."""
    assert monitoring.scrub({"status_code": 500, "endpoint": "/api/dashboard"}) == \
        {"status_code": 500, "endpoint": "/api/dashboard"}


def test_an_email_in_free_text_is_masked():
    assert monitoring.scrub_text("no account for maria@grafeio.gr") == \
        "no account for [email]"


def test_a_jwt_in_free_text_is_masked():
    token = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0ZXIifQ."
             "abcdefghij")
    assert token not in monitoring.scrub_text(f"Bearer {token} was rejected")


def test_a_connection_string_password_is_masked():
    """The single most likely secret to appear in a production stack trace:
    libpq quotes the whole DSN back when a connection fails."""
    dsn = "postgresql://postgres.abcd:S3cr3t%2Fpw@aws-0-eu.pooler.supabase.com:6543/postgres"
    masked = monitoring.scrub_text(f"could not connect to {dsn}")
    assert "S3cr3t" not in masked
    assert "postgresql://" in masked


def test_the_walk_gives_up_rather_than_looping_forever():
    """An event is a tree built partly from user data. Hanging inside an error
    handler is the hardest possible place to diagnose a hang."""
    deep = current = {}
    for _ in range(50):
        current["next"] = {}
        current = current["next"]
    current["leaf"] = "value"

    node = monitoring.scrub(deep)
    levels = 0
    while isinstance(node, dict) and "next" in node:
        node = node["next"]
        levels += 1
    # Truncated a long way short of the 50 it was handed, and terminated in a
    # marker rather than in the original value.
    assert levels <= monitoring._MAX_DEPTH + 1
    assert node == monitoring.REDACTED


def test_a_list_of_dicts_is_walked():
    payload = {"rows": [{"email": "a@b.gr", "amount": 10.0}]}
    assert monitoring.scrub(payload) == \
        {"rows": [{"email": monitoring.REDACTED, "amount": 10.0}]}


def test_scrubbing_does_not_mutate_the_input():
    """The event handed to before_send is built from live objects. Scrubbing in
    place could redact the request the application is still serving."""
    original = {"password": "hunter2"}
    monitoring.scrub(original)
    assert original == {"password": "hunter2"}


# --- The whole event -------------------------------------------------------
def _event():
    """An event shaped the way the FastAPI integration builds one."""
    return {
        "message": "failed for maria@grafeio.gr",
        "request": {
            "url": "https://api.example.com/api/v1/clients?afm=123456789",
            "cookies": {"session": "eyJhbGciOi.payload.sig"},
            "headers": {"Authorization": "Bearer abc", "User-Agent": "curl"},
            "query_string": "email=maria@grafeio.gr",
            "data": {"password": "hunter2", "company_name": "Νησίδα Α.Ε."},
        },
        "user": {"id": 42, "email": "maria@grafeio.gr", "username": "maria"},
        "extra": {"typed_password": "hunter2"},
        "exception": {"values": [{
            "type": "ValueError",
            "value": "rejected maria@grafeio.gr",
            "stacktrace": {"frames": [
                {"function": "register",
                 "vars": {"password": "hunter2", "email": "maria@grafeio.gr",
                          "status": 500}},
            ]},
        }]},
        "breadcrumbs": {"values": [
            {"message": "POST /api/v1/auth/register for maria@grafeio.gr",
             "data": {"access_token": "abc123"}},
        ]},
    }


def test_cookies_are_dropped_wholesale():
    """They carry the session JWT and the trusted-device 2FA bypass. There is
    nothing in either worth a bug report."""
    out = monitoring.before_send(_event(), {})
    assert "cookies" not in out["request"]


def test_the_authorization_header_never_leaves():
    out = monitoring.before_send(_event(), {})
    assert out["request"]["headers"]["Authorization"] == monitoring.REDACTED
    # And the harmless ones survive, or there is nothing left to debug with.
    assert out["request"]["headers"]["User-Agent"] == "curl"


def test_the_posted_password_never_leaves():
    out = monitoring.before_send(_event(), {})
    assert out["request"]["data"]["password"] == monitoring.REDACTED
    assert "hunter2" not in repr(out)


def test_the_password_in_a_stack_frames_locals_never_leaves():
    """The subtle one, and the reason include_local_variables is safe to leave
    on: the frame that raises inside the register endpoint is holding the
    plaintext password in a local at that exact moment."""
    out = monitoring.before_send(_event(), {})
    local_vars = out["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
    assert local_vars["password"] == monitoring.REDACTED
    assert local_vars["email"] == monitoring.REDACTED
    # Not everything — a traceback with no locals is barely a traceback.
    assert local_vars["status"] == 500


def test_the_tenant_is_identified_by_id_and_nothing_else():
    out = monitoring.before_send(_event(), {})
    assert out["user"] == {"id": 42}


def test_emails_are_masked_everywhere_they_appear():
    out = monitoring.before_send(_event(), {})
    assert "maria@grafeio.gr" not in repr(out)


def test_breadcrumbs_are_scrubbed():
    out = monitoring.before_send(_event(), {})
    crumb = out["breadcrumbs"]["values"][0]
    assert "maria@grafeio.gr" not in crumb["message"]
    assert crumb["data"]["access_token"] == monitoring.REDACTED


def test_a_transaction_event_is_scrubbed_too():
    """Performance events carry a URL and a query string, which is enough to
    leak an ΑΦΜ or an address on their own."""
    out = monitoring.before_send_transaction(_event(), {})
    assert "cookies" not in out["request"]
    assert out["request"]["headers"]["Authorization"] == monitoring.REDACTED


# ==========================================================================
# Initialisation
# ==========================================================================
def test_no_dsn_means_nothing_is_initialised():
    """The normal state locally and in the test suite. An error reporter that
    phones home from a developer's laptop is a privacy problem, not a feature."""
    assert monitoring.init(dsn="") is False
    assert monitoring.is_configured() is False


def test_a_broken_dsn_does_not_take_the_service_down(capsys):
    """Monitoring that cannot start must not stop the thing it monitors."""
    assert monitoring.init(dsn="not-a-dsn") is False
    assert monitoring.is_configured() is False


def test_the_status_endpoint_reports_whether_reporting_is_on(api):
    """A boolean, and never the DSN — /api/status is public."""
    body = api.get("/api/status").json()
    assert body["sentry_configured"] is False
    assert not any("sentry" in str(key).lower() and "dsn" in str(key).lower()
                   for key in body)
    assert "sentry_dsn" not in body
