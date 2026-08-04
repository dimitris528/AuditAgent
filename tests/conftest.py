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
# Live data only: the demo dataset would mask a broken query with fixtures.
os.environ["DASHBOARD_DEMO"] = "0"
# No OCR key: every test that touches scanning asserts the disabled path, and a
# key inherited from the developer's shell would bill real API calls.
os.environ.pop("ANTHROPIC_API_KEY", None)
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

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import delete  # noqa: E402

from server import database  # noqa: E402
from server.main import app  # noqa: E402
from server.models import Client, DebtPayment, Invoice, Transaction, User  # noqa: E402

# Children first: transactions reference clients and users, and debt_payments
# references all three.
_TABLES = (DebtPayment, Invoice, Transaction, Client, User)


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
        res = client.post("/api/v1/auth/register", json={
            "username": "tester",
            "email": "tester@example.com",
            "password": "correct-horse-battery",
        })
        assert res.status_code == 201, res.text
        token = res.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        yield client
