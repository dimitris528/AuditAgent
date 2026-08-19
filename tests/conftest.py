"""
Test bootstrap.

The suite runs against a throwaway SQLite file, NOT Postgres: config.py reads
DATABASE_URL once at import time and server.database caches the engine, so the
variable has to be set before anything else is imported — hence the assignments
at module scope, above the imports that consume them.

server.database.is_postgres() is what keeps this honest: the Postgres-only
pieces (pgbouncer pooling, statement_timeout, the ADD COLUMN IF NOT EXISTS
migrations) are skipped on SQLite, and a fresh SQLite file gets the full schema
from create_all instead.
"""

import os
import pathlib
import tempfile

_DB = pathlib.Path(tempfile.mkdtemp(prefix="auditagent-tests-")) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["JWT_SECRET"] = "test-only-secret-not-used-anywhere-real"
# No OCR key: every test that touches scanning either asserts the disabled path
# or stubs the SDK, and a key inherited from the developer's shell would bill
# real OpenAI calls.
os.environ.pop("OPENAI_API_KEY", None)
# A FIXED webhook signing secret, so the billing tests can sign a payload the
# way Stripe does and exercise the real verification path rather than stubbing
# construct_event out (which would leave the signature check untested).
WEBHOOK_SECRET = "whsec_test_secret_for_the_suite_only"
os.environ["STRIPE_WEBHOOK_SECRET"] = WEBHOOK_SECRET
# Checkout stays UNCONFIGURED by default: an inherited sk_live_ key would open
# real Stripe sessions. The tests that need it patch server.billing directly.
os.environ.pop("STRIPE_SECRET_KEY", None)
os.environ.pop("STRIPE_PRICE_ID", None)
# Pinned rather than inherited: the trial assertions count days.
os.environ["TRIAL_DAYS"] = "14"
# The rate limiter is OFF in tests. The suite fires hundreds of requests a
# second from one "client", which is exactly what the limiter exists to stop —
# leaving it on would make the suite flaky rather than safe. It has its own
# tests (test_security.py) that switch it on deliberately.
os.environ["RATE_LIMIT_ENABLED"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import delete  # noqa: E402

from server import database, middleware  # noqa: E402
from server.main import app  # noqa: E402
from server.models import (Client, DebtPayment, Invoice,  # noqa: E402
                          Transaction, TrustedDevice, User)

# Children first: transactions reference clients and users, debt_payments
# references all three, and trusted_devices references users.
_TABLES = (DebtPayment, Invoice, Transaction, Client, TrustedDevice, User)


def signup_payload(**overrides):
    """A complete, VALID registration body.

    Every test that needs a tenant builds on this rather than writing the fields
    out again, so adding a required field to the signup contract is one edit
    here instead of one per call site — and a test that breaks on it breaks for
    the right reason.

    Reached through the `signup` fixture below rather than imported: tests/ has
    no __init__.py (see pytest.ini), so `from conftest import ...` works under
    one pytest invocation and not the other.
    """
    body = {
        "company_name": "Λογιστικό Γραφείο Δοκιμών",
        "full_name": "Δοκιμαστής Δοκιμίδης",
        "username": "tester",
        "email": "tester@example.com",
        "password": "correct-horse-battery",
        "accept_terms": True,
    }
    body.update(overrides)
    return body


@pytest.fixture()
def signup():
    """The builder above, as a fixture. `signup(email=...)` in any test."""
    return signup_payload


def _live_rate_limiter():
    """The RateLimitMiddleware instance actually serving requests.

    Not the class: `app.add_middleware` constructs ONE instance when the stack
    is built, and it owns its counter store as an instance attribute. Patching
    the class would leave the live object untouched — and a rate-limit test that
    silently metered against the real counters would pass or fail depending on
    what ran before it.
    """
    if app.middleware_stack is None:
        # Exactly what Starlette does on the first request; doing it here means
        # a test that never sent one still finds the stack.
        app.middleware_stack = app.build_middleware_stack()
    node = app.middleware_stack
    while node is not None:
        if isinstance(node, middleware.RateLimitMiddleware):
            return node
        node = getattr(node, "app", None)
    return None


@pytest.fixture()
def fresh_rate_limiter():
    """Meter this test against EMPTY counters, and put the old ones back.

    Needed because the limiter's windows outlive a test: the signup bucket is
    metered over an hour, so the second rate-limit test in a session would start
    already throttled by the first one's requests and assert nothing at all.
    """
    limiter = _live_rate_limiter()
    assert limiter is not None, "the rate limiter is no longer in the stack"
    previous = limiter.backend
    limiter.backend = middleware.MemoryWindow()
    try:
        yield limiter
    finally:
        limiter.backend = previous


@pytest.fixture(scope="session", autouse=True)
def schema():
    database.init_db()


@pytest.fixture()
def api():
    """A logged-in client against an empty book.

    Wiping between tests rather than sharing a fixture dataset: duplicate
    detection is the thing under test, so a row left behind by an earlier test
    is exactly the kind of contamination that would make a failure look like a
    pass.
    """
    with database.session_scope() as session:
        for model in _TABLES:
            session.execute(delete(model))
        session.commit()

    with TestClient(app) as client:
        res = client.post("/api/v1/auth/register", json=signup_payload())
        assert res.status_code == 201, res.text
        token = res.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        yield client
