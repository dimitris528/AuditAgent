"""
The owner-account setup script.

The dangerous failures here are quieter than the wipe script's. Nothing is
deleted, so the ways this hurts are: an account that silently expires two weeks
after launch (`active` written alongside a trial date), an account whose
password does not actually let anyone in, and a re-run that overwrites a live
credential without being asked to. Every test below aims at one of those.
"""

import pytest

from scripts import create_admin
from server import database, store, subscription
from server.models import User
from sqlalchemy import delete


@pytest.fixture()
def db(api):
    """An empty database with no accounts at all.

    Builds on the `api` fixture only to reuse its per-test wipe, then removes
    the account that fixture registers — this script's whole subject is what
    happens on a database with no users in it.
    """
    with database.session_scope() as session:
        session.execute(delete(User))
        session.commit()
    yield


def _create(email="owner@example.com", username="owner",
            password="correct-horse-battery", **kw):
    with database.session_scope() as session:
        user, created = create_admin.create_admin(
            session, email, username, password, **kw)
        # Read the fields off inside the session; the object is detached after.
        return {
            "id": user.id,
            "email": user.email,
            "username": user.username,
            "status": user.subscription_status,
            "trial_ends_at": user.trial_ends_at,
            "password_hash": user.password_hash,
        }, created


# --------------------------------------------------------------------------
# The account it produces
# --------------------------------------------------------------------------
def test_creates_an_active_account(db):
    user, created = _create()
    assert created is True
    assert user["email"] == "owner@example.com"
    assert user["username"] == "owner"
    assert user["status"] == subscription.ACTIVE


def test_the_account_carries_no_trial_date(db):
    """The invariant the whole script exists for.

    `active` WITH a trial date is a self-expiring account: subscription.resolve
    reads a present date as "this is a trial" whatever the status column says,
    so the owner would be locked to read-only when it passed.
    """
    user, _ = _create()
    assert user["trial_ends_at"] is None

    state = subscription.resolve(user["status"], user["trial_ends_at"])
    assert state.status == subscription.ACTIVE
    assert state.allows_writes is True
    assert state.is_trialing is False


def test_the_account_still_resolves_active_long_after_a_trial_would_have_ended(db):
    """Time travel: a year out, the owner can still write."""
    import datetime as dt

    user, _ = _create()
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=365)
    state = subscription.resolve(user["status"], user["trial_ends_at"], now=later)
    assert state.status == subscription.ACTIVE
    assert state.allows_writes is True


def test_the_email_is_lower_cased_and_stripped(db):
    """Login looks the row up case-insensitively; storing it folded keeps the
    stored value and the lookup agreeing."""
    user, _ = _create(email="  Owner@Example.COM  ")
    assert user["email"] == "owner@example.com"


def test_the_password_is_stored_hashed_and_verifies(db):
    import passwords

    user, _ = _create(password="a-real-password-1")
    assert user["password_hash"] != "a-real-password-1"
    assert passwords.is_hashed(user["password_hash"])
    assert passwords.verify_password(user["password_hash"], "a-real-password-1")
    assert not passwords.verify_password(user["password_hash"], "wrong")


def test_the_owner_can_actually_log_in(api, db):
    """End to end through the real endpoint — a created account that cannot log
    in is the failure that would be discovered at launch."""
    _create(password="a-real-password-1")
    res = api.post("/api/auth/login", json={
        "username": "owner", "password": "a-real-password-1",
    })
    assert res.status_code == 200, res.text
    assert res.json()["subscription"]["status"] == subscription.ACTIVE
    assert res.json()["subscription"]["allows_writes"] is True


def test_the_owner_can_write_immediately(api, db):
    """The paywall gates writes, not reads. An owner who cannot POST a client
    on day one has not been set up."""
    _create(password="a-real-password-1")
    res = api.post("/api/auth/login", json={
        "username": "owner", "password": "a-real-password-1",
    })
    owner = api.__class__(api.app)
    owner.headers["Authorization"] = f"Bearer {res.json()['access_token']}"

    created = owner.post("/api/v1/clients", json={"name": "Πρώτος Πελάτης"})
    assert created.status_code == 201, created.text


# --------------------------------------------------------------------------
# Re-runs must not quietly overwrite a live credential
# --------------------------------------------------------------------------
def test_a_duplicate_is_refused_by_default(db):
    _create()
    with pytest.raises(SystemExit) as exc:
        _create()
    assert "already exists" in str(exc.value)


def test_a_duplicate_email_is_refused_even_under_a_new_username(db):
    _create(email="owner@example.com", username="owner")
    with pytest.raises(SystemExit):
        _create(email="owner@example.com", username="someone-else")


def test_a_duplicate_username_is_refused_even_under_a_new_email(db):
    _create(email="owner@example.com", username="owner")
    with pytest.raises(SystemExit):
        _create(email="different@example.com", username="owner")


def test_the_refusal_does_not_change_the_stored_password(db):
    import passwords

    first, _ = _create(password="original-password")
    with pytest.raises(SystemExit):
        _create(password="attempted-overwrite")

    with database.session_scope() as session:
        user = store.get_user_by_email(session, "owner@example.com")
        assert passwords.verify_password(user.password_hash, "original-password")


def test_reset_password_updates_in_place(db):
    import passwords

    first, _ = _create(password="original-password")
    second, created = _create(password="new-password-2", reset_password=True)

    assert created is False
    # The same row, not a second account.
    assert second["id"] == first["id"]
    assert passwords.verify_password(second["password_hash"], "new-password-2")
    assert not passwords.verify_password(second["password_hash"], "original-password")


def test_reset_password_reactivates_a_lapsed_account(db):
    """The realistic repair case: the owner's account fell to `inactive` (or was
    left on a trial), and the fix must clear the trial date as well as the
    status — otherwise it expires again."""
    import datetime as dt

    with database.session_scope() as session:
        store.create_user(
            session, username="owner", email="owner@example.com",
            password_hash="x",
            subscription_status=subscription.TRIALING,
            trial_ends_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1),
        )

    user, created = _create(reset_password=True)
    assert created is False
    assert user["status"] == subscription.ACTIVE
    assert user["trial_ends_at"] is None
    assert subscription.resolve(user["status"], user["trial_ends_at"]).allows_writes


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------
def test_the_password_minimum_matches_the_api(db):
    """The constant is duplicated in the script (see its docstring). If the API
    ever raises its minimum, this fails rather than letting the script create an
    account whose password the API's own reset flow would reject."""
    from server.main import MIN_PASSWORD_LENGTH as api_minimum

    assert create_admin.MIN_PASSWORD_LENGTH == api_minimum


def test_a_short_password_is_refused_before_anything_is_written(db, monkeypatch):
    monkeypatch.setattr(create_admin.getpass, "getpass", lambda *a, **k: "short")
    with pytest.raises(SystemExit) as exc:
        create_admin.prompt_password()
    assert "at least" in str(exc.value)


def test_mismatched_passwords_are_refused(db, monkeypatch):
    typed = iter(["a-long-enough-password", "a-different-password"])
    monkeypatch.setattr(create_admin.getpass, "getpass",
                        lambda *a, **k: next(typed))
    with pytest.raises(SystemExit) as exc:
        create_admin.prompt_password()
    assert "did not match" in str(exc.value)


def test_the_password_cannot_be_passed_as_an_argument(db):
    """A --password flag would put the owner's production credential into shell
    history and `ps`. Asserted because it is a property worth keeping."""
    with pytest.raises(SystemExit):
        # argparse exits 2 on an unrecognised argument.
        create_admin.main(["--password", "hunter2"])


def test_the_printed_target_never_contains_the_password(monkeypatch):
    """Same guarantee the wipe script makes: the connection string is echoed so
    the operator can confirm the database, and it must be echoed with the
    password removed."""
    monkeypatch.setattr(
        database, "DATABASE_URL",
        "postgresql://postgres.abc:sup3r-s3cret@db.example.com:6543/postgres")
    rendered = create_admin._target_description()
    assert "sup3r-s3cret" not in rendered
    assert "db.example.com" in rendered
