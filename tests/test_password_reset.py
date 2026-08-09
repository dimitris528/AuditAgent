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


# --- Diagnostics ----------------------------------------------------------
# A send that fails here fails invisibly by design: the endpoint answers the
# same 200 whatever happens. The log is therefore the ONLY place a failure can
# ever appear, which makes these assertions about the log assertions about
# whether the feature is operable at all.
def test_a_failed_send_logs_the_servers_own_words(api, monkeypatch, capsys):
    """The reason "no email arrived" is so hard to chase: the exception type
    alone does not say which setting is wrong. The provider's status code and
    response line do, and they are what an operator can act on."""
    def refuse(*_args, **_kwargs):
        raise mailer.smtplib.SMTPAuthenticationError(
            535, b"5.7.8 Authentication failed: invalid API key")

    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(mailer.smtplib, "SMTP", refuse)
    assert mailer.send_password_reset("x@example.com", "https://x/y") is False

    out = capsys.readouterr().out
    assert "SMTPAuthenticationError" in out
    assert "535" in out
    assert "invalid API key" in out          # the server's own text
    assert "stage 'connect'" in out          # where it died
    assert "Traceback (most recent call last)" in out


def test_the_stage_of_the_failure_is_named(api, monkeypatch, capsys):
    """'Failed at login' and 'failed at connect' send you to entirely
    different settings, and the exception does not always distinguish them."""
    class _SMTP:
        def __init__(self, *_a, **_kw): pass
        def __enter__(self): return self
        def __exit__(self, *_a): return False
        def starttls(self, context=None): pass
        def login(self, *_a):
            raise mailer.smtplib.SMTPAuthenticationError(535, b"nope")
        def send_message(self, _m): return {}

    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    # Without a user there is nothing to authenticate WITH, and the login step
    # is skipped entirely — which is itself a failure mode worth knowing about.
    monkeypatch.setattr(mailer, "SMTP_USER", "resend")
    monkeypatch.setattr(mailer.smtplib, "SMTP", _SMTP)
    assert mailer.send_password_reset("x@example.com", "https://x/y") is False
    assert "stage 'login'" in capsys.readouterr().out


def test_a_partly_refused_send_is_not_reported_as_success(api, monkeypatch, capsys):
    """send_message RETURNS refused recipients when at least one was accepted
    and only RAISES when they all were. Read as success, the one address that
    did not get the mail is exactly the one being complained about."""
    class _SMTP:
        def __init__(self, *_a, **_kw): pass
        def __enter__(self): return self
        def __exit__(self, *_a): return False
        def starttls(self, context=None): pass
        def login(self, *_a): pass
        def send_message(self, _m):
            return {"x@example.com": (550, b"Domain not verified")}

    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(mailer.smtplib, "SMTP", _SMTP)
    assert mailer.send_password_reset("x@example.com", "https://x/y") is False
    out = capsys.readouterr().out
    assert "REFUSED" in out
    assert "Domain not verified" in out


def test_the_credentials_never_reach_the_log(api, monkeypatch, capsys):
    """The configuration dump exists to answer "is the key even set" without
    becoming a way to read it out of a log."""
    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(mailer, "SMTP_PASSWORD", "re_supersecret_key_value")
    monkeypatch.setattr(mailer.smtplib, "SMTP",
                        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("x")))
    mailer.send_password_reset("x@example.com", "https://x/y")

    out = capsys.readouterr().out
    assert "re_supersecret_key_value" not in out
    assert "chars" in out          # it does say the length


def test_an_unlogged_address_still_logs_that_nothing_was_sent(api, capsys):
    """The commonest cause of "no reset email arrived" — the address matches
    no account — used to produce no evidence at all, so the mailer was blamed
    for a send it was never asked to make."""
    api.post(FORGOT, json={"email": UNKNOWN})
    out = capsys.readouterr().out
    assert "matches no account" in out
    assert UNKNOWN in out


def test_the_diagnostics_are_logged_without_reopening_the_oracle(api, capsys):
    """The property the whole endpoint is built around, restated against the
    new logging: the LOG may distinguish a known address from an unknown one —
    that is what makes a failure diagnosable — and the RESPONSE may not."""
    known = api.post(FORGOT, json={"email": KNOWN})
    known_log = capsys.readouterr().out
    unknown = api.post(FORGOT, json={"email": UNKNOWN})
    unknown_log = capsys.readouterr().out

    assert known.status_code == unknown.status_code
    assert known.json() == unknown.json()          # the caller learns nothing
    assert known_log != unknown_log                # the operator learns which
    assert "matches no account" in unknown_log
    assert "matches no account" not in known_log


def test_a_log_line_survives_an_address_the_console_cannot_encode(monkeypatch):
    """Greek email addresses are ordinary here, and a cp1252 stdout raises on
    them. Unhandled that would escape the mailer and answer 500 for a real
    address next to 200 for an unknown one — the enumeration oracle this flow
    exists to deny, reintroduced by its own diagnostics."""
    class _NarrowStream:
        encoding = "ascii"

        def __init__(self): self.written = []

        def write(self, text):
            if any(ord(c) > 127 for c in text):
                raise UnicodeEncodeError("ascii", text, 0, 1, "not encodable")
            self.written.append(text)

        def flush(self): pass

    stream = _NarrowStream()
    monkeypatch.setattr("sys.stdout", stream)
    mailer.log("INFO", "reset for χρήστης@παράδειγμα.gr")   # must not raise
    monkeypatch.undo()
    assert any("reset for" in chunk for chunk in stream.written)


# --- The Resend HTTPS transport -------------------------------------------
# SMTP cannot work on a host that blocks outbound mail ports, and a blocked
# port hangs rather than refusing — so the failure is a timeout after the full
# wait, on every attempt, with nothing at the provider end to look at. These
# cover the path that goes out over 443 instead.
import io                                                        # noqa: E402
import json                                                      # noqa: E402
import socket                                                    # noqa: E402
import urllib.error                                              # noqa: E402


class _Response:
    """The shape urlopen returns: a context manager with .status and .read()."""

    def __init__(self, status=200, body=b'{"id":"re_msg_123"}'):
        self.status, self._body = status, body

    def __enter__(self): return self
    def __exit__(self, *_a): return False
    def read(self): return self._body


def _http_error(code, body, reason="Forbidden"):
    return urllib.error.HTTPError(
        "https://api.resend.com/emails", code, reason, {}, io.BytesIO(body))


@pytest.fixture()
def api_transport(monkeypatch):
    """A process configured to send over the Resend API."""
    monkeypatch.setattr(mailer, "RESEND_API_KEY", "re_test_key_not_real")
    monkeypatch.setattr(mailer, "MAIL_FROM", "no-reply@example.gr")
    return monkeypatch


def test_an_api_key_selects_the_http_transport(api_transport):
    assert mailer.transport() == mailer.TRANSPORT_API
    assert mailer.is_configured() is True


def test_the_api_key_wins_over_smtp(api_transport):
    """The deployment with both is the one that configured SMTP, watched it
    time out, and added a key — so the key is the newer answer."""
    api_transport.setattr(mailer, "SMTP_HOST", "smtp.resend.com")
    assert mailer.transport() == mailer.TRANSPORT_API


def test_smtp_still_runs_when_no_api_key_is_set(monkeypatch):
    monkeypatch.setattr(mailer, "RESEND_API_KEY", "")
    monkeypatch.setattr(mailer, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(mailer, "MAIL_FROM", "no-reply@example.gr")
    assert mailer.transport() == mailer.TRANSPORT_SMTP


def test_neither_configured_is_still_no_transport(monkeypatch):
    monkeypatch.setattr(mailer, "RESEND_API_KEY", "")
    monkeypatch.setattr(mailer, "SMTP_HOST", "")
    assert mailer.transport() is None
    assert mailer.is_configured() is False


def test_a_successful_api_send_reports_the_message_id(api_transport, capsys):
    sent = {}

    def fake_urlopen(request, timeout=None):
        sent["url"] = request.full_url
        sent["body"] = json.loads(request.data.decode("utf-8"))
        sent["auth"] = request.headers.get("Authorization")
        return _Response()

    api_transport.setattr(mailer.urllib.request, "urlopen", fake_urlopen)
    assert mailer.send_password_reset("x@example.com", "https://x/y") is True

    assert sent["url"] == "https://api.resend.com/emails"
    assert sent["body"]["to"] == ["x@example.com"]
    assert sent["body"]["from"] == "no-reply@example.gr"
    assert "https://x/y" in sent["body"]["text"]
    assert sent["auth"] == "Bearer re_test_key_not_real"

    out = capsys.readouterr().out
    assert "re_msg_123" in out           # the id, for the Resend dashboard
    assert "ACCEPTED by Resend" in out


def test_a_refused_api_send_logs_resends_own_words(api_transport, capsys):
    """The 403 every unverified domain produces. The status alone does not say
    which domain or why; the body does, in a sentence."""
    body = (b'{"statusCode":403,"name":"validation_error",'
            b'"message":"The example.gr domain is not verified"}')

    def fake_urlopen(request, timeout=None):
        raise _http_error(403, body)

    api_transport.setattr(mailer.urllib.request, "urlopen", fake_urlopen)
    assert mailer.send_password_reset("x@example.com", "https://x/y") is False

    out = capsys.readouterr().out
    assert "HTTP 403" in out
    assert "The example.gr domain is not verified" in out
    assert "validation_error" in out
    assert "not verified" in out


def test_a_bad_api_key_is_named_as_such(api_transport, capsys):
    def fake_urlopen(request, timeout=None):
        raise _http_error(401, b'{"message":"API key is invalid"}',
                          reason="Unauthorized")

    api_transport.setattr(mailer.urllib.request, "urlopen", fake_urlopen)
    assert mailer.send_password_reset("x@example.com", "https://x/y") is False
    out = capsys.readouterr().out
    assert "HTTP 401" in out
    assert "Check RESEND_API_KEY" in out


def test_a_non_json_answer_is_quoted_rather_than_swallowed(api_transport, capsys):
    """A proxy or WAF between here and Resend answers with HTML, and "the body
    was not JSON" is itself the finding."""
    def fake_urlopen(request, timeout=None):
        raise _http_error(502, b"<html><body>Bad Gateway</body></html>")

    api_transport.setattr(mailer.urllib.request, "urlopen", fake_urlopen)
    assert mailer.send_password_reset("x@example.com", "https://x/y") is False
    out = capsys.readouterr().out
    assert "not JSON" in out
    assert "Bad Gateway" in out


def test_a_network_failure_names_the_wrapped_cause(api_transport, capsys):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError(socket.gaierror(-2, "Name not known"))

    api_transport.setattr(mailer.urllib.request, "urlopen", fake_urlopen)
    assert mailer.send_password_reset("x@example.com", "https://x/y") is False
    out = capsys.readouterr().out
    assert "gaierror" in out
    assert "did not resolve" in out
    assert "Traceback (most recent call last)" in out


def test_a_failed_api_send_does_not_fall_back_to_blocked_smtp(api_transport,
                                                              capsys):
    """The deliberate non-behaviour. On the host this exists for, SMTP is
    blocked and hangs — retrying over it would add a full timeout to every API
    error, which is the opposite of the problem being solved."""
    attempted = []

    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError(socket.timeout("timed out"))

    api_transport.setattr(mailer.urllib.request, "urlopen", fake_urlopen)
    api_transport.setattr(mailer, "SMTP_HOST", "smtp.resend.com")
    api_transport.setattr(mailer.smtplib, "SMTP",
                          lambda *a, **kw: attempted.append(a))

    assert mailer.send_password_reset("x@example.com", "https://x/y") is False
    assert attempted == [], "the API failure fell through to SMTP"


def test_the_api_key_never_reaches_the_log(api_transport, capsys):
    api_transport.setattr(mailer, "RESEND_API_KEY", "re_a_very_secret_key_1234")
    api_transport.setattr(
        mailer.urllib.request, "urlopen",
        lambda *_a, **_kw: (_ for _ in ()).throw(_http_error(401, b"{}")))
    mailer.send_password_reset("x@example.com", "https://x/y")

    out = capsys.readouterr().out
    assert "re_a_very_secret_key_1234" not in out
    assert "chars" in out          # it does confirm one is set, and how long


def test_the_endpoint_survives_an_api_failure(api, api_transport):
    """Same contract as the SMTP path: the neutral 200 does not depend on
    whether the mail went."""
    api_transport.setattr(
        mailer.urllib.request, "urlopen",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boom")))
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
