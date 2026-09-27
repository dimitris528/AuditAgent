"""
The demo seed script.

Its credentials are public, so the properties worth pinning are: the account
it produces can actually sign in, the books it writes reconcile like a real
tenant's, a re-run restores the published state (including undoing 2FA a
visitor switched on), and it never touches anyone else's rows.
"""

import datetime as dt

import pytest

from scripts import seed_demo
from server import database, store
from server.models import Client, Transaction, User
from sqlmodel import select


def _seed():
    with database.session_scope() as session:
        user, created, n_clients, n_txns = seed_demo.seed(
            session, today=dt.date(2026, 9, 27))
        return user.id, created, n_clients, n_txns


def _rows(model, user_id):
    with database.session_scope() as session:
        return session.exec(select(model).where(model.user_id == user_id)).all()


def test_creates_the_demo_account_with_sample_books(api):
    user_id, created, n_clients, n_txns = _seed()
    assert created is True
    assert len(_rows(Client, user_id)) == n_clients == len(seed_demo.CLIENTS)
    assert len(_rows(Transaction, user_id)) == n_txns == len(seed_demo.TRANSACTIONS)


def test_demo_credentials_sign_in(api):
    _seed()
    api.headers.pop("Authorization", None)
    res = api.post("/api/auth/login", json={"username": seed_demo.DEMO_EMAIL,
                                            "password": seed_demo.DEMO_PASSWORD})
    assert res.status_code == 200, res.text
    assert res.json().get("access_token")
    assert not res.json().get("mfa_required")


def test_rows_follow_the_booking_conventions(api):
    user_id, *_ = _seed()
    rows = _rows(Transaction, user_id)
    for row in rows:
        if row.type == "Έξοδο":
            assert row.amount < 0
        elif row.doc_type == "Πιστωτικό":
            assert row.amount < 0 and row.vat_amount < 0
        else:
            assert row.amount > 0
        if row.type == "Χρεωστούμενο":
            assert row.vat_amount is None and row.due_date is not None
    assert any(r.type == "Χρεωστούμενο" and r.due_date < dt.date(2026, 9, 27)
               for r in rows), "the debt alerts need an overdue row"


def test_rerun_resets_instead_of_duplicating(api):
    user_id, *_ = _seed()
    with database.session_scope() as session:
        user = session.get(User, user_id)
        user.mfa_enabled, user.mfa_secret = True, "JBSWY3DPEHPK3PXP"
        user.password_hash = "changed"
        session.add(user)
        session.commit()

    again_id, created, *_ = _seed()
    assert again_id == user_id and created is False
    assert len(_rows(Transaction, user_id)) == len(seed_demo.TRANSACTIONS)
    assert len(_rows(Client, user_id)) == len(seed_demo.CLIENTS)
    with database.session_scope() as session:
        user = session.get(User, user_id)
        assert user.mfa_enabled is False and user.mfa_secret is None
        assert store.refresh_subscription(session, user).allows_writes


def test_leaves_other_tenants_alone(api):
    api.post("/api/v1/clients", json={"name": "Πραγματικός Πελάτης"})
    with database.session_scope() as session:
        other = store.get_user_by_username(session, "tester")
        before = len(session.exec(
            select(Client).where(Client.user_id == other.id)).all())
    assert before == 1

    _seed()
    _seed()
    assert len(_rows(Client, other.id)) == before


def test_refuses_to_reset_a_real_account_on_the_demo_email(api):
    with database.session_scope() as session:
        store.create_user(session, username="someone", email=seed_demo.DEMO_EMAIL,
                          password_hash="x")
    with pytest.raises(SystemExit, match="Refusing"):
        _seed()
