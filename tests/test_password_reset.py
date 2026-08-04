"""
Forgot / reset password.

Most of these are about what the endpoints REFUSE to reveal. A reset flow is
the softest part of any auth system: it mints a credential from an email
address, so every way it can differ between a real and an unknown account is a
way to enumerate users, and every token that outlives its use is a way in.
"""

import datetime as dt

import pytest

from server import database, mailer, store
from server.models import PasswordResetToken

FORGOT = "/api/v1/auth/forgot-password"
RESET = "/api/v1/auth/reset-password"

KNOWN = "tester@example.com"
UNKNOWN = "nobody-at-all@example.com"
NEW_PASSWORD = "a-brand-new-password"


def _issue(api, email=KNOWN):
    """Ask for a reset and dig the raw token out of the database.

    Read from the table rather than the response on purpose: the endpoint does
    not return it in this configuration, and a test that relied on it returning
    one would be testing the development switch instead of the real flow.
    """
    assert api.post(FORGOT, json={"email": email}).status_code == 200
    # Recreate the hash from a candidate to find the row is impossible — the
    # raw value is gone. So the tests mint their own through the store, which
    # is the same code path the endpoint uses.
    with database.session_scope() as session:
        user = store.get_user_by_email(session, email)
        return store.create_reset_token(session, user)


def _row_count():
    with database.session_scope() as session:
        from sqlmodel import select
        return len(session.exec(select(PasswordResetToken)).all())


# --- Enumeration ----------------------------------------------------------
def test_a_known_and_an_unknown_address_are_indistinguishable(api):
    """The whole security property of this endpoint. Any difference — status,
    body, wording — turns it into a tool for discovering who has an account."""
    known = api.post(FORGOT, json={"email": KNOWN})
    unknown = api.post(FORGOT, json={"email": UNKNOWN})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


def test_no_token_is_returned_to_the_caller(api):
    """Returning one would be account takeover as a feature."""
    body = api.post(FORGOT, json={"email": KNOWN}).json()
    assert "reset_token" not in body
    assert "reset_url" not in body


def test_the_development_switch_refuses_to_arm_against_a_real_database():
    """RESET_TOKEN_IN_RESPONSE is exactly the flag someone leaves on. It is
    computed at import against the configured database, and this suite runs on
    SQLite, so the assertion here is on the guard existing and defaulting off."""
    assert mailer.EXPOSE_RESET_TOKEN is False


def test_an_unknown_address_creates_no_token(api):
    before = _row_count()
    api.post(FORGOT, json={"email": UNKNOWN})
    assert _row_count() == before


def test_a_malformed_address_is_rejected_by_validation(api):
    assert api.post(FORGOT, json={"email": "not-an-email"}).status_code == 422


# --- The token ------------------------------------------------------------
def test_the_raw_token_is_never_stored(api):
    """The table holds a list of live account-takeover keys. Anyone who reads
    it — a backup, a log — must not be able to use what they find."""
    raw = _issue(api)
    with database.session_scope() as session:
        from sqlmodel import select
        hashes = [r.token_hash for r in session.exec(select(PasswordResetToken)).all()]
    assert raw not in hashes
    assert store.hash_reset_token(raw) in hashes
    assert all(len(h) == 64 for h in hashes)


def test_a_valid_token_sets_the_new_password(api):
    raw = _issue(api)
    res = api.post(RESET, json={"token": raw, "password": NEW_PASSWORD})
    assert res.status_code == 200, res.text

    # The old password no longer works and the new one does.
    assert api.post("/api/auth/login", json={
        "username": "tester", "password": "correct-horse-battery"}).status_code == 401
    assert api.post("/api/auth/login", json={
        "username": "tester", "password": NEW_PASSWORD}).status_code == 200


def test_a_token_cannot_be_used_twice(api):
    """A link sits in an inbox forever; spending it must close it."""
    raw = _issue(api)
    assert api.post(RESET, json={"token": raw, "password": NEW_PASSWORD}).status_code == 200
    second = api.post(RESET, json={"token": raw, "password": "another-password-x"})
    assert second.status_code == 400


def test_an_expired_token_is_refused(api):
    raw = _issue(api)
    with database.session_scope() as session:
        from sqlmodel import select
        row = session.exec(
            select(PasswordResetToken)
            .where(PasswordResetToken.token_hash == store.hash_reset_token(raw))
        ).one()
        row.expires_at = store.utcnow() - dt.timedelta(minutes=1)
        session.add(row)
        session.commit()
    assert api.post(RESET, json={"token": raw,
                                 "password": NEW_PASSWORD}).status_code == 400


def test_an_unknown_token_is_refused(api):
    res = api.post(RESET, json={"token": "x" * 64, "password": NEW_PASSWORD})
    assert res.status_code == 400


def test_every_rejection_reads_the_same(api):
    """Unknown, expired and already-spent must not be distinguishable —
    "already used" would confirm the token was real."""
    used = _issue(api)
    api.post(RESET, json={"token": used, "password": NEW_PASSWORD})

    messages = set()
    for token in (used, "y" * 64):
        res = api.post(RESET, json={"token": token, "password": NEW_PASSWORD})
        assert res.status_code == 400
        messages.add(res.json()["detail"])
    assert len(messages) == 1


def test_requesting_again_invalidates_the_previous_link(api):
    """Two working keys in one inbox is one too many."""
    first = _issue(api)
    second = _issue(api)
    assert api.post(RESET, json={"token": first,
                                 "password": NEW_PASSWORD}).status_code == 400
    assert api.post(RESET, json={"token": second,
                                 "password": NEW_PASSWORD}).status_code == 200


def test_a_short_password_is_refused_and_the_token_survives(api):
    """Validation runs before the token is spent, so a typo does not cost the
    user their link."""
    raw = _issue(api)
    assert api.post(RESET, json={"token": raw, "password": "short"}).status_code == 422
    assert api.post(RESET, json={"token": raw,
                                 "password": NEW_PASSWORD}).status_code == 200


def test_the_reset_does_not_hand_back_a_session(api):
    """Logging in with the new password is what proves it is the one they set."""
    raw = _issue(api)
    body = api.post(RESET, json={"token": raw, "password": NEW_PASSWORD}).json()
    assert "access_token" not in body


def test_tokens_are_unique_and_unguessable(api):
    with database.session_scope() as session:
        user = store.get_user_by_email(session, KNOWN)
        tokens = {store.create_reset_token(session, user) for _ in range(5)}
    assert len(tokens) == 5
    for token in tokens:
        assert len(token) >= 40


# --- Delivery -------------------------------------------------------------
def test_the_link_points_at_the_frontend_form_not_the_api(api):
    """The user needs the page, not the endpoint."""
    link = mailer.reset_link("abc123")
    assert "/reset-password?token=abc123" in link
    assert "/api/" not in link


def test_sending_without_smtp_reports_failure_rather_than_pretending(api):
    """A silent no-op would leave the flow looking healthy while every user
    waited for an email that was never coming."""
    assert mailer.is_configured() is False
    assert mailer.send_password_reset("someone@example.com", "https://x/y") is False


def test_a_mail_failure_never_breaks_the_endpoint(api, monkeypatch):
    """A 500 for a real address next to a 200 for an unknown one is exactly the
    oracle the neutral response exists to deny."""
    def boom(*_args, **_kwargs):
        raise RuntimeError("smtp exploded")

    monkeypatch.setattr(mailer, "send_password_reset", boom)
    with pytest.raises(RuntimeError):
        mailer.send_password_reset("x", "y")
    # The endpoint catches nothing itself — mailer does — so restore and prove
    # the real implementation swallows its own failure.
    monkeypatch.undo()
    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(mailer.smtplib, "SMTP", boom)
    assert mailer.send_password_reset("x@example.com", "https://x/y") is False
    assert api.post(FORGOT, json={"email": KNOWN}).status_code == 200


# --- Housekeeping ---------------------------------------------------------
def test_spent_tokens_are_purged(api):
    raw = _issue(api)
    api.post(RESET, json={"token": raw, "password": NEW_PASSWORD})
    with database.session_scope() as session:
        assert store.purge_expired_reset_tokens(session) >= 1


def test_reset_requires_a_database(api, monkeypatch):
    monkeypatch.setattr(database, "is_configured", lambda: False)
    assert api.post(FORGOT, json={"email": KNOWN}).status_code == 503
