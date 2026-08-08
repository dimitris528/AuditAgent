"""
The security boundary: tenancy, the auth gate, rate limiting, and 2FA.

Tenant isolation is asserted the only way worth asserting it — by having a
SECOND real account try, through the API, to read and change the first one's
rows. Every id used is genuine and belongs to somebody; what is under test is
that belonging to somebody else is enough.

The RLS policies in scripts/enable_rls.sql are the second lock on that door
and cannot run here: the suite is on SQLite, which has neither
current_setting() nor row level security. What IS tested here is the half that
decides whether those policies can work at all — that the tenant reaches the
database on every transaction, including after a commit — because a policy
reading a variable nobody set denies the application every row.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from server import mfa, middleware, store, tenancy
from server.main import app


@pytest.fixture(autouse=True)
def _no_inherited_tenant():
    """Every test starts with no tenant declared.

    Not tidiness: "nobody, until a token says otherwise" is the default the
    RLS policies rely on, and a test that passed only because an earlier one
    left a tenant lying around would be asserting the opposite of the
    property.
    """
    tenancy.set_current_tenant(None)
    yield
    tenancy.set_current_tenant(None)


@pytest.fixture()
def other(api):
    """A second registered tenant, with their own token and their own data."""
    res = api.post("/api/v1/auth/register", json={
        "username": "outsider", "email": "outsider@example.com",
        "password": "correct-horse-battery"})
    assert res.status_code == 201, res.text
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    api.post("/api/v1/clients", json={"name": "Ξένος Πελάτης", "afm": "999888777"},
             headers=headers)
    api.post("/api/transactions", json={
        "client": "Ξένος Πελάτης", "amount": 500.0, "type": "Έσοδο",
        "vat_rate": 0.24, "date": "2026-01-15"}, headers=headers)

    listed = api.get("/api/v1/clients", headers=headers).json()["clients"]
    txns = api.get("/api/transactions", headers=headers).json()["transactions"]
    return {"headers": headers, "client_id": listed[0]["id"],
            "txn_id": txns[0]["id"]}


def _mine(api):
    api.post("/api/v1/clients", json={"name": "Δικός μου", "afm": "111222333"})
    api.post("/api/transactions", json={
        "client": "Δικός μου", "amount": 124.0, "type": "Έσοδο",
        "vat_rate": 0.24, "date": "2026-01-15"})


# ==========================================================================
# Tenant isolation
# ==========================================================================
def test_a_tenant_sees_only_their_own_rows(api, other):
    _mine(api)
    assert [c["name"] for c in api.get("/api/v1/clients").json()["clients"]] == \
        ["Δικός μου"]
    assert [t["client"] for t in api.get("/api/transactions").json()["transactions"]] == \
        ["Δικός μου"]

    dashboard = api.get("/api/dashboard").json()
    assert dashboard["counts"]["active_clients"] == 1
    assert dashboard["header"]["total_gross_rev"] == 124.0


def test_another_tenants_client_is_indistinguishable_from_a_missing_one(api, other):
    """404, not 403. Telling someone the id EXISTS but is not theirs confirms
    the account it belongs to — which is exactly the thing not to confirm."""
    res = api.get(f"/api/v1/clients/{other['client_id']}")
    assert res.status_code == 404


def test_a_tenant_cannot_edit_another_tenants_client(api, other):
    res = api.put(f"/api/v1/clients/{other['client_id']}",
                  json={"name": "Καταλήφθηκε"})
    assert res.status_code == 404
    # And the row is untouched when its owner looks.
    listed = api.get("/api/v1/clients", headers=other["headers"]).json()["clients"]
    assert listed[0]["name"] == "Ξένος Πελάτης"


def test_a_tenant_cannot_delete_another_tenants_transaction(api, other):
    assert api.delete(f"/api/transactions/{other['txn_id']}").status_code == 404
    rows = api.get("/api/transactions", headers=other["headers"]).json()
    assert len(rows["transactions"]) == 1


def test_a_tenant_cannot_settle_another_tenants_debt(api, other):
    debt = api.post("/api/transactions", json={
        "client": "Ξένος Πελάτης", "amount": 500.0, "type": "Χρεωστούμενο",
        "vat_rate": 0.24, "date": "2026-02-01"},
        headers=other["headers"]).json()["id"]
    res = api.post(f"/api/v1/transactions/{debt}/settle", json={"amount": 100.0})
    assert res.status_code == 404


def test_a_tenant_cannot_read_another_tenants_statement(api, other):
    res = api.get(f"/api/v1/clients/{other['client_id']}?year=2026")
    assert res.status_code == 404


def test_an_export_carries_only_the_callers_rows(api, other):
    _mine(api)
    body = api.get("/api/v1/exports/transactions.csv").text
    assert "Δικός μου" in body
    assert "Ξένος Πελάτης" not in body


# --- Bulk actions, which touch many rows at once --------------------------
def test_bulk_delete_cannot_reach_across_tenants(api, other):
    """The case that matters most: one request naming many ids, where a
    missing tenant filter would take somebody else's book with it."""
    _mine(api)
    mine = api.get("/api/transactions").json()["transactions"][0]["id"]

    body = api.post("/api/transactions/bulk-delete",
                    json={"ids": [mine, other["txn_id"]]}).json()
    assert body["deleted"] == 1
    assert body["skipped"] == 1

    survivors = api.get("/api/transactions",
                        headers=other["headers"]).json()["transactions"]
    assert [t["id"] for t in survivors] == [other["txn_id"]]


def test_bulk_client_delete_cannot_reach_across_tenants(api, other):
    body = api.post("/api/clients/bulk-delete",
                    json={"ids": [other["client_id"]]}).json()
    assert body["deleted"] == 0
    assert body["skipped"] == 1
    assert api.get("/api/v1/clients",
                   headers=other["headers"]).json()["clients"] != []


def test_bulk_archive_cannot_reach_across_tenants(api, other):
    body = api.post("/api/clients/bulk-archive",
                    json={"ids": [other["client_id"]]}).json()
    assert body["changed"] == 0
    theirs = api.get("/api/v1/clients", headers=other["headers"]).json()["clients"]
    assert theirs[0]["archived"] is False


# --- Imports --------------------------------------------------------------
def test_an_import_lands_only_in_the_callers_book(api, other):
    """Tenancy comes from the token. There is no field in a spreadsheet that
    could point the rows anywhere else, and this is the assertion that keeps
    it that way."""
    csv = ("Ημερομηνία;Πελάτης;Είδος Κίνησης;Σύνολο\r\n"
           "2026-03-01;Εισαγόμενος;Έσοδο;1240,00\r\n").encode("utf-8-sig")
    res = api.post("/api/import/transactions",
                   files={"file": ("k.csv", csv, "text/csv")})
    assert res.json()["imported"] == 1

    theirs = api.get("/api/transactions",
                     headers=other["headers"]).json()["transactions"]
    assert [t["client"] for t in theirs] == ["Ξένος Πελάτης"]


# --- The database-level half ----------------------------------------------
def test_the_tenant_is_declared_for_the_rls_policies(api):
    """RLS is only as good as the variable it reads. If resolve_user_state
    stopped setting it, the policies in enable_rls.sql would deny the
    application every row — an outage, not a leak, but an outage caused by a
    silent change here."""
    from server import database, deps

    with database.session_scope() as session:
        deps.resolve_user_state(session, "tester")
    assert tenancy.current_tenant() is not None


def test_the_tenant_survives_a_commit(api):
    """The subtle one. store.py commits several times inside one session, and
    each commit ends the transaction a SET LOCAL was scoped to. The listener
    re-applies it on every `after_begin`, which is what keeps the second and
    third writes of a request from running with no tenant at all."""
    from sqlalchemy import event
    from sqlmodel import Session

    from server import database

    seen = []

    def record(session, transaction, connection):
        seen.append(tenancy.current_tenant())

    event.listen(Session, "after_begin", record)
    token = tenancy.set_current_tenant(4242)
    try:
        with database.session_scope() as session:
            session.exec(__import__("sqlalchemy").text("select 1"))
            session.commit()
            session.exec(__import__("sqlalchemy").text("select 1"))
            session.commit()
    finally:
        tenancy.reset(token)
        event.remove(Session, "after_begin", record)

    assert len(seen) >= 2, "a transaction began without the listener firing"
    assert all(value == 4242 for value in seen)


class _StubSession:
    """Just enough Session to see what tenancy.bind_session emits."""

    def __init__(self, dialect):
        import types

        self.executed = []
        self._bind = types.SimpleNamespace(
            dialect=types.SimpleNamespace(name=dialect))

    def get_bind(self):
        return self._bind

    def execute(self, statement, params=None):
        self.executed.append((str(statement), params))


def test_the_tenant_is_pushed_into_the_transaction_already_open():
    """The bug this exists for returned 200 with an EMPTY book.

    A request resolves its tenant by looking the user up, and that lookup is
    itself a query — so the transaction is already open by the time the tenant
    is known, and the after_begin listener has come and gone with nothing to
    set. Every later statement in that transaction then ran unstamped, matched
    no policy, and returned nothing: no error anywhere, just a dashboard that
    had quietly lost its data.
    """
    session = _StubSession("postgresql")
    tenancy.bind_session(session, tenant_id=77)
    assert len(session.executed) == 1
    sql, params = session.executed[0]
    assert "set_config" in sql
    assert params == {"name": tenancy.SETTING, "value": "77"}


def test_binding_is_a_no_op_off_postgres():
    """SQLite has neither set_config nor RLS; raising there would fail the
    whole suite for a production-only feature."""
    session = _StubSession("sqlite")
    tenancy.bind_session(session, tenant_id=77)
    assert session.executed == []


def test_binding_is_a_no_op_with_no_tenant():
    session = _StubSession("postgresql")
    tenancy.bind_session(session)
    assert session.executed == []


def test_resolving_a_user_binds_the_session_it_was_given(api, monkeypatch):
    """The ordering requirement, pinned. resolve_user_state must hand the LIVE
    session to bind_session — setting only the ContextVar leaves the
    transaction already in flight unstamped."""
    from server import database, deps

    seen = []
    monkeypatch.setattr(tenancy, "bind_session",
                        lambda session, tenant_id=None: seen.append(session))

    with database.session_scope() as session:
        deps.resolve_user_state(session, "tester")
        assert seen == [session], "the open transaction was never stamped"


def test_no_tenant_is_declared_outside_a_request():
    """The default has to be "nobody". Under RLS that means no rows, which is
    the correct way for a background job with no tenant to fail."""
    assert tenancy.current_tenant() is None


# ==========================================================================
# The auth gate
# ==========================================================================
@pytest.mark.parametrize("method,path", [
    ("get", "/api/dashboard"),
    ("get", "/api/v1/clients"),
    ("get", "/api/transactions"),
    ("post", "/api/v1/clients"),
    ("post", "/api/transactions/bulk-delete"),
    ("post", "/api/import/analyze"),
    ("get", "/api/v1/auth/mfa"),
])
def test_every_data_route_refuses_an_unauthenticated_caller(method, path):
    with TestClient(app) as client:
        res = (client.post(path, json={}) if method == "post"
               else client.get(path))
    assert res.status_code == 401


@pytest.mark.parametrize("header", [
    "",
    "Bearer ",
    "Basic dGVzdGVyOnNlY3JldA==",
    "Bearer not-a-token",
    # Correctly shaped, signed with the wrong key.
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJ0ZXN0ZXIiLCJpYXQiOjEsImV4cCI6OTk5OTk5OTk5OX0.forged",
])
def test_a_malformed_or_forged_token_never_reaches_application_code(header):
    with TestClient(app) as client:
        res = client.get("/api/dashboard", headers={"Authorization": header})
    assert res.status_code == 401


def test_the_gate_does_not_leak_why_a_token_failed():
    """"Signature invalid" versus "expired" tells someone probing exactly
    which part of a forged token to work on next."""
    with TestClient(app) as client:
        res = client.get("/api/dashboard",
                         headers={"Authorization": "Bearer aaa.bbb.ccc"})
    detail = res.json()["detail"]
    for leaked in ("signature", "Signature", "algorithm", "expired", "padding"):
        assert leaked not in detail


@pytest.mark.parametrize("path", [
    "/api/health", "/api/status", "/api/meta", "/",
])
def test_the_public_endpoints_stay_public(path):
    with TestClient(app) as client:
        assert client.get(path).status_code == 200


def test_a_new_route_is_closed_by_default():
    """The reason this is middleware and not another dependency. Nothing here
    has to remember anything: a path that is not on the allowlist is shut."""
    assert middleware.is_public("/api/dashboard") is False
    assert middleware.is_public("/api/some/future/route") is False
    assert middleware.is_public("/api/health") is True


# ==========================================================================
# Rate limiting
# ==========================================================================
def test_repeated_password_guesses_are_turned_away(monkeypatch):
    monkeypatch.setattr(middleware, "ENABLED", True)
    limit, _window = middleware.LIMITS["auth"]

    with TestClient(app) as client:
        codes = [
            client.post("/api/auth/login",
                        json={"username": "tester", "password": f"guess-{i}"}).status_code
            for i in range(limit + 3)
        ]
    assert 429 in codes, "the limiter never fired"
    assert codes.index(429) <= limit + 1


def test_a_throttled_response_says_when_to_come_back(monkeypatch):
    monkeypatch.setattr(middleware, "ENABLED", True)
    limit, _ = middleware.LIMITS["auth"]
    with TestClient(app) as client:
        last = None
        for i in range(limit + 3):
            last = client.post("/api/auth/login",
                               json={"username": "x", "password": f"p{i}"})
    assert last.status_code == 429
    assert int(last.headers["Retry-After"]) >= 1
    assert "{" not in last.json()["detail"]


@pytest.mark.parametrize("path,expected", [
    ("/api/auth/login", "auth"),
    ("/api/v1/auth/mfa/verify", "auth"),
    ("/api/import/process", "write"),
    ("/api/clients/bulk-delete", "write"),
    ("/api/transactions/bulk-delete", "write"),
    ("/api/dashboard", "default"),
])
def test_each_sensitive_path_lands_in_the_right_bucket(path, expected):
    assert middleware.bucket_for(path) == expected


def test_reads_are_not_metered(monkeypatch):
    """The dashboard makes several GETs per page. Metering them would throttle
    ordinary use while doing nothing about the abuse this is here for."""
    monkeypatch.setattr(middleware, "ENABLED", True)
    with TestClient(app) as client:
        codes = [client.get("/api/health").status_code for _ in range(400)]
    assert 429 not in codes


# ==========================================================================
# Two-factor authentication
# ==========================================================================
pytestmark_mfa = pytest.mark.skipif(not mfa.is_available(),
                                    reason="pyotp is not installed")


def _totp(secret):
    import pyotp

    return pyotp.TOTP(secret).now()


@pytestmark_mfa
def test_enrolment_does_not_turn_the_factor_on(api):
    """Abandoning the setup screen must leave the account exactly as it was —
    not locked behind a factor nobody finished enrolling."""
    setup = api.post("/api/v1/auth/mfa/setup").json()
    assert setup["secret"]
    assert setup["otpauth_url"].startswith("otpauth://totp/")

    status = api.get("/api/v1/auth/mfa").json()
    assert status["enabled"] is False
    assert status["pending"] is True

    # And login still hands out a session.
    res = api.post("/api/auth/login", json={
        "username": "tester", "password": "correct-horse-battery"})
    assert "access_token" in res.json()


@pytestmark_mfa
def test_a_wrong_code_does_not_enable_the_factor(api):
    api.post("/api/v1/auth/mfa/setup")
    assert api.post("/api/v1/auth/mfa/enable",
                    json={"code": "000000"}).status_code == 401
    assert api.get("/api/v1/auth/mfa").json()["enabled"] is False


@pytestmark_mfa
def test_login_stops_at_a_challenge_once_the_factor_is_on(api):
    secret = api.post("/api/v1/auth/mfa/setup").json()["secret"]
    api.post("/api/v1/auth/mfa/enable", json={"code": _totp(secret)})

    res = api.post("/api/auth/login", json={
        "username": "tester", "password": "correct-horse-battery"})
    body = res.json()
    assert body["mfa_required"] is True
    # The password alone yields NOTHING that can read a book.
    assert "access_token" not in body
    assert body["trust_days"] == mfa.TRUST_DAYS


@pytestmark_mfa
def test_a_challenge_is_not_a_session(api):
    """It says "this password was correct" and nothing else. Presented as a
    bearer token it must open nothing — otherwise passing leg one would be the
    whole login and the second factor would be decorative."""
    secret = api.post("/api/v1/auth/mfa/setup").json()["secret"]
    api.post("/api/v1/auth/mfa/enable", json={"code": _totp(secret)})
    challenge = api.post("/api/auth/login", json={
        "username": "tester", "password": "correct-horse-battery"}).json()["challenge"]

    with TestClient(app) as client:
        res = client.get("/api/dashboard",
                         headers={"Authorization": f"Bearer {challenge}"})
    assert res.status_code == 401


@pytestmark_mfa
def test_the_right_code_completes_the_login(api):
    secret = api.post("/api/v1/auth/mfa/setup").json()["secret"]
    api.post("/api/v1/auth/mfa/enable", json={"code": _totp(secret)})
    challenge = api.post("/api/auth/login", json={
        "username": "tester", "password": "correct-horse-battery"}).json()["challenge"]

    res = api.post("/api/v1/auth/mfa/verify",
                   json={"challenge": challenge, "code": _totp(secret)})
    assert res.status_code == 200
    assert "access_token" in res.json()


@pytestmark_mfa
def test_a_wrong_code_does_not_complete_the_login(api):
    secret = api.post("/api/v1/auth/mfa/setup").json()["secret"]
    api.post("/api/v1/auth/mfa/enable", json={"code": _totp(secret)})
    challenge = api.post("/api/auth/login", json={
        "username": "tester", "password": "correct-horse-battery"}).json()["challenge"]

    res = api.post("/api/v1/auth/mfa/verify",
                   json={"challenge": challenge, "code": "000000"})
    assert res.status_code == 401
    assert "access_token" not in res.json()


# --- Trust this device ----------------------------------------------------
@pytestmark_mfa
def test_a_trusted_device_skips_the_prompt_next_time(api):
    secret = api.post("/api/v1/auth/mfa/setup").json()["secret"]
    api.post("/api/v1/auth/mfa/enable", json={"code": _totp(secret)})
    challenge = api.post("/api/auth/login", json={
        "username": "tester", "password": "correct-horse-battery"}).json()["challenge"]

    verified = api.post("/api/v1/auth/mfa/verify", json={
        "challenge": challenge, "code": _totp(secret), "trust_device": True})
    assert verified.json()["device_trusted"] is True
    # The cookie is set on the response and httpOnly.
    assert mfa.DEVICE_COOKIE in verified.cookies

    again = api.post("/api/auth/login", json={
        "username": "tester", "password": "correct-horse-battery"}).json()
    assert "access_token" in again
    assert again.get("mfa_required") is not True


@pytestmark_mfa
def test_without_the_checkbox_the_prompt_comes_back(api):
    secret = api.post("/api/v1/auth/mfa/setup").json()["secret"]
    api.post("/api/v1/auth/mfa/enable", json={"code": _totp(secret)})
    challenge = api.post("/api/auth/login", json={
        "username": "tester", "password": "correct-horse-battery"}).json()["challenge"]

    api.post("/api/v1/auth/mfa/verify",
             json={"challenge": challenge, "code": _totp(secret)})
    again = api.post("/api/auth/login", json={
        "username": "tester", "password": "correct-horse-battery"}).json()
    assert again["mfa_required"] is True


@pytestmark_mfa
def test_one_accounts_device_token_does_nothing_for_another(api, other):
    """A device token is evidence about the account it was issued for and no
    other. Without the user check it would be a bearer bypass for anyone who
    could get hold of a cookie."""
    from server import database

    with database.session_scope() as session:
        mine = store.get_user_by_username(session, "tester")
        theirs = store.get_user_by_username(session, "outsider")
        token = mfa.new_device_token()
        store.trust_device(session, theirs, token, mfa.trust_expiry(),
                           label="δικό τους")
        assert store.find_trusted_device(session, mine, token) is None
        assert store.find_trusted_device(session, theirs, token) is not None


@pytestmark_mfa
def test_a_device_is_labelled_with_the_browser_not_the_proxy(api):
    """Found by running it: the backend sees the Next route handler's own
    user-agent ("node"), so every device was labelled identically and the list
    could not be used to tell one from another before revoking it. The browser
    agent is forwarded as X-Device-Agent and preferred here."""
    secret = api.post("/api/v1/auth/mfa/setup").json()["secret"]
    api.post("/api/v1/auth/mfa/enable", json={"code": _totp(secret)})
    challenge = api.post("/api/auth/login", json={
        "username": "tester", "password": "correct-horse-battery"}).json()["challenge"]

    api.post("/api/v1/auth/mfa/verify",
             json={"challenge": challenge, "code": _totp(secret),
                   "trust_device": True},
             headers={"X-Device-Agent": "Mozilla/5.0 Chrome/140 Safari/537.36",
                      "User-Agent": "node"})

    devices = api.get("/api/v1/auth/mfa").json()["devices"]
    assert "Chrome" in devices[0]["label"]
    assert devices[0]["label"] != "node"


@pytestmark_mfa
def test_an_expired_trust_stops_working(api):
    from server import database

    with database.session_scope() as session:
        row = store.get_user_by_username(session, "tester")
        token = mfa.new_device_token()
        store.trust_device(
            session, row, token,
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1))
        assert store.find_trusted_device(session, row, token) is None


@pytestmark_mfa
def test_trust_can_be_revoked(api):
    """The reason these are database rows and not self-contained signed
    tokens: a signed token stays valid until it expires whatever its owner
    does about the laptop they left on a train."""
    secret = api.post("/api/v1/auth/mfa/setup").json()["secret"]
    api.post("/api/v1/auth/mfa/enable", json={"code": _totp(secret)})
    challenge = api.post("/api/auth/login", json={
        "username": "tester", "password": "correct-horse-battery"}).json()["challenge"]
    api.post("/api/v1/auth/mfa/verify", json={
        "challenge": challenge, "code": _totp(secret), "trust_device": True})

    assert len(api.get("/api/v1/auth/mfa").json()["devices"]) == 1
    assert api.delete("/api/v1/auth/mfa/devices").json()["revoked"] == 1

    again = api.post("/api/auth/login", json={
        "username": "tester", "password": "correct-horse-battery"}).json()
    assert again["mfa_required"] is True


@pytestmark_mfa
def test_turning_the_factor_off_forgets_every_device(api):
    """Otherwise a device trusted under the old secret keeps a live bypass
    that nobody remembers granting, ready for whenever 2FA is turned back on."""
    secret = api.post("/api/v1/auth/mfa/setup").json()["secret"]
    api.post("/api/v1/auth/mfa/enable", json={"code": _totp(secret)})
    challenge = api.post("/api/auth/login", json={
        "username": "tester", "password": "correct-horse-battery"}).json()["challenge"]
    api.post("/api/v1/auth/mfa/verify", json={
        "challenge": challenge, "code": _totp(secret), "trust_device": True})

    assert api.post("/api/v1/auth/mfa/disable",
                    json={"code": _totp(secret)}).status_code == 200
    status = api.get("/api/v1/auth/mfa").json()
    assert status["enabled"] is False
    assert status["devices"] == []


@pytestmark_mfa
def test_the_factor_cannot_be_removed_without_a_current_code(api):
    """Holding a live session is not enough to remove the factor protecting
    it — or a borrowed laptop would be."""
    secret = api.post("/api/v1/auth/mfa/setup").json()["secret"]
    api.post("/api/v1/auth/mfa/enable", json={"code": _totp(secret)})
    assert api.post("/api/v1/auth/mfa/disable",
                    json={"code": "000000"}).status_code == 401
    assert api.get("/api/v1/auth/mfa").json()["enabled"] is True


@pytestmark_mfa
def test_codes_are_accepted_the_way_an_app_displays_them(api):
    """Every authenticator shows "123 456" and people copy what they see."""
    import pyotp

    secret = mfa.new_secret()
    code = pyotp.TOTP(secret).now()
    assert mfa.verify_code(secret, f"{code[:3]} {code[3:]}") is True
    assert mfa.verify_code(secret, "abcdef") is False
    assert mfa.verify_code(secret, "") is False
    assert mfa.verify_code(None, code) is False
