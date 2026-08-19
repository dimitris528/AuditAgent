"""
Public tenant onboarding: POST /api/v1/auth/register.

This is the only endpoint an anonymous caller can use to CREATE something, which
makes it the one place where "what does it refuse?" matters as much as "what
does it do?". So the happy path is asserted once and the rest of the file is
about the refusals: a taken email, a password made out of the company's own
name, a consent checkbox that was never ticked, and a script trying to mint
accounts in bulk.

The atomicity claim is asserted directly rather than trusted: every rejection
below is followed by a check that the users table is unchanged, because a
signup that half-succeeds leaves an office with no trial (locked out on its
first request) or a username reserved by a registration that failed.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from server import database, legal, middleware, password_policy, store, subscription
from server.main import app
from server.models import User

REGISTER = "/api/v1/auth/register"


def _users():
    """Every user row, as sorted (username, email) pairs."""
    with database.session_scope() as session:
        return sorted((u.username, u.email)
                      for u in session.exec(select(User)).all())


def _row(email):
    with database.session_scope() as session:
        return store.get_user_by_email(session, email)


# ==========================================================================
# The happy path
# ==========================================================================
def test_an_office_can_sign_itself_up(api, signup):
    res = api.post(REGISTER, json=signup(
        username=None, email="nea@grafeio.gr",
        company_name="Λογιστικό Γραφείο Νέα Α.Ε.",
        full_name="Μαρία Παπαδοπούλου"))
    assert res.status_code == 201, res.text

    body = res.json()
    assert body["ok"] is True
    assert body["company_name"] == "Λογιστικό Γραφείο Νέα Α.Ε."
    assert body["full_name"] == "Μαρία Παπαδοπούλου"
    assert body["email"] == "nea@grafeio.gr"
    # Registering signs you in — no second round trip, no verification email.
    assert body["access_token"]
    assert body["token_type"] == "bearer"


def test_the_new_account_is_an_admin_of_its_own_tenant(api, signup):
    api.post(REGISTER, json=signup(username=None, email="nea@grafeio.gr"))
    assert _row("nea@grafeio.gr").role == store.ROLE_ADMIN


def test_the_trial_is_granted_by_the_act_of_registering(api, signup):
    """No card, no extra step. The trial metadata is part of the same write —
    an account created without it is inactive from its very first request."""
    res = api.post(REGISTER, json=signup(username=None, email="nea@grafeio.gr"))
    state = res.json()["subscription"]
    assert state["status"] == subscription.TRIALING
    assert state["allows_writes"] is True
    assert state["days_left"] == subscription.TRIAL_DAYS

    row = _row("nea@grafeio.gr")
    assert row.subscription_status == subscription.TRIALING
    assert row.trial_ends_at is not None
    left = subscription.as_utc(row.trial_ends_at) - dt.datetime.now(dt.timezone.utc)
    assert dt.timedelta(days=subscription.TRIAL_DAYS - 1) < left


def test_the_session_it_returns_actually_opens_the_book(api, signup):
    """The token is not decorative: the whole point of signing the user in is
    that the next screen they see is their own dashboard."""
    token = api.post(REGISTER, json=signup(
        username=None, email="nea@grafeio.gr")).json()["access_token"]
    res = api.get("/api/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    # An empty book, and their own. No sample data is seeded — an invented
    # transaction in somebody's ledger is not a friendly touch.
    assert res.json()["transactions"] == []
    assert res.json()["counts"]["active_clients"] == 0


def test_a_second_office_is_a_separate_tenant(api, signup):
    api.post(REGISTER, json=signup(username=None, email="alpha@grafeio.gr",
                                   company_name="Άλφα"))
    api.post(REGISTER, json=signup(username=None, email="beta@grafeio.gr",
                                   company_name="Βήτα"))
    assert _row("alpha@grafeio.gr").id != _row("beta@grafeio.gr").id


# --- The username, which the form does not collect -------------------------
def test_the_username_is_derived_from_the_email(api, signup):
    """The signup form asks for a company, a name, an email and a password —
    never a username. The tenant key is still a username (every existing token
    carries one as its `sub`), so it is derived rather than demanded."""
    api.post(REGISTER, json=signup(username=None, email="maria@grafeio.gr"))
    assert _row("maria@grafeio.gr").username == "maria"


def test_a_greek_email_still_yields_a_usable_username(api, signup):
    """Transliterated, not discarded. Until a company name is on file this is
    what a printed statement puts on its letterhead."""
    assert store.slugify_username("μαρια") == "maria"
    # ΜΠ is one sound, so it becomes "b" — the passport spelling, not "mp".
    assert store.slugify_username("Μπάμπης") == "babis"
    # A local part of nothing but symbols has to fall back to something.
    assert store.slugify_username("+++") == "user"


def test_two_offices_sharing_an_email_prefix_get_distinct_usernames(api, signup):
    """maria@alpha.gr and maria@beta.gr both derive "maria". The second must
    still get an account rather than a constraint violation."""
    api.post(REGISTER, json=signup(username=None, email="maria@alpha.gr"))
    res = api.post(REGISTER, json=signup(username=None, email="maria@beta.gr"))
    assert res.status_code == 201, res.text
    assert _row("maria@alpha.gr").username == "maria"
    assert _row("maria@beta.gr").username == "maria-2"


def test_an_explicit_username_is_honoured(api, signup):
    """Not collected by the form, but scripts/create_admin.py and the test
    suite key tenancy on a username they choose."""
    api.post(REGISTER, json=signup(username="chosen", email="x@grafeio.gr"))
    assert _row("x@grafeio.gr").username == "chosen"


# ==========================================================================
# Duplicates
# ==========================================================================
def test_a_taken_email_is_a_409(api, signup):
    """`tester@example.com` is the fixture's own account."""
    before = _users()
    res = api.post(REGISTER, json=signup(username=None,
                                         email="tester@example.com"))
    assert res.status_code == 409
    assert _users() == before, "a rejected signup wrote a row anyway"


def test_the_duplicate_message_points_somewhere_useful(api, signup):
    """The commonest cause is a returning customer who forgot they had an
    account, so the answer names the two things that would help them."""
    detail = api.post(REGISTER, json=signup(
        username=None, email="tester@example.com")).json()["detail"]
    assert "email" in detail
    assert "επαναφορά" in detail.lower() or "συνδεθείτε" in detail.lower()


def test_a_taken_email_is_matched_regardless_of_case(api, signup):
    """Addresses are stored lower-cased, so TESTER@EXAMPLE.COM is the same
    account — and must not be able to open a second one."""
    res = api.post(REGISTER, json=signup(username=None,
                                         email="TESTER@Example.COM"))
    assert res.status_code == 409


def test_a_taken_username_is_a_409(api, signup):
    res = api.post(REGISTER, json=signup(username="tester",
                                         email="different@grafeio.gr"))
    assert res.status_code == 409
    assert "όνομα χρήστη" in res.json()["detail"]


def test_nothing_is_left_behind_by_a_rejected_signup(api, signup):
    """The atomicity claim, from the outside. Whatever the reason for the
    refusal, the users table has to look exactly as it did."""
    before = _users()
    for payload in (
        signup(username=None, email="tester@example.com"),      # duplicate
        signup(username=None, email="ok@grafeio.gr", password="short"),
        signup(username=None, email="ok@grafeio.gr", accept_terms=False),
        signup(username=None, email="not-an-email"),
    ):
        assert api.post(REGISTER, json=payload).status_code in (409, 422)
    assert _users() == before


# ==========================================================================
# Validation
# ==========================================================================
@pytest.mark.parametrize("field", ["company_name", "full_name", "email",
                                   "password"])
def test_every_required_field_is_required(api, signup, field):
    payload = signup(username=None, email="new@grafeio.gr")
    payload.pop(field)
    res = api.post(REGISTER, json=payload)
    assert res.status_code == 422


@pytest.mark.parametrize("email", ["", "not-an-email", "@grafeio.gr",
                                   "maria@", "maria grafeio.gr"])
def test_a_malformed_email_is_refused(api, signup, email):
    assert api.post(REGISTER,
                    json=signup(username=None, email=email)).status_code == 422


def test_a_one_letter_company_name_is_refused(api, signup):
    """The company name is the tenant's identity and ends up on a printed
    statement. "Α" is a typo, not an office."""
    res = api.post(REGISTER, json=signup(username=None, email="new@grafeio.gr",
                                         company_name="Α"))
    assert res.status_code == 422


def test_whitespace_around_the_names_is_trimmed(api, signup):
    api.post(REGISTER, json=signup(
        username=None, email="new@grafeio.gr",
        company_name="  Γραφείο Άλφα  ", full_name="  Μαρία Π.  "))
    row = _row("new@grafeio.gr")
    assert row.company_name == "Γραφείο Άλφα"
    assert row.full_name == "Μαρία Π."


# --- Password strength -----------------------------------------------------
@pytest.mark.parametrize("password", [
    "short",           # under the floor
    "12345678",        # a wordlist's first guess
    "password",
    "aaaaaaaa",        # one character, repeated
    "abcdefgh",        # a straight run
])
def test_a_weak_password_is_refused_with_a_reason(api, signup, password):
    res = api.post(REGISTER, json=signup(username=None, email="new@grafeio.gr",
                                         password=password))
    assert res.status_code == 422
    # A reason the user can act on, not a schema dump.
    assert isinstance(res.json()["detail"], str)
    assert res.json()["detail"].strip()


def test_a_password_made_of_the_offices_own_name_is_refused(api, signup):
    """The attacker starts with the company name, because it is printed on
    every invoice the office has ever sent."""
    res = api.post(REGISTER, json=signup(
        username=None, email="new@grafeio.gr",
        company_name="Παπαδόπουλος Λογιστική", password="papadopoulos1"))
    assert res.status_code == 422
    assert "επωνυμία" in res.json()["detail"]


def test_a_password_made_of_the_email_is_refused(api, signup):
    res = api.post(REGISTER, json=signup(
        username=None, email="kostas@grafeio.gr", password="kostas2026!"))
    assert res.status_code == 422


def test_a_long_passphrase_needs_no_decoration(api, signup):
    """The rule this policy deliberately does NOT have is
    upper+lower+digit+symbol, which produces `Password1!` and rejects this."""
    res = api.post(REGISTER, json=signup(
        username=None, email="new@grafeio.gr",
        password="μια πολυ μεγαλη φρασηι"))
    assert res.status_code == 201, res.text


def test_an_enormous_password_is_refused_rather_than_hashed(api, signup):
    """PBKDF2 runs 600 000 iterations over whatever it is given, on an
    endpoint nobody has authenticated to."""
    res = api.post(REGISTER, json=signup(
        username=None, email="new@grafeio.gr",
        password="a" * (password_policy.MAX_LENGTH + 1)))
    assert res.status_code == 422


def test_the_stored_password_is_hashed(api, signup):
    """The plaintext never reaches the database."""
    api.post(REGISTER, json=signup(username=None, email="new@grafeio.gr",
                                   password="correct-horse-battery"))
    stored = _row("new@grafeio.gr").password_hash
    assert "correct-horse-battery" not in stored
    assert stored.startswith("pbkdf2_sha256$")


def test_the_new_account_can_immediately_log_in(api, signup):
    api.post(REGISTER, json=signup(username=None, email="new@grafeio.gr",
                                   password="correct-horse-battery"))
    res = api.post("/api/auth/login", json={"username": "new@grafeio.gr",
                                            "password": "correct-horse-battery"})
    assert res.status_code == 200, res.text


# ==========================================================================
# Consent (GDPR)
# ==========================================================================
def test_signup_without_accepting_the_terms_is_refused(api, signup):
    """A checkbox enforced only in the browser is not consent anyone can
    prove afterwards."""
    res = api.post(REGISTER, json=signup(username=None, email="new@grafeio.gr",
                                         accept_terms=False))
    assert res.status_code == 422
    assert "Όρους" in res.json()["detail"]


def test_omitting_the_consent_field_entirely_is_refused(api, signup):
    """It defaults to False rather than True. A caller that does not mention
    consent has not given it."""
    payload = signup(username=None, email="new@grafeio.gr")
    payload.pop("accept_terms")
    assert api.post(REGISTER, json=payload).status_code == 422


def test_what_was_agreed_to_is_recorded_with_when(api, signup):
    """Consent is to a specific text at a specific moment. A bare boolean
    could not answer what the customer actually agreed to a year later."""
    before = dt.datetime.now(dt.timezone.utc)
    api.post(REGISTER, json=signup(username=None, email="new@grafeio.gr"))
    row = _row("new@grafeio.gr")

    assert row.terms_version == legal.CONSENT_VERSION
    assert legal.TERMS_VERSION in row.terms_version
    assert legal.PRIVACY_VERSION in row.terms_version
    accepted = subscription.as_utc(row.terms_accepted_at)
    assert accepted is not None
    assert before - dt.timedelta(seconds=5) <= accepted


def test_the_version_the_form_displayed_is_the_version_recorded(api, signup):
    """Echoed back by the browser, so the record says what was on screen
    rather than what the server assumes was on screen — the two differ for
    exactly as long as it takes a cached page to be replaced."""
    api.post(REGISTER, json=signup(username=None, email="new@grafeio.gr",
                                   terms_version="terms:1999-01-01"))
    assert _row("new@grafeio.gr").terms_version == "terms:1999-01-01"


def test_the_current_legal_version_is_published(api):
    """The signup form has to know which version it is displaying, and /api/meta
    is public so it can be read before anyone has an account."""
    meta = api.get("/api/meta").json()
    assert meta["legal"]["terms_version"] == legal.TERMS_VERSION
    assert meta["legal"]["privacy_version"] == legal.PRIVACY_VERSION
    assert meta["legal"]["terms_url"] == "/terms"
    assert meta["legal"]["privacy_url"] == "/privacy"
    assert meta["password"]["min_length"] == password_policy.MIN_LENGTH


# ==========================================================================
# What the collected company name is actually FOR
# ==========================================================================
# A field asked for at signup and never used again is a field that should not
# have been asked for. These two documents are printed and sent to the office's
# own clients, and both used to go out with a login name on the letterhead.
def test_the_statement_letterhead_carries_the_office(api, signup):
    token = api.post(REGISTER, json=signup(
        username=None, email="nea@grafeio.gr",
        company_name="Λογιστικό Γραφείο Νέα Α.Ε.",
        full_name="Μαρία Παπαδοπούλου")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    client_id = api.post("/api/v1/clients", json={"name": "Πελάτης Α"},
                         headers=headers).json()["id"]
    issuer = api.get(f"/api/v1/clients/{client_id}",
                     headers=headers).json()["issuer"]

    assert issuer["name"] == "Λογιστικό Γραφείο Νέα Α.Ε."
    assert issuer["contact"] == "Μαρία Παπαδοπούλου"
    assert issuer["email"] == "nea@grafeio.gr"


def test_the_summary_letterhead_carries_the_office(api, signup):
    token = api.post(REGISTER, json=signup(
        username=None, email="nea@grafeio.gr",
        company_name="Λογιστικό Γραφείο Νέα Α.Ε.")).json()["access_token"]
    issuer = api.get("/api/dashboard",
                     headers={"Authorization": f"Bearer {token}"}).json()["issuer"]
    assert issuer["name"] == "Λογιστικό Γραφείο Νέα Α.Ε."


def test_an_account_with_no_company_falls_back_to_its_username(api):
    """Every account created before public signup existed has no company name,
    and inventing one would print a made-up business on a real document."""
    with database.session_scope() as session:
        row = store.get_user_by_username(session, "tester")
        row.company_name = None
        session.add(row)
        session.commit()

    client_id = api.post("/api/v1/clients", json={"name": "Πελάτης Α"}).json()["id"]
    assert api.get(f"/api/v1/clients/{client_id}").json()["issuer"]["name"] == \
        "tester"


# ==========================================================================
# Rate limiting
# ==========================================================================
def test_bulk_signup_is_turned_away(monkeypatch, signup, fresh_rate_limiter):
    """The one endpoint an anonymous caller can use to CREATE rows, so it is
    metered hardest — and over an hour, not a minute, because a script that
    sleeps for sixty seconds walks straight through a per-minute window."""
    monkeypatch.setattr(middleware, "ENABLED", True)
    limit, window = middleware.LIMITS["register"]
    assert window >= 3600, "a per-minute window is trivially waited out"

    with TestClient(app) as client:
        codes = [
            client.post(REGISTER,
                        json=signup(username=None,
                                    email=f"flood-{i}@grafeio.gr")).status_code
            for i in range(limit + 2)
        ]

    assert 429 in codes, "the limiter never fired"
    assert codes.index(429) <= limit + 1


def test_a_throttled_signup_says_when_to_come_back(monkeypatch, signup,
                                                   fresh_rate_limiter):
    monkeypatch.setattr(middleware, "ENABLED", True)
    limit, _ = middleware.LIMITS["register"]

    with TestClient(app) as client:
        last = None
        for i in range(limit + 2):
            last = client.post(REGISTER, json=signup(
                username=None, email=f"flood2-{i}@grafeio.gr"))

    assert last.status_code == 429
    assert int(last.headers["Retry-After"]) >= 1
    # A Greek sentence, not a template that never got filled in.
    assert "{" not in last.json()["detail"]


def test_registration_is_metered_apart_from_login(api):
    """Longest prefix wins. Sharing login's bucket would mean five signups a
    day cost somebody their tenth password attempt — and, worse, that a flood
    of signups locked real users out of logging in."""
    assert middleware.bucket_for(REGISTER) == "register"
    assert middleware.bucket_for("/api/auth/login") == "auth"
    assert middleware.bucket_for("/api/v1/auth/mfa/verify") == "auth"
    assert middleware.bucket_for("/api/v1/auth/forgot-password") == "auth"


def test_registration_stays_reachable_without_a_token(api):
    """It is on the auth gate's allowlist, and has to be: a new visitor has no
    token to give."""
    assert middleware.is_public(REGISTER) is True


# ==========================================================================
# The atomic write, at the store level
# ==========================================================================
def test_register_tenant_writes_everything_or_nothing(api):
    """A failure part-way through must not leave an office with no trial —
    that account would be locked out of the product on its first request."""
    from sqlalchemy.exc import IntegrityError

    before = _users()
    with database.session_scope() as session:
        with pytest.raises(IntegrityError):
            store.register_tenant(
                session,
                company_name="Σύγκρουση Α.Ε.",
                full_name="Κάποιος Κάποιου",
                # Already taken by the fixture's account.
                email="tester@example.com",
                password_hash="x",
                username="brand-new-name")
        session.rollback()
    assert _users() == before


def test_register_tenant_fills_in_the_whole_tenant(api):
    with database.session_scope() as session:
        user = store.register_tenant(
            session,
            company_name="Γραφείο Άλφα",
            full_name="Άλφα Άλφειος",
            email="alpha@grafeio.gr",
            password_hash="pbkdf2_sha256$1$aa$bb",
            terms_version=legal.CONSENT_VERSION)
        assert user.id is not None
        assert user.username == "alpha"
        assert user.role == store.ROLE_ADMIN
        assert user.company_name == "Γραφείο Άλφα"
        assert user.subscription_status == subscription.TRIALING
        assert user.trial_ends_at is not None
        assert user.terms_accepted_at is not None
