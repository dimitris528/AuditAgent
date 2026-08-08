"""
Bulk delete and bulk archive — what a checkbox selection is allowed to destroy.

The interesting assertions are all about REFUSAL. A bulk action over a book is
one click away from removing figures somebody files a tax return against, so
these cover the two guards that matter: a client holding transactions is never
deleted, and a deleted transaction never leaves a settlement log entry pointing
at a row that no longer exists.
"""

import pytest

import finance


def make_client(api, name, afm=None):
    res = api.post("/api/v1/clients", json={"name": name, "afm": afm})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def make_txn(api, client, amount=124.0, type_="Έσοδο", **extra):
    body = {"client": client, "amount": amount, "type": type_,
            "vat_rate": 0.24, "date": "2026-01-15", **extra}
    res = api.post("/api/transactions", json=body)
    assert res.status_code == 201, res.text
    return res.json()["id"]


def client_names(api, archived=None):
    rows = api.get("/api/v1/clients").json()["clients"]
    if archived is not None:
        rows = [c for c in rows if c["archived"] is archived]
    return sorted(c["name"] for c in rows)


# --- Transactions ---------------------------------------------------------
def test_selected_transactions_are_deleted(api):
    keep = make_txn(api, "Νησίδα Café", 100.0)
    drop = [make_txn(api, "Νησίδα Café", 200.0),
            make_txn(api, "Νησίδα Café", 300.0)]

    res = api.post("/api/transactions/bulk-delete", json={"ids": drop})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["deleted"] == 2
    assert body["skipped"] == 0
    assert body["message"] == "Διαγράφηκαν 2 κινήσεις."

    remaining = api.get("/api/transactions").json()["transactions"]
    assert [t["id"] for t in remaining] == [keep]


def test_one_deleted_transaction_reads_as_singular_greek(api):
    one = make_txn(api, "Νησίδα Café")
    body = api.post("/api/transactions/bulk-delete", json={"ids": [one]}).json()
    assert body["message"] == "Διαγράφηκαν 1 κίνηση."


def test_ids_may_arrive_as_strings_or_numbers(api):
    """The dashboard serialises transaction ids as strings and client ids as
    numbers; one contract has to take both."""
    a = make_txn(api, "Νησίδα Café", 100.0)
    b = make_txn(api, "Νησίδα Café", 200.0)
    res = api.post("/api/transactions/bulk-delete",
                   json={"ids": [str(a), int(b)]})
    assert res.json()["deleted"] == 2


def test_an_unknown_id_is_skipped_and_reported_not_fatal(api):
    real = make_txn(api, "Νησίδα Café")
    body = api.post("/api/transactions/bulk-delete",
                    json={"ids": [real, 999999]}).json()
    assert body["deleted"] == 1
    assert body["skipped"] == 1


def test_deleting_a_settled_debt_takes_its_payment_log_with_it(api):
    """debt_payments.debt_id is a real foreign key. Left behind, Postgres
    refuses the delete outright — and a log entry pointing at a row that no
    longer exists is not history, it is a balance nobody can explain."""
    debt = make_txn(api, "Οφειλέτης", 500.0, type_=finance.DEBT_TYPE)
    settle = api.post(f"/api/v1/transactions/{debt}/settle",
                      json={"amount": 200.0})
    assert settle.status_code == 200, settle.text

    res = api.post("/api/transactions/bulk-delete", json={"ids": [debt]})
    assert res.status_code == 200, res.text
    assert res.json()["deleted"] == 1

    # The partial settlement's revenue row survives; it is money that was
    # actually received.
    rows = api.get("/api/transactions").json()["transactions"]
    assert [t["type"] for t in rows] == ["Έσοδο"]
    assert rows[0]["debt_id"] is None

    # Asserted against the table itself, not inferred from the API: the suite
    # runs on SQLite, which does not enforce foreign keys by default, so a
    # dangling debt_payments row would pass every HTTP-level check here and
    # fail only against Postgres in production.
    from sqlmodel import select

    from server import database
    from server.models import DebtPayment

    with database.session_scope() as session:
        assert session.exec(select(DebtPayment)).all() == []


def test_deleting_a_settlement_row_leaves_the_debt_standing(api):
    debt = make_txn(api, "Οφειλέτης", 500.0, type_=finance.DEBT_TYPE)
    api.post(f"/api/v1/transactions/{debt}/settle", json={"amount": 200.0})
    payment = next(t["id"] for t in api.get("/api/transactions").json()["transactions"]
                   if t["type"] == "Έσοδο")

    assert api.post("/api/transactions/bulk-delete",
                    json={"ids": [payment]}).json()["deleted"] == 1
    rows = api.get("/api/transactions").json()["transactions"]
    assert [t["type"] for t in rows] == [finance.DEBT_TYPE]


# --- Clients --------------------------------------------------------------
def test_clients_without_transactions_are_deleted(api):
    keep = make_client(api, "Κρατάμε")
    drop = [make_client(api, "Λάθος Καταχώρηση"), make_client(api, "Διπλότυπο")]

    body = api.post("/api/clients/bulk-delete", json={"ids": drop}).json()
    assert body["deleted"] == 2
    assert body["blocked"] == []
    assert body["message"] == "Διαγράφηκαν 2 πελάτες."
    assert client_names(api) == ["Κρατάμε"]
    assert keep


def test_a_client_holding_transactions_is_refused_not_cascaded(api):
    """The central guard. Deleting the client would either destroy booked
    history or orphan rows that finance.py still counts by name — the totals
    would stay put while the card vanished."""
    busy = make_client(api, "Νησίδα Café")
    make_txn(api, "Νησίδα Café", 124.0)
    empty = make_client(api, "Άδειος Πελάτης")

    body = api.post("/api/clients/bulk-delete",
                    json={"ids": [busy, empty]}).json()
    assert body["deleted"] == 1
    assert body["skipped"] == 1
    assert len(body["blocked"]) == 1

    blocked = body["blocked"][0]
    assert blocked["id"] == busy
    assert blocked["transactions"] == 1
    # The message has to name the alternative, or the user is simply stuck.
    assert "Αρχειοθετήστε" in blocked["message"]

    assert client_names(api) == ["Νησίδα Café"]
    assert len(api.get("/api/transactions").json()["transactions"]) == 1


def test_a_client_is_blocked_by_a_transaction_matched_only_by_name(api):
    """Rows written before client_id existed carry the name alone, and they
    still belong to the client — so they still block the delete."""
    client = make_client(api, "Νησίδα Café")
    with_db_row = api.post("/api/transactions", json={
        "client": "Νησίδα Café", "amount": 124.0, "type": "Έσοδο",
        "vat_rate": 0.24, "date": "2026-01-15"})
    assert with_db_row.status_code == 201

    from server import database, store
    from server.models import Transaction
    with database.session_scope() as session:
        row = session.get(Transaction, int(with_db_row.json()["id"]))
        row.client_id = None            # simulate the pre-FK shape
        session.add(row)
        session.commit()
        assert store is not None

    body = api.post("/api/clients/bulk-delete", json={"ids": [client]}).json()
    assert body["deleted"] == 0
    assert body["blocked"][0]["transactions"] == 1


# --- Archive --------------------------------------------------------------
def test_selected_clients_are_archived(api):
    ids = [make_client(api, "Πρώτος"), make_client(api, "Δεύτερος")]
    make_client(api, "Τρίτος")

    body = api.post("/api/clients/bulk-archive", json={"ids": ids}).json()
    assert body["changed"] == 2
    assert body["message"] == "Αρχειοθετήθηκαν 2 πελάτες."

    assert client_names(api, archived=True) == ["Δεύτερος", "Πρώτος"]
    assert client_names(api, archived=False) == ["Τρίτος"]


def test_archiving_a_client_that_holds_transactions_is_allowed(api):
    """The whole point of archive: it is what bulk-delete tells you to do with
    a client whose history has to stay."""
    client = make_client(api, "Νησίδα Café")
    make_txn(api, "Νησίδα Café")

    assert api.post("/api/clients/bulk-archive",
                    json={"ids": [client]}).json()["changed"] == 1
    assert client_names(api, archived=True) == ["Νησίδα Café"]
    # Nothing was destroyed.
    assert len(api.get("/api/transactions").json()["transactions"]) == 1


def test_an_archived_client_leaves_the_active_totals(api):
    client = make_client(api, "Νησίδα Café")
    make_txn(api, "Νησίδα Café", 124.0)

    before = api.get("/api/dashboard").json()
    assert before["counts"]["active_clients"] == 1
    assert [c["name"] for c in before["clients"]] == ["Νησίδα Café"]

    api.post("/api/clients/bulk-archive", json={"ids": [client]})
    after = api.get("/api/dashboard").json()
    assert after["counts"]["active_clients"] == 0
    assert [c["name"] for c in after["archived_clients"]] == ["Νησίδα Café"]


def test_archiving_is_reversible(api):
    client = make_client(api, "Νησίδα Café")
    api.post("/api/clients/bulk-archive", json={"ids": [client]})

    body = api.post("/api/clients/bulk-archive",
                    json={"ids": [client], "archived": False}).json()
    assert body["changed"] == 1
    assert body["message"] == "Επαναφέρθηκαν 1 πελάτης."
    assert client_names(api, archived=False) == ["Νησίδα Café"]


def test_a_client_already_archived_is_reported_rather_than_counted(api):
    """Selecting five of which two were already archived must say "3 archived,
    2 already were" — a bare 5 hides what actually happened."""
    already = make_client(api, "Ήδη Αρχειοθετημένος")
    fresh = make_client(api, "Καινούριος")
    api.post("/api/clients/bulk-archive", json={"ids": [already]})

    body = api.post("/api/clients/bulk-archive",
                    json={"ids": [already, fresh]}).json()
    assert body["changed"] == 1
    assert body["already"] == 1
    assert body["skipped"] == 1


# --- Contract + tenancy ---------------------------------------------------
@pytest.mark.parametrize("path", [
    "/api/transactions/bulk-delete",
    "/api/clients/bulk-delete",
    "/api/clients/bulk-archive",
])
def test_an_empty_selection_is_refused(api, path):
    assert api.post(path, json={"ids": []}).status_code == 422


@pytest.mark.parametrize("path", [
    "/api/transactions/bulk-delete",
    "/api/clients/bulk-delete",
    "/api/clients/bulk-archive",
])
def test_bulk_actions_require_a_session(path):
    from fastapi.testclient import TestClient
    from server.main import app

    with TestClient(app) as client:
        assert client.post(path, json={"ids": [1]}).status_code == 401


@pytest.mark.parametrize("path", [
    "/api/transactions/bulk-delete",
    "/api/clients/bulk-delete",
    "/api/clients/bulk-archive",
])
def test_one_tenant_cannot_touch_anothers_rows(api, path):
    """The ids are real — they simply belong to somebody else, and must be
    indistinguishable from ids that do not exist."""
    client = make_client(api, "Νησίδα Café")
    txn = make_txn(api, "Νησίδα Café")

    other = api.post("/api/v1/auth/register", json={
        "username": "outsider", "email": "outsider@example.com",
        "password": "correct-horse-battery"})
    token = other.json()["access_token"]

    target = txn if "transactions" in path else client
    res = api.post(path, json={"ids": [target]},
                   headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["skipped"] == 1

    # Untouched.
    assert client_names(api) == ["Νησίδα Café"]
    assert len(api.get("/api/transactions").json()["transactions"]) == 1
