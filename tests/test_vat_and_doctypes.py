"""Greek VAT rates, net/gross entry, and document types."""

import pytest

import finance


# --- The rate table -------------------------------------------------------
def test_the_four_greek_rates_are_offered(api):
    meta = api.get("/api/meta").json()
    assert [r["value"] for r in meta["vat_rates"]] == [0.24, 0.13, 0.06, 0.0]
    labels = {r["value"]: r["label"] for r in meta["vat_rates"]}
    assert labels[0.24] == "24 %"
    assert labels[0.0] == "0 % (Απαλλασσόμενο)"


def test_a_zero_rated_row_carries_no_vat(api):
    """Απαλλασσόμενο is a real rate, not a missing one: the row must store 0
    rather than fall through to the default-rate derivation."""
    api.post("/api/transactions", json={
        "client": "Απαλλασσόμενος", "amount": 100, "type": "Έσοδο",
        "vat_rate": 0.0})
    row = api.get("/api/transactions").json()["transactions"][0]
    assert row["vat_amount"] == 0.0
    assert row["net_amount"] == 100.0


# --- Net / gross conversion ----------------------------------------------
@pytest.mark.parametrize("rate,net,gross,vat", [
    (0.24, 100.0, 124.0, 24.0),
    (0.13, 100.0, 113.0, 13.0),
    (0.06, 100.0, 106.0, 6.0),
    (0.0, 100.0, 100.0, 0.0),
    (0.24, 1650.0, 2046.0, 396.0),
])
def test_conversion_round_trips_at_every_rate(rate, net, gross, vat):
    assert finance.gross_from_net(net, rate) == gross
    assert finance.net_from_gross(gross, rate) == net
    assert finance.vat_of(gross, rate) == vat
    # The three always reconcile — that is the point of deriving net by
    # subtraction rather than by dividing the gross.
    assert round(net + vat, 2) == gross


def test_net_and_gross_entry_store_the_same_row(api):
    """The form lets either side be typed; both must land identically."""
    api.post("/api/transactions", json={
        "client": "Α", "amount": 124, "amount_basis": "gross",
        "type": "Έσοδο", "vat_rate": 0.24})
    api.post("/api/transactions", json={
        "client": "Β", "amount": 100, "amount_basis": "net",
        "type": "Έσοδο", "vat_rate": 0.24})

    rows = api.get("/api/transactions").json()["transactions"]
    assert {(r["amount"], r["vat_amount"], r["net_amount"]) for r in rows} == {
        (124.0, 24.0, 100.0)
    }


def test_net_entry_on_an_expense_keeps_the_sign(api):
    api.post("/api/transactions", json={
        "client": "Προμηθευτής", "amount": 100, "amount_basis": "net",
        "type": "Έξοδο", "vat_rate": 0.24})
    row = api.get("/api/transactions").json()["transactions"][0]
    # Expenses are stored negative; VAT is a positive bucket magnitude.
    assert row["amount"] == -124.0
    assert row["vat_amount"] == 24.0
    assert row["net_amount"] == -100.0


def test_gross_is_the_default_basis(api):
    """Callers predating amount_basis send a gross figure and must keep
    working unchanged."""
    api.post("/api/transactions", json={
        "client": "Α", "amount": 124, "type": "Έσοδο", "vat_rate": 0.24})
    assert api.get("/api/transactions").json()["transactions"][0]["amount"] == 124.0


def test_a_scanned_vat_figure_still_wins_over_the_derived_one(api):
    """An invoice with several lines at different rates prints a VAT that the
    single-rate derivation cannot reproduce; the document is authoritative."""
    api.post("/api/transactions", json={
        "client": "Προμηθευτής", "amount": 124, "type": "Έξοδο",
        "vat_rate": 0.24, "vat_amount": 23.5})
    row = api.get("/api/transactions").json()["transactions"][0]
    assert row["vat_amount"] == 23.5
    assert row["net_amount"] == -100.5


# --- Document types -------------------------------------------------------
def test_the_five_document_types_are_offered(api):
    meta = api.get("/api/meta").json()
    assert [d["value"] for d in meta["doc_types"]] == [
        "Τιμολόγιο Πώλησης", "ΑΠΥ", "Πιστωτικό",
        "Δαπάνη/Έξοδο", "Λειτουργικό Έξοδο",
    ]
    suggests = {d["value"]: d["suggests"] for d in meta["doc_types"]}
    assert suggests["ΑΠΥ"] == "Έσοδο"
    assert suggests["Λειτουργικό Έξοδο"] == "Έξοδο"


def test_document_type_survives_the_round_trip(api):
    api.post("/api/transactions", json={
        "client": "Πελάτης", "amount": 124, "type": "Έσοδο",
        "vat_rate": 0.24, "doc_type": "ΑΠΥ"})
    assert api.get("/api/transactions").json()["transactions"][0]["doc_type"] == "ΑΠΥ"


def test_an_unknown_document_type_is_refused(api):
    res = api.post("/api/transactions", json={
        "client": "Πελάτης", "amount": 124, "type": "Έσοδο",
        "vat_rate": 0.24, "doc_type": "Χειρόγραφο"})
    assert res.status_code == 422


def test_document_type_is_optional(api):
    """Quick cash entries have no document, and rows predating the column have
    none either."""
    assert api.post("/api/transactions", json={
        "client": "Πελάτης", "amount": 124, "type": "Έσοδο",
        "vat_rate": 0.24}).status_code == 201
    assert api.get("/api/transactions").json()["transactions"][0]["doc_type"] is None


# --- Credit notes ---------------------------------------------------------
def test_a_credit_note_reduces_revenue_rather_than_adding_expense(api):
    api.post("/api/transactions", json={
        "client": "Πελάτης", "amount": 1000, "type": "Έσοδο",
        "vat_rate": 0.24, "doc_type": "Τιμολόγιο Πώλησης"})
    api.post("/api/transactions", json={
        "client": "Πελάτης", "amount": 124, "type": "Έσοδο",
        "vat_rate": 0.24, "doc_type": "Πιστωτικό"})

    header = api.get("/api/dashboard").json()["header"]
    # Revenue falls by the credited gross; expenses are untouched.
    assert header["total_gross_rev"] == 876.0
    assert header["total_gross_exp"] == 0.0
    # 1000 gross at 24% is 193.55; the credit reverses 24.00 of output VAT.
    assert header["total_vat"] == round(193.55 - 24.0, 2)


def test_a_credit_note_against_a_purchase_reduces_expenses(api):
    api.post("/api/transactions", json={
        "client": "Προμηθευτής", "amount": 1000, "type": "Έξοδο",
        "vat_rate": 0.24, "doc_type": "Δαπάνη/Έξοδο"})
    api.post("/api/transactions", json={
        "client": "Προμηθευτής", "amount": 124, "type": "Έξοδο",
        "vat_rate": 0.24, "doc_type": "Πιστωτικό"})

    header = api.get("/api/dashboard").json()["header"]
    assert header["total_gross_exp"] == 876.0
    assert header["total_gross_rev"] == 0.0


def test_a_credit_note_cannot_be_a_debt(api):
    res = api.post("/api/transactions", json={
        "client": "Πελάτης", "amount": 124, "type": "Χρεωστούμενο",
        "vat_rate": 0.24, "doc_type": "Πιστωτικό"})
    assert res.status_code == 422


def test_vat_totals_still_reconcile_with_credit_notes(api):
    """The summation guarantee: the header total is exactly the sum of the
    per-client figures, negative rows included."""
    for client in ("Α", "Β"):
        api.post("/api/transactions", json={
            "client": client, "amount": 620, "type": "Έσοδο",
            "vat_rate": 0.24, "doc_type": "Τιμολόγιο Πώλησης"})
        api.post("/api/transactions", json={
            "client": client, "amount": 124, "type": "Έσοδο",
            "vat_rate": 0.24, "doc_type": "Πιστωτικό"})

    payload = api.get("/api/dashboard").json()
    per_client = round(sum(c["metrics"]["net_vat"] for c in payload["clients"]), 2)
    assert payload["header"]["total_vat"] == per_client
    assert payload["header"]["total_gross_rev"] == round(
        sum(c["metrics"]["gross_rev"] for c in payload["clients"]), 2)


def test_credit_note_net_amount_keeps_its_sign(api):
    api.post("/api/transactions", json={
        "client": "Πελάτης", "amount": 124, "type": "Έσοδο",
        "vat_rate": 0.24, "doc_type": "Πιστωτικό"})
    row = api.get("/api/transactions").json()["transactions"][0]
    assert row["amount"] == -124.0
    assert row["vat_amount"] == -24.0
    assert row["net_amount"] == -100.0
