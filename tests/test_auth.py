"""
Login, and the removal of the demo credential.

The demo/demo account used to be accepted whenever DATABASE_URL was absent, and
the login page printed the credential for every visitor. These tests are the
thing standing between that and a well-meaning re-introduction: a shared
password published in a public repository is not a bootstrap convenience, it is
a way in.
"""

import pytest

import auth
from server import database


def test_the_demo_credential_is_refused_without_a_database(monkeypatch):
    """The exact path that used to accept it: no database configured."""
    monkeypatch.setattr(database, "is_configured", lambda: False)
    assert auth.authenticate("demo", "demo") is None


@pytest.mark.parametrize("identifier,password", [
    ("demo", "demo"),
    ("DEMO", "demo"),
    ("demo", "DEMO"),
    ("demo@example.com", "demo"),
])
def test_no_credential_at_all_works_without_a_database(monkeypatch, identifier,
                                                       password):
    """Not just the one string — nothing authenticates without the users
    table, so there is no variant to stumble onto."""
    monkeypatch.setattr(database, "is_configured", lambda: False)
    assert auth.authenticate(identifier, password) is None


def test_the_demo_constants_are_gone(monkeypatch):
    """A guard against re-introduction. If someone adds DEMO_USER back, this
    fails and they have to read the note in auth.py explaining why it went."""
    for name in ("DEMO_USER", "DEMO_PASSWORD", "DEMO_ENABLED"):
        assert not hasattr(auth, name), f"auth.{name} is back — see auth.py"


def test_login_endpoint_rejects_demo_demo(api):
    """End to end, with a database configured — there is simply no such row."""
    res = api.post("/api/auth/login", json={"username": "demo",
                                            "password": "demo"})
    assert res.status_code == 401


def test_status_never_reports_a_demo_auth_mode(api):
    """It used to say "demo" without a database. With the credential gone that
    would be a lie, and a diagnostics endpoint that lies is worse than none."""
    body = api.get("/api/status").json()
    assert body["auth_mode"] in ("postgres", "disabled")
    assert body["auth_mode"] != "demo"


# --- Regression guards: real logins must still work -----------------------
def test_a_real_account_still_logs_in(api):
    res = api.post("/api/auth/login", json={"username": "tester",
                                            "password": "correct-horse-battery"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["username"] == "tester"
    assert body["demo"] is False
    assert body["access_token"]


def test_login_by_email_still_works(api):
    """Signup collects an email, so people reasonably try to log in with it."""
    res = api.post("/api/auth/login", json={"username": "tester@example.com",
                                            "password": "correct-horse-battery"})
    assert res.status_code == 200, res.text
    assert res.json()["username"] == "tester"


def test_a_wrong_password_is_still_a_401(api):
    res = api.post("/api/auth/login", json={"username": "tester",
                                            "password": "not-the-password"})
    assert res.status_code == 401
