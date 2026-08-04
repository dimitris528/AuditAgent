"""Duplicate detection — clients (ΑΦΜ + Greek name) and invoices."""

import pytest

from server.text import afm_key, doc_key, name_key


# --- The normalisation the guards are built on ----------------------------
@pytest.mark.parametrize("written", [
    "Νησίδα Café",
    "νησιδα cafe",
    "ΝΗΣΙΔΑ CAFE",
    "  Νησίδα   Café  ",
    "Νησίδα, Café",
])
def test_greek_name_variants_share_one_key(written):
    """The three spellings from the brief, plus the whitespace and punctuation
    a real typist adds, all have to collapse to the same key."""
    assert name_key(written) == name_key("Νησίδα Café")


def test_final_sigma_folds():
    """lower() gives "οδοσ" for ΟΔΟΣ but "οδος" for οδός — a mismatch on every
    Greek word ending in sigma. casefold maps both onto the medial σ."""
    assert name_key("ΟΔΟΣ") == name_key("οδός") == "οδοσ"


def test_distinct_names_do_not_collide():
    assert name_key("Νησίδα Café") != name_key("Νησίδα Bar")
    assert name_key("Παπαδόπουλος") != name_key("Παπαδοπούλου")


def test_afm_key_ignores_formatting_and_greek_prefix():
    assert afm_key("EL123456789") == afm_key("123 456 789") == "123456789"
    assert afm_key("el-123456789") == "123456789"
    # A foreign VAT number's country code is part of its identity.
    assert afm_key("DE123456789") == "DE123456789"
    assert afm_key(None) == ""


def test_doc_key_ignores_series_formatting():
    assert doc_key("ΤΠΥ-1042") == doc_key("τπυ 1042") == doc_key("ΤΠΥ1042")
    assert doc_key("ΤΠΥ-1042") != doc_key("ΤΠΥ-1043")


# --- Clients --------------------------------------------------------------
def test_duplicate_client_name_is_refused_across_greek_spellings(api):
    assert api.post("/api/v1/clients", json={"name": "Νησίδα Café"}).status_code == 201

    for attempt in ("νησιδα cafe", "ΝΗΣΙΔΑ CAFE", "Νησίδα Café"):
        res = api.post("/api/v1/clients", json={"name": attempt})
        assert res.status_code == 409, f"{attempt} was accepted as a new client"
        assert "Νησίδα Café" in res.json()["detail"]


def test_duplicate_afm_is_refused_even_under_a_different_name(api):
    api.post("/api/v1/clients",
             json={"name": "Νησίδα Café", "afm": "123456789"})
    res = api.post("/api/v1/clients",
                   json={"name": "Εντελώς Άλλη Επωνυμία", "afm": "EL123456789"})
    assert res.status_code == 409
    assert "Α.Φ.Μ." in res.json()["detail"]


def test_different_clients_are_allowed(api):
    assert api.post("/api/v1/clients",
                    json={"name": "Νησίδα Café", "afm": "123456789"}).status_code == 201
    assert api.post("/api/v1/clients",
                    json={"name": "Νησίδα Bar", "afm": "987654321"}).status_code == 201


def test_check_duplicate_reports_without_writing(api):
    api.post("/api/v1/clients", json={"name": "Νησίδα Café", "afm": "123456789"})

    hit = api.post("/api/v1/clients/check-duplicate",
                   json={"name": "ΝΗΣΙΔΑ CAFE"}).json()["duplicate"]
    assert hit["matched_by"] == "name" and hit["name"] == "Νησίδα Café"

    by_afm = api.post("/api/v1/clients/check-duplicate",
                      json={"afm": "EL 123 456 789"}).json()["duplicate"]
    assert by_afm["matched_by"] == "afm"

    assert api.post("/api/v1/clients/check-duplicate",
                    json={"name": "Καινούριος"}).json()["duplicate"] is None
    # Nothing was created by any of the above.
    assert len(api.get("/api/v1/clients").json()["clients"]) == 1


def test_editing_a_client_does_not_collide_with_itself(api):
    client_id = api.post("/api/v1/clients",
                         json={"name": "Νησίδα Café", "afm": "123456789"}).json()["id"]
    # Same name and ΑΦΜ, only the notes change.
    res = api.put(f"/api/v1/clients/{client_id}",
                  json={"name": "Νησίδα Café", "afm": "123456789",
                        "notes": "Πελάτης από 2024"})
    assert res.status_code == 200


def test_renaming_onto_another_client_is_refused(api):
    api.post("/api/v1/clients", json={"name": "Νησίδα Café"})
    other = api.post("/api/v1/clients", json={"name": "Νησίδα Bar"}).json()["id"]
    res = api.put(f"/api/v1/clients/{other}", json={"name": "ΝΗΣΙΔΑ CAFE"})
    assert res.status_code == 409


def test_transaction_reuses_an_existing_client_across_spellings(api):
    """The picker can be bypassed by typing a name; that path must not open a
    second card for a company that already has one."""
    api.post("/api/v1/clients", json={"name": "Νησίδα Café"})
    res = api.post("/api/transactions", json={
        "client": "ΝΗΣΙΔΑ CAFE", "amount": 124, "type": "Έσοδο", "vat_rate": 0.24,
    })
    assert res.status_code == 201
    clients = api.get("/api/v1/clients").json()["clients"]
    assert [c["name"] for c in clients] == ["Νησίδα Café"]


# --- Invoices -------------------------------------------------------------
INVOICE = {
    "client": "Προμηθευτής ΑΕ",
    "amount": 124.0,
    "type": "Έξοδο",
    "vat_rate": 0.24,
    "date": "2026-07-14",
    "doc_number": "ΤΠΥ-1042",
    "counterparty_afm": "123456789",
}


def test_same_invoice_twice_is_blocked_then_allowed_with_force(api):
    assert api.post("/api/transactions", json=INVOICE).status_code == 201

    res = api.post("/api/transactions", json={**INVOICE, "doc_number": "τπυ 1042"})
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["duplicate"]["doc_number"] == "ΤΠΥ-1042"
    assert detail["duplicate"]["date"] == "2026-07-14"

    # The user has now seen what it collided with and chose to keep it.
    assert api.post("/api/transactions",
                    json={**INVOICE, "force": True}).status_code == 201


@pytest.mark.parametrize("change", [
    {"doc_number": "ΤΠΥ-1043"},                       # different document
    {"date": "2026-07-15"},                           # different date
    {"counterparty_afm": "987654321", "client": "Άλλος ΑΕ"},   # different issuer
])
def test_a_genuinely_different_invoice_is_accepted(api, change):
    assert api.post("/api/transactions", json=INVOICE).status_code == 201
    assert api.post("/api/transactions",
                    json={**INVOICE, **change}).status_code == 201


def test_rows_without_a_document_number_are_never_duplicates(api):
    """Two same-day cash receipts from one supplier for the same amount are
    legitimate; with nothing to key on the guard must stay out of the way."""
    plain = {k: v for k, v in INVOICE.items()
             if k not in ("doc_number", "counterparty_afm")}
    assert api.post("/api/transactions", json=plain).status_code == 201
    assert api.post("/api/transactions", json=plain).status_code == 201


def test_duplicate_is_caught_by_client_name_when_no_afm_was_typed(api):
    no_afm = {k: v for k, v in INVOICE.items() if k != "counterparty_afm"}
    assert api.post("/api/transactions", json=no_afm).status_code == 201
    assert api.post("/api/transactions", json=no_afm).status_code == 409


def test_transaction_check_duplicate_endpoint(api):
    api.post("/api/transactions", json=INVOICE)

    hit = api.post("/api/v1/transactions/check-duplicate", json={
        "doc_number": "ΤΠΥ-1042", "counterparty_afm": "EL123456789",
        "date": "2026-07-14",
    }).json()["duplicate"]
    assert hit["amount"] == 124.0

    miss = api.post("/api/v1/transactions/check-duplicate", json={
        "doc_number": "ΤΠΥ-1042", "counterparty_afm": "123456789",
        "date": "2026-08-01",
    }).json()["duplicate"]
    assert miss is None


def test_document_fields_survive_the_round_trip(api):
    api.post("/api/transactions", json=INVOICE)
    rows = api.get("/api/transactions").json()["transactions"]
    assert rows[0]["doc_number"] == "ΤΠΥ-1042"
    assert rows[0]["counterparty_afm"] == "123456789"
