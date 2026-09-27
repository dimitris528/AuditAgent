"""
The demo-account guard (server/demo_account.py).

The demo's credentials are public, so the tests below all ask one question:
can a visitor signed in as the demo reach something that costs money, acts on
Stripe, locks other visitors out, or takes the account over? Each guarded
route is checked for the demo AND for an ordinary tenant, because a guard that
also blocked real customers would pass every "demo is refused" test.
"""

import datetime as dt
import io

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from scripts import seed_demo
from server import billing, database, ocr, store
from server import demo_account as demo
from server.main import app
from server.models import PasswordResetToken, User

FORGOT = "/api/v1/auth/forgot-password"
RESET = "/api/v1/auth/reset-password"


@pytest.fixture()
def demo_api(api):
    """A client signed in as the seeded demo tenant (on top of `api`'s wipe)."""
    with database.session_scope() as session:
        seed_demo.seed(session, today=dt.date(2026, 9, 27))
    with TestClient(app) as client:
        res = client.post("/api/auth/login",
                          json={"username": seed_demo.DEMO_EMAIL,
                                "password": seed_demo.DEMO_PASSWORD})
        assert res.status_code == 200, res.text
        client.headers["Authorization"] = f"Bearer {res.json()['access_token']}"
        yield client


def _assert_demo_refused(res):
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["code"] == "demo_account_restricted"


# --------------------------------------------------------------------------
# Billing
# --------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/api/v1/billing/checkout",
                                  "/api/v1/billing/portal",
                                  "/api/v1/billing/cancel"])
def test_demo_cannot_touch_billing(demo_api, monkeypatch, path):
    # Configured, so a refusal cannot be the "Stripe is not set up" 503.
    monkeypatch.setattr(billing, "is_configured", lambda: True)
    monkeypatch.setattr(billing, "portal_is_configured", lambda: True)

    def no_stripe(*a, **k):
        raise AssertionError("the demo reached Stripe")

    monkeypatch.setattr(billing.stripe.checkout.Session, "create", no_stripe)
    monkeypatch.setattr(billing.stripe.billing_portal.Session, "create", no_stripe)
    _assert_demo_refused(demo_api.post(path))


def test_billing_guard_leaves_real_tenants_alone(api):
    # Stripe is unconfigured in the suite, so a real tenant gets the 503 that
    # says so — anything but the demo's 403.
    res = api.post("/api/v1/billing/checkout")
    assert res.status_code != 403


# --------------------------------------------------------------------------
# 2FA
# --------------------------------------------------------------------------
def test_demo_cannot_start_2fa(demo_api):
    _assert_demo_refused(demo_api.post("/api/v1/auth/mfa/setup"))
    with database.session_scope() as session:
        user = store.get_user_by_username(session, demo.DEMO_USERNAME)
        assert user.mfa_secret is None and user.mfa_enabled is False


def test_demo_cannot_enable_2fa(demo_api):
    _assert_demo_refused(demo_api.post("/api/v1/auth/mfa/enable",
                                       json={"code": "123456"}))


def test_2fa_guard_leaves_real_tenants_alone(api):
    assert api.post("/api/v1/auth/mfa/setup").status_code != 403


# --------------------------------------------------------------------------
# Invoice scanning (OpenAI)
# --------------------------------------------------------------------------
def test_demo_cannot_scan_invoices(demo_api, monkeypatch):
    monkeypatch.setattr(ocr, "is_configured", lambda: True)

    def no_openai(*a, **k):
        raise AssertionError("the demo reached OpenAI")

    monkeypatch.setattr(ocr, "extract", no_openai)
    res = demo_api.post("/api/v1/documents/scan",
                        files={"file": ("invoice.pdf", io.BytesIO(b"%PDF-1.4"),
                                        "application/pdf")})
    _assert_demo_refused(res)


def test_scan_guard_leaves_real_tenants_alone(api):
    res = api.post("/api/v1/documents/scan",
                   files={"file": ("invoice.pdf", io.BytesIO(b"%PDF-1.4"),
                                   "application/pdf")})
    assert res.status_code == 503  # no OPENAI_API_KEY in the suite — not 403


# --------------------------------------------------------------------------
# Password reset
# --------------------------------------------------------------------------
def _reset_tokens_for(username):
    with database.session_scope() as session:
        user = store.get_user_by_username(session, username)
        return session.exec(select(PasswordResetToken)
                            .where(PasswordResetToken.user_id == user.id)).all()


def test_forgot_password_for_the_demo_sends_nothing(demo_api):
    res = demo_api.post(FORGOT, json={"email": seed_demo.DEMO_EMAIL})
    # The neutral answer every address gets…
    assert res.status_code == 200
    assert "reset_token" not in res.json()
    # …but no token was minted, so no link exists to be mailed.
    assert _reset_tokens_for(demo.DEMO_USERNAME) == []


def test_forgot_password_still_works_for_real_tenants(api):
    assert api.post(FORGOT, json={"email": "tester@example.com"}).status_code == 200
    assert len(_reset_tokens_for("tester")) == 1


def test_a_stray_reset_token_cannot_change_the_demo_password(demo_api):
    # A token minted before the guard existed.
    with database.session_scope() as session:
        user = store.get_user_by_username(session, demo.DEMO_USERNAME)
        raw = store.create_reset_token(session, user)
        before = user.password_hash

    _assert_demo_refused(demo_api.post(RESET, json={
        "token": raw, "password": "an0ther-Strong-passphrase!"}))
    with database.session_scope() as session:
        assert store.get_user_by_username(
            session, demo.DEMO_USERNAME).password_hash == before


# --------------------------------------------------------------------------
# The identity is reserved
# --------------------------------------------------------------------------
def test_nobody_can_register_as_the_demo(api, signup):
    api.headers.pop("Authorization", None)
    assert api.post("/api/v1/auth/register", json=signup(
        username="demo", email="someone@example.com")).status_code == 409
    assert api.post("/api/v1/auth/register", json=signup(
        username="someone", email="DEMO@auditagent.io")).status_code == 409


def test_a_demo_named_email_is_not_given_the_demo_username(api, signup):
    api.headers.pop("Authorization", None)
    res = api.post("/api/v1/auth/register",
                   json=signup(username=None, email="demo@gmail.com"))
    assert res.status_code == 201, res.text
    assert res.json()["username"] != demo.DEMO_USERNAME
    # And that tenant is NOT restricted like the demo.
    with database.session_scope() as session:
        assert session.exec(select(User).where(
            User.username == demo.DEMO_USERNAME)).first() is None
