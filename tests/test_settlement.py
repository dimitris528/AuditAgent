"""Debt settlement — full, partial, and the balances that follow from it."""

import pytest

DEBT = {"client": "Νησίδα Café", "amount": 500.0,
        "type": "Χρεωστούμενο", "vat_rate": 0.24, "date": "2026-07-01"}


def _open_debt(api, **overrides):
    res = api.post("/api/transactions", json={**DEBT, **overrides})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _client_id(api, name="Νησίδα Café"):
    return next(c["id"] for c in api.get("/api/v1/clients").json()["clients"]
                if c["name"] == name)


def _drawer(api):
    return api.get(f"/api/v1/clients/{_client_id(api)}").json()


def _header(api):
    return api.get("/api/dashboard").json()["header"]


def test_partial_settlement_leaves_the_balance(api):
    """The worked example from the brief: €500 owed, €200 paid, €300 left."""
    debt_id = _open_debt(api)
    assert _header(api)["total_debt"] == 500.0

    res = api.post(f"/api/v1/transactions/{debt_id}/settle",
                   json={"amount": 200, "date": "2026-07-20"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["settled"] is False
    assert body["paid"] == 200.0
    assert body["remaining"] == 300.0

    header = _header(api)
    assert header["total_debt"] == 300.0
    # The €200 became revenue on the day it was received, not the day the debt
    # was raised.
    assert header["total_gross_rev"] == 200.0
    # 200 gross at 24% -> 200 * 0.24 / 1.24, rounded once.
    assert header["total_vat"] == 38.71


def test_partial_payments_accumulate_to_a_full_settlement(api):
    debt_id = _open_debt(api)
    api.post(f"/api/v1/transactions/{debt_id}/settle", json={"amount": 200})
    api.post(f"/api/v1/transactions/{debt_id}/settle", json={"amount": 150})

    final = api.post(f"/api/v1/transactions/{debt_id}/settle",
                     json={"amount": 150}).json()
    assert final["settled"] is True
    assert final["remaining"] == 0.0

    header = _header(api)
    assert header["total_debt"] == 0.0
    assert header["total_gross_rev"] == 500.0


def test_full_settlement_in_one_click(api):
    """No amount sent = pay off whatever is left — the "Πλήρης Εξόφληση"
    button, which cannot know the balance any better than the server does."""
    debt_id = _open_debt(api)
    res = api.post(f"/api/v1/transactions/{debt_id}/settle", json={})
    assert res.status_code == 200
    body = res.json()
    assert (body["ok"], body["id"], body["settled"]) == (True, debt_id, True)
    assert (body["paid"], body["remaining"]) == (500.0, 0.0)
    assert body["payment"]["kind"] == "full"

    header = _header(api)
    assert header["total_debt"] == 0.0
    assert header["total_gross_rev"] == 500.0
    assert header["total_vat"] == 96.77          # 500 * 0.24 / 1.24


def test_the_settled_row_stops_being_a_debt(api):
    debt_id = _open_debt(api)
    api.post(f"/api/v1/transactions/{debt_id}/settle", json={})
    row = next(t for t in _drawer(api)["transactions"] if t["id"] == debt_id)
    assert row["type"] == "Έσοδο"
    assert row["is_debt"] is False
    assert row["is_revenue"] is True


def test_a_partly_settled_debt_reports_paid_and_original(api):
    debt_id = _open_debt(api)
    api.post(f"/api/v1/transactions/{debt_id}/settle", json={"amount": 200})

    row = next(t for t in _drawer(api)["transactions"] if t["id"] == debt_id)
    assert row["is_debt"] is True
    assert row["remaining"] == 300.0
    assert row["paid"] == 200.0
    assert row["original"] == 500.0


def test_payment_history_is_logged(api):
    debt_id = _open_debt(api)
    api.post(f"/api/v1/transactions/{debt_id}/settle",
             json={"amount": 200, "date": "2026-07-20", "note": "Έναντι"})
    api.post(f"/api/v1/transactions/{debt_id}/settle",
             json={"amount": 300, "date": "2026-08-04"})

    log = api.get(f"/api/v1/transactions/{debt_id}/payments").json()
    assert log["paid"] == 500.0
    assert log["remaining"] == 0.0
    assert [(p["amount"], p["remaining"], p["kind"]) for p in log["payments"]] == [
        (300.0, 0.0, "full"),
        (200.0, 300.0, "partial"),
    ]
    assert log["payments"][1]["note"] == "Έναντι"
    assert log["payments"][1]["paid_date"] == "2026-07-20"


def test_history_survives_the_full_settlement_that_rewrites_the_row(api):
    """A full settlement turns the debt row into a revenue row, so the log is
    the only place the debt's history still exists."""
    debt_id = _open_debt(api)
    api.post(f"/api/v1/transactions/{debt_id}/settle", json={})
    drawer = _drawer(api)
    assert [p["kind"] for p in drawer["payments"]] == ["full"]
    assert drawer["payments"][0]["amount"] == 500.0


def test_overpayment_is_refused(api):
    debt_id = _open_debt(api)
    res = api.post(f"/api/v1/transactions/{debt_id}/settle", json={"amount": 600})
    assert res.status_code == 422
    assert "500.00" in res.json()["detail"]
    # Nothing moved.
    assert _header(api)["total_debt"] == 500.0


def test_settling_a_non_debt_row_is_refused(api):
    res = api.post("/api/transactions", json={
        "client": "Νησίδα Café", "amount": 124, "type": "Έσοδο", "vat_rate": 0.24})
    revenue_id = res.json()["id"]
    settle = api.post(f"/api/v1/transactions/{revenue_id}/settle", json={})
    assert settle.status_code == 422
    assert "χρεωστούμενο" in settle.json()["detail"]


def test_settling_an_already_paid_debt_is_refused(api):
    debt_id = _open_debt(api)
    api.post(f"/api/v1/transactions/{debt_id}/settle", json={})
    again = api.post(f"/api/v1/transactions/{debt_id}/settle", json={"amount": 10})
    assert again.status_code == 422


def test_another_tenants_debt_is_not_settleable(api, signup):
    debt_id = _open_debt(api)
    api.post("/api/v1/auth/register", json=signup(
        username="intruder", email="intruder@example.com",
        company_name="Γραφείο Εισβολέα", full_name="Εισβολέας Εισβολίδης",
        password="another-long-password"))
    token = api.post("/api/auth/login", json={
        "username": "intruder", "password": "another-long-password"}
    ).json()["access_token"]

    res = api.post(f"/api/v1/transactions/{debt_id}/settle", json={},
                   headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 404


def test_legacy_resolve_endpoint_settles_exactly_what_it_is_sent(api):
    """It used to flip the whole debt to revenue no matter the amount, and
    stamp VAT computed on the smaller figure. It now settles that amount."""
    debt_id = _open_debt(api)
    res = api.post(f"/api/transactions/{debt_id}/resolve",
                   json={"amount": 200, "vat_rate": 0.24})
    assert res.status_code == 200
    assert _header(api)["total_debt"] == 300.0
    assert _header(api)["total_gross_rev"] == 200.0


def test_vat_totals_still_reconcile_after_settlement(api):
    """The dashboard's summation guarantee: the header total is exactly the sum
    of the per-client figures, settlements included."""
    _open_debt(api)
    _open_debt(api, client="Άλλος Πελάτης", amount=248.0)
    for row in api.get("/api/transactions").json()["transactions"]:
        api.post(f"/api/v1/transactions/{row['id']}/settle", json={"amount": 100})

    payload = api.get("/api/dashboard").json()
    per_client = round(sum(c["metrics"]["net_vat"] for c in payload["clients"]), 2)
    assert payload["header"]["total_vat"] == per_client
    assert payload["header"]["total_debt"] == round(
        sum(c["metrics"]["debt"] for c in payload["clients"]), 2)
