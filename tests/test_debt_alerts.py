"""Overdue / outstanding debt alerts."""

import datetime as dt

import pytest

import finance

TODAY = dt.date(2026, 8, 4)


def _debt(amount, issued, due=None, name="Πελάτης", txn_id="1"):
    """A debt in the finance record shape build_debt_alerts consumes."""
    return {
        "id": txn_id,
        "createdTime": None,
        "fields": {
            "Category": name,
            "Amount": amount,
            "Type": finance.DEBT_TYPE,
            "Date": issued,
            "DueDate": due,
        },
    }


def _project(name, project_id):
    return {"id": project_id, "fields": {"Name": name}}


# --- Due dates ------------------------------------------------------------
def test_an_explicit_due_date_wins():
    debt = _debt(100, "2026-07-01", due="2026-07-10")
    assert finance.debt_due_date(debt) == dt.date(2026, 7, 10)


def test_a_missing_due_date_is_inferred_from_the_issue_date():
    """Rows written before due dates existed are the OLDEST debts — exactly
    the ones worth alerting on — so they must still age."""
    debt = _debt(100, "2026-07-01")
    assert finance.debt_due_date(debt) == dt.date(2026, 7, 31)  # +30 days


def test_terms_are_configurable():
    debt = _debt(100, "2026-07-01")
    assert finance.debt_due_date(debt, terms=60) == dt.date(2026, 8, 30)


# --- Status ---------------------------------------------------------------
@pytest.mark.parametrize("due,expected", [
    ("2026-07-01", "overdue"),    # a month past
    ("2026-08-03", "overdue"),    # yesterday
    ("2026-08-04", "due_soon"),   # today is not yet late
    ("2026-08-09", "due_soon"),   # inside the 7-day window
    ("2026-08-11", "due_soon"),   # exactly 7 days out
    ("2026-08-12", "current"),    # beyond it
])
def test_status_boundaries(due, expected):
    assert finance.debt_status(_debt(100, "2026-07-01", due=due), TODAY) == expected


def test_a_debt_with_no_usable_date_is_outstanding_but_not_overdue():
    """Still counted in the totals; claiming it is late would be an
    invention."""
    debt = _debt(100, None)
    assert finance.debt_status(debt, TODAY) == "unknown"
    assert finance.days_overdue(debt, TODAY) == 0


def test_days_overdue_counts_only_past_due():
    assert finance.days_overdue(_debt(100, "2026-06-01", due="2026-07-05"), TODAY) == 30
    assert finance.days_overdue(_debt(100, "2026-07-01", due="2026-09-01"), TODAY) == 0


# --- Ageing ---------------------------------------------------------------
def test_only_overdue_money_is_bucketed():
    """A debt not yet due has no age; folding it into 1-30 would overstate the
    oldest bucket the first time anyone looked."""
    debts = [
        _debt(100, "2026-07-01", due="2026-07-25"),   # 10 days late
        _debt(200, "2026-05-01", due="2026-06-20"),   # 45 days late
        _debt(400, "2026-01-01", due="2026-02-01"),   # 184 days late
        _debt(800, "2026-08-01", due="2026-09-01"),   # not due at all
    ]
    assert finance.aging_buckets(debts, TODAY) == [
        {"label": "1-30", "amount": 100.0},
        {"label": "31-60", "amount": 200.0},
        {"label": "61-90", "amount": 0.0},
        {"label": "90+", "amount": 400.0},
    ]


# --- The alert payload ----------------------------------------------------
def test_clients_are_grouped_and_ranked_worst_first():
    projects = [_project("Αργοπορημένος", "1"), _project("Συνεπής", "2")]
    debts = [
        _debt(300, "2026-01-01", due="2026-02-01", name="Αργοπορημένος", txn_id="10"),
        _debt(100, "2026-07-20", due="2026-07-30", name="Αργοπορημένος", txn_id="11"),
        _debt(900, "2026-08-01", due="2026-12-01", name="Συνεπής", txn_id="12"),
    ]
    alerts = finance.build_debt_alerts(projects, debts, today=TODAY)

    assert alerts["total"] == 1300.0
    assert alerts["overdue_total"] == 400.0
    assert alerts["overdue_count"] == 2
    assert alerts["clients_affected"] == 2
    assert alerts["clients_overdue"] == 1

    worst, ok = alerts["clients"]
    assert worst["name"] == "Αργοπορημένος"
    assert worst["id"] == "1"           # the drawer needs this
    assert worst["status"] == "overdue"
    assert worst["total"] == 400.0
    assert worst["max_days_overdue"] == 184
    # Most pressing debt first within the client.
    assert [d["id"] for d in worst["debts"]] == ["10", "11"]

    assert ok["status"] == "current"
    assert ok["overdue"] == 0.0


def test_a_clients_status_is_the_worst_of_its_debts():
    debts = [
        _debt(100, "2026-08-01", due="2026-12-01"),   # current
        _debt(100, "2026-01-01", due="2026-02-01"),   # overdue
    ]
    alerts = finance.build_debt_alerts([], debts, today=TODAY)
    assert alerts["clients"][0]["status"] == "overdue"


def test_an_archived_clients_debt_still_alerts():
    """A closed client can still owe, and that is the debt most likely to be
    forgotten."""
    alerts = finance.build_debt_alerts(
        [_project("Κλειστός", "7")],
        [_debt(500, "2026-01-01", due="2026-02-01", name="Κλειστός")],
        today=TODAY)
    assert alerts["clients"][0]["id"] == "7"
    assert alerts["overdue_total"] == 500.0


def test_a_debt_for_an_unknown_client_is_still_reported():
    """No id means no drawer link, but the money is still owed and must not
    silently vanish from the total."""
    alerts = finance.build_debt_alerts(
        [], [_debt(250, "2026-01-01", name="Άγνωστος")], today=TODAY)
    assert alerts["total"] == 250.0
    assert alerts["clients"][0]["id"] is None


def test_no_debts_is_an_empty_but_well_formed_payload():
    alerts = finance.build_debt_alerts([], [], today=TODAY)
    assert alerts["total"] == 0.0
    assert alerts["clients"] == []
    assert [b["amount"] for b in alerts["aging"]] == [0.0, 0.0, 0.0, 0.0]


# --- Through the API ------------------------------------------------------
def _iso(days_ago):
    return (dt.date.today() - dt.timedelta(days=days_ago)).isoformat()


def test_dashboard_reports_overdue_debts(api):
    api.post("/api/transactions", json={
        "client": "Αργοπορημένος", "amount": 500, "type": "Χρεωστούμενο",
        "vat_rate": 0.24, "date": _iso(200), "due_date": _iso(120)})
    api.post("/api/transactions", json={
        "client": "Συνεπής", "amount": 300, "type": "Χρεωστούμενο",
        "vat_rate": 0.24, "date": _iso(2), "due_date": _iso(-60)})

    alerts = api.get("/api/dashboard").json()["debt_alerts"]
    assert alerts["total"] == 800.0
    assert alerts["overdue_total"] == 500.0
    assert alerts["clients_overdue"] == 1
    assert alerts["clients"][0]["name"] == "Αργοπορημένος"
    assert alerts["clients"][0]["max_days_overdue"] == 120
    assert alerts["aging"][-1] == {"label": "90+", "amount": 500.0}


def test_alerts_ignore_the_selected_period(api):
    """An overdue debt is a fact about today. Suppressing it because the user
    is looking at a past quarter would hide the thing the alert exists for."""
    api.post("/api/transactions", json={
        "client": "Αργοπορημένος", "amount": 500, "type": "Χρεωστούμενο",
        "vat_rate": 0.24, "date": _iso(120), "due_date": _iso(90)})

    scoped = api.get("/api/dashboard?year=2019").json()
    # The period-scoped KPI correctly shows nothing in 2019 ...
    assert scoped["header"]["total_debt"] == 0.0
    # ... while the alert still fires.
    assert scoped["debt_alerts"]["overdue_total"] == 500.0


def test_settling_a_debt_clears_it_from_the_alerts(api):
    res = api.post("/api/transactions", json={
        "client": "Πελάτης", "amount": 500, "type": "Χρεωστούμενο",
        "vat_rate": 0.24, "date": _iso(120), "due_date": _iso(90)})
    debt_id = res.json()["id"]

    api.post(f"/api/v1/transactions/{debt_id}/settle", json={"amount": 200})
    alerts = api.get("/api/dashboard").json()["debt_alerts"]
    assert alerts["total"] == 300.0
    assert alerts["overdue_total"] == 300.0

    api.post(f"/api/v1/transactions/{debt_id}/settle", json={})
    alerts = api.get("/api/dashboard").json()["debt_alerts"]
    assert alerts["total"] == 0.0
    assert alerts["clients"] == []


def test_a_debt_row_reports_its_own_status(api):
    api.post("/api/transactions", json={
        "client": "Πελάτης", "amount": 500, "type": "Χρεωστούμενο",
        "vat_rate": 0.24, "date": _iso(120), "due_date": _iso(90)})
    row = api.get("/api/transactions").json()["transactions"][0]
    assert row["status"] == "overdue"
    assert row["days_overdue"] == 90
    assert row["due_date"] == _iso(90)


def test_a_debt_without_a_due_date_still_ages_through_the_api(api):
    api.post("/api/transactions", json={
        "client": "Πελάτης", "amount": 500, "type": "Χρεωστούμενο",
        "vat_rate": 0.24, "date": _iso(100)})
    row = api.get("/api/transactions").json()["transactions"][0]
    assert row["status"] == "overdue"
    assert row["days_overdue"] == 70          # 100 days old, 30-day terms
