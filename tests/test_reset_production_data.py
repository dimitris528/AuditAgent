"""
The production reset script — what it deletes, and more importantly what it
must not.

The dangerous failure for this script is not "it did not delete enough". It is
"it deleted an account", "it deleted another tenant's books", or "it deleted
something while the operator was doing a dry run". Every test here is aimed at
one of those three.

The suite's throwaway SQLite database is a fair stand-in: the script speaks
SQLAlchemy models rather than raw SQL, so the statements it emits are the same
ones the Postgres deployment gets. The one Postgres-only piece — restarting the
id sequences — is skipped there by database.is_postgres(), exactly as it is in
server/database.py's migrations.
"""

import pytest
from sqlalchemy import func, select

from scripts import reset_production_data as reset
from server import database, store
from server.models import Client, DebtPayment, Invoice, Transaction, User


def _seed(api, client_name="Πελάτης Α"):
    """A book with a client, a revenue row, and a settled debt (which is what
    puts a row in debt_payments)."""
    assert api.post("/api/v1/clients", json={"name": client_name}).status_code == 201
    assert api.post("/api/transactions", json={
        "client": client_name, "amount": 100, "type": "Έσοδο",
    }).status_code == 201
    debt = api.post("/api/transactions", json={
        "client": client_name, "amount": 500, "type": "Χρεωστούμενο",
    })
    assert debt.status_code == 201
    settled = api.post(f"/api/v1/transactions/{debt.json()['id']}/settle",
                       json={"amount": 200})
    assert settled.status_code == 200, settled.text


def _table_counts():
    with database.session_scope() as session:
        return {
            model.__tablename__: session.execute(
                select(func.count()).select_from(model)).scalar_one()
            for model in (DebtPayment, Invoice, Transaction, Client, User)
        }


def _second_tenant(api):
    """Register a second account and return a client authenticated as them."""
    res = api.post("/api/v1/auth/register", json={
        "username": "other",
        "email": "other@example.com",
        "password": "correct-horse-battery",
    })
    assert res.status_code == 201, res.text
    other = api.__class__(api.app)
    other.headers["Authorization"] = f"Bearer {res.json()['access_token']}"
    return other


# --------------------------------------------------------------------------
# The wipe itself
# --------------------------------------------------------------------------
def test_wipe_empties_the_books(api):
    _seed(api)
    before = _table_counts()
    assert before["transactions"] > 0 and before["clients"] > 0
    assert before["debt_payments"] > 0

    with database.session_scope() as session:
        deleted = reset.wipe(session)
        session.commit()

    after = _table_counts()
    assert after["transactions"] == 0
    assert after["clients"] == 0
    assert after["debt_payments"] == 0
    assert after["invoices"] == 0
    # …and the counts it reported are the counts it actually removed.
    assert deleted["transactions"] == before["transactions"]
    assert deleted["clients"] == before["clients"]


def test_wipe_keeps_every_account_and_its_credentials(api):
    """The whole point: an empty book, not an empty user table."""
    _seed(api)
    with database.session_scope() as session:
        before = store.get_user_by_username(session, "tester")
        password_hash = before.password_hash
        status = before.subscription_status

    with database.session_scope() as session:
        reset.wipe(session)
        session.commit()

    with database.session_scope() as session:
        after = store.get_user_by_username(session, "tester")
        assert after is not None
        assert after.password_hash == password_hash
        assert after.subscription_status == status

    # Not just present in the table — still able to log in.
    res = api.post("/api/auth/login",
                   json={"username": "tester", "password": "correct-horse-battery"})
    assert res.status_code == 200, res.text


def test_the_wiped_app_still_works(api):
    """The schema survived, so the account can start entering real data
    immediately rather than meeting an error about a missing table."""
    _seed(api)
    with database.session_scope() as session:
        reset.wipe(session)
        session.commit()

    assert api.get("/api/dashboard").status_code == 200
    assert api.get("/api/transactions").json()["transactions"] == []
    assert api.post("/api/v1/clients", json={"name": "Πρώτος Πραγματικός"}).status_code == 201
    assert api.post("/api/transactions", json={
        "client": "Πρώτος Πραγματικός", "amount": 42, "type": "Έσοδο",
    }).status_code == 201


def test_a_scoped_wipe_leaves_other_tenants_alone(api):
    """Deleting one tenant's rehearsal must not touch the neighbour's books."""
    _seed(api)
    other = _second_tenant(api)
    _seed(other, client_name="Πελάτης Β")

    with database.session_scope() as session:
        tester_id = store.get_user_by_username(session, "tester").id
        reset.wipe(session, user_id=tester_id)
        session.commit()

    # The other tenant still sees everything.
    rows = other.get("/api/transactions").json()
    assert len(rows) > 0
    assert other.get("/api/v1/clients").json()["clients"]
    # …and the wiped one sees nothing.
    assert api.get("/api/v1/clients").json()["clients"] == []


def test_users_are_only_deleted_when_explicitly_asked(api):
    _seed(api)
    with database.session_scope() as session:
        assert reset.wipe(session).get("users") is None
        session.commit()
    assert _table_counts()["users"] == 1

    with database.session_scope() as session:
        deleted = reset.wipe(session, include_users=True)
        session.commit()
    assert deleted["users"] == 1
    assert _table_counts()["users"] == 0


def test_counts_reports_what_is_there(api):
    _seed(api)
    with database.session_scope() as session:
        rows = reset.counts(session)
    assert rows["clients"] == 1
    assert rows["transactions"] >= 2
    # users is not in the default set — it is not part of what gets wiped.
    assert "users" not in rows
    with database.session_scope() as session:
        assert reset.counts(session, include_users=True)["users"] == 1


# --------------------------------------------------------------------------
# The safety catches
# --------------------------------------------------------------------------
def test_a_dry_run_deletes_nothing(api, capsys):
    _seed(api)
    before = _table_counts()

    assert reset.main([]) == 0

    assert _table_counts() == before
    assert "DRY RUN" in capsys.readouterr().out


def test_apply_without_the_confirmation_phrase_deletes_nothing(api):
    _seed(api)
    before = _table_counts()

    with pytest.raises(SystemExit):
        reset.main(["--apply"])
    assert _table_counts() == before

    with pytest.raises(SystemExit):
        reset.main(["--apply", "--confirm", "yes"])
    assert _table_counts() == before


def test_apply_with_the_phrase_wipes_and_keeps_the_account(api, capsys):
    _seed(api)
    assert reset.main(["--apply", "--confirm", reset.CONFIRM_PHRASE,
                       "--no-input"]) == 0

    after = _table_counts()
    assert after["transactions"] == 0
    assert after["clients"] == 0
    assert after["users"] == 1
    assert "User accounts kept: 1" in capsys.readouterr().out


def test_an_unknown_username_is_refused_before_anything_is_deleted(api):
    _seed(api)
    before = _table_counts()
    with pytest.raises(SystemExit):
        reset.main(["--user", "nobody", "--apply", "--confirm",
                    reset.CONFIRM_PHRASE, "--no-input"])
    assert _table_counts() == before


def test_the_statements_are_valid_postgres(api):
    """The suite runs on SQLite; production is PostgreSQL.

    Compiling each DELETE against the psycopg2 dialect is what makes the two
    the same claim rather than a hope — it is the exact SQL the deployment
    would receive, and a statement SQLite tolerates but Postgres rejects fails
    here instead of in front of a customer.
    """
    from sqlalchemy import delete
    from sqlalchemy.dialects import postgresql

    dialect = postgresql.dialect()
    for model in reset.DATA_TABLES + reset.AUTH_TABLES:
        sql = str(delete(model).compile(dialect=dialect))
        assert sql.startswith(f"DELETE FROM {model.__tablename__}")
        # Never a DROP or a TRUNCATE: the schema has to survive this script.
        assert "DROP" not in sql.upper() and "TRUNCATE" not in sql.upper()

        scoped = str(reset._scoped(delete(model), model, 7)
                     .compile(dialect=dialect))
        assert "WHERE" in scoped


def test_sequences_are_only_touched_on_postgres(api, monkeypatch):
    """pg_get_serial_sequence does not exist on SQLite, so the call has to be
    skipped rather than raise — the same guard server/database.py uses for its
    Postgres-only migrations."""
    monkeypatch.setattr(database, "is_postgres", lambda *a, **k: False)
    with database.session_scope() as session:
        assert reset.reset_sequences(session, reset.DATA_TABLES) == []


def test_the_printed_target_never_contains_the_password(monkeypatch):
    """This string goes to a terminal and, in CI, to a log."""
    monkeypatch.setattr(
        database, "DATABASE_URL",
        "postgresql://postgres.abc:sup3r-s3cret@db.example.com:6543/postgres")
    # The engine cache is not touched: _require_url() re-reads the module value.
    shown = reset._target_description()
    assert "sup3r-s3cret" not in shown
    assert "db.example.com" in shown
