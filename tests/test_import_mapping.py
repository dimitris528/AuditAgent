"""
The interactive column-mapping step.

Auto-detection has a limit, and past it the importer used to refuse the file —
which left the user editing their export to match our vocabulary. The two-step
flow moves the guessing from a gate to a starting point: /api/import/analyze
reports what the server WOULD have chosen, and /api/import/process takes back
whatever the user corrected.

The assertions that matter are about authority. A mapping the user confirmed
must win over every heuristic in the module, including the ones that would
have been right — quietly re-adding a guess for a field they cleared is the
one behaviour that makes the screen pointless.
"""

import datetime as dt
import io
import json

import pytest

import finance
from server import imports


def csv_bytes(rows, delimiter=";", encoding="utf-8-sig"):
    return "\r\n".join(delimiter.join(str(c) for c in row)
                       for row in rows).encode(encoding)


def xlsx_bytes(rows):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


# A file no alias table can read: every heading is meaningless.
OPAQUE = (
    ("F1", "F2", "F3", "F4", "F5", "F6"),
    ("15/01/2026", "Νησίδα Café", "094127562", "Εξοφλημένο", "1.000,00 €", "240,00 €"),
    ("01/02/2026", "Οδός Τεχνική", "800123456", "Εκκρεμεί", "500,00 €", "120,00 €"),
)


def upload(api, path, data, form=None, filename="data.csv"):
    return api.post(path, files={"file": (filename, data, "text/csv")},
                    data=form or {})


# --- analyze --------------------------------------------------------------
def test_analyze_reports_the_shape_of_the_file():
    result = imports.analyze(csv_bytes(OPAQUE), imports.TRANSACTIONS)
    assert result["headers"] == ["F1", "F2", "F3", "F4", "F5", "F6"]
    assert result["rows"] == 2
    assert result["sample"][0][1] == "Νησίδα Café"
    assert len(result["sample"]) <= imports.SAMPLE_ROWS


def test_analyze_offers_every_field_the_parser_can_read():
    result = imports.analyze(csv_bytes(OPAQUE), imports.TRANSACTIONS)
    keys = {field["key"] for field in result["fields"]}
    # The brief's list, in the app's own vocabulary.
    for expected in ("date", "client", "afm", "net_amount", "amount",
                     "vat_amount", "status"):
        assert expected in keys


def test_analyze_writes_nothing(api):
    res = upload(api, "/api/import/analyze", csv_bytes(OPAQUE),
                 form={"kind": "transactions"})
    assert res.status_code == 200, res.text
    assert api.get("/api/transactions").json()["transactions"] == []
    assert api.get("/api/v1/clients").json()["clients"] == []


def test_a_blank_heading_still_gets_a_name_in_the_dropdown():
    """Otherwise the user is choosing between several identical empty
    options."""
    result = imports.analyze(csv_bytes([
        ("Ημερομηνία", "", "Σύνολο"),
        ("2026-01-15", "Νησίδα Café", "1240,00"),
    ]), imports.TRANSACTIONS)
    assert result["headers"][1] == "Στήλη 2"


def test_analyze_pre_selects_what_it_recognises():
    result = imports.analyze(csv_bytes([
        ("Ημερομηνία", "Πελάτης", "Σύνολο"),
        ("2026-01-15", "Νησίδα Café", "1240,00"),
    ]), imports.TRANSACTIONS)
    assert result["mapping"] == {"date": 0, "client": 1, "amount": 2}


# --- The mapping is authoritative -----------------------------------------
def test_a_confirmed_mapping_imports_a_file_nothing_could_read():
    """The whole point of the step."""
    mapping = {"date": 0, "client": 1, "afm": 2, "status": 3,
               "net_amount": 4, "vat_amount": 5}
    parsed = imports.parse_transactions(csv_bytes(OPAQUE), mapping=mapping)
    assert parsed.errors == []
    assert [(r["amount"], r["vat_amount"]) for r in parsed.rows] == [
        (1240.0, 240.0), (620.0, 120.0),
    ]


def test_the_users_mapping_overrides_a_correct_detection():
    """Even when detection would have been right. A mapping that gets
    second-guessed is a screen that does nothing."""
    data = csv_bytes([
        ("Ημερομηνία", "Πελάτης", "Σύνολο", "Άλλο"),
        ("2026-01-15", "Νησίδα Café", "1240,00", "999,00"),
    ])
    assert imports.analyze(data, imports.TRANSACTIONS)["mapping"]["amount"] == 2

    parsed = imports.parse_transactions(
        data, mapping={"date": 0, "client": 1, "amount": 3})
    assert parsed.rows[0]["amount"] == 999.00


def test_a_field_left_unmapped_is_not_quietly_re_detected():
    """Clearing a dropdown has to mean something, or the screen is a
    decoration."""
    data = csv_bytes([
        ("Ημερομηνία", "Πελάτης", "Σύνολο", "Φ.Π.Α."),
        ("2026-01-15", "Νησίδα Café", "1240,00", "240,00"),
    ])
    parsed = imports.parse_transactions(
        data, mapping={"date": 0, "client": 1, "amount": 2})
    # VAT was deliberately not mapped, so it is DERIVED rather than read from
    # the column sitting right there.
    assert parsed.rows[0]["vat_amount"] == 240.0      # 1240 at the standard rate
    assert parsed.rows[0]["vat_rate"] == finance.DEFAULT_VAT_RATE


def test_the_positional_fallback_never_fires_under_an_explicit_mapping():
    parsed = imports.parse_transactions(csv_bytes(OPAQUE), mapping={
        "date": 0, "client": 1, "amount": 4})
    assert parsed.rows[0]["amount"] == 1000.0
    # The type warning is legitimate — OPAQUE maps no type column. The one
    # that must be absent is the fallback's, which would mean the module went
    # looking for a money column despite being told where one was.
    assert not any("Δεν εντοπίστηκαν στήλες ποσών" in w["message"]
                   for w in parsed.warnings)


@pytest.mark.parametrize("mapping", [
    {"date": 0, "client": 1, "amount": 4, "άγνωστο": 2},     # unknown field
    {"date": 0, "client": 1, "amount": 4, "vat_amount": None},
    {"date": 0, "client": 1, "amount": 4, "doc_number": 99},  # index past the end
])
def test_an_unusable_entry_is_dropped_rather_than_fatal(mapping):
    """The mapping was filled in against a file the user may since have
    changed. Losing one dropdown beats refusing the upload."""
    parsed = imports.parse_transactions(csv_bytes(OPAQUE), mapping=mapping)
    assert parsed.errors == []
    assert len(parsed.rows) == 2


@pytest.mark.parametrize("mapping,named", [
    ({"client": 1, "amount": 4}, "Ημερομηνία"),
    ({"date": 0, "client": 1}, "Συνολικό Ποσό"),
    # Dropped for being out of range, which leaves the file with no amount at
    # all — said once, at file level, not four hundred times per row.
    ({"date": 0, "client": 1, "amount": 99}, "Συνολικό Ποσό"),
])
def test_a_mapping_missing_a_required_field_is_refused_by_name(mapping, named):
    with pytest.raises(imports.ImportFileError) as excinfo:
        imports.parse_transactions(csv_bytes(OPAQUE), mapping=mapping)
    assert named in str(excinfo.value)


# --- Κατάσταση ------------------------------------------------------------
def test_an_outstanding_status_makes_the_row_a_debt():
    """A legacy export routinely carries a payment state and no type column.
    "Εκκρεμεί" IS a Χρεωστούμενο — not a guess, so no warning."""
    parsed = imports.parse_transactions(csv_bytes(OPAQUE), mapping={
        "date": 0, "client": 1, "status": 3, "amount": 4})
    assert [r["type"] for r in parsed.rows] == ["Έσοδο", finance.DEBT_TYPE]


def test_a_settled_status_does_not_decide_direction():
    """"Εξοφλημένο" says the money moved, not which way. It has to defer to
    the amount's sign rather than invent a direction."""
    parsed = imports.parse_transactions(csv_bytes([
        ("Ημ", "Πελ", "Κατ", "Ποσό"),
        ("15/01/2026", "Α", "Εξοφλημένο", "-310,00"),
        ("16/01/2026", "Β", "Εξοφλημένο", "1240,00"),
    ]), mapping={"date": 0, "client": 1, "status": 2, "amount": 3})
    assert [r["type"] for r in parsed.rows] == ["Έξοδο", "Έσοδο"]


def test_an_explicit_type_outranks_the_status_column():
    parsed = imports.parse_transactions(csv_bytes([
        ("Ημ", "Πελ", "Είδος", "Κατ", "Ποσό"),
        ("15/01/2026", "Α", "Έξοδο", "Εκκρεμεί", "310,00"),
    ]), mapping={"date": 0, "client": 1, "type": 2, "status": 3, "amount": 4})
    assert parsed.rows[0]["type"] == "Έξοδο"


# --- Currency cleaning ----------------------------------------------------
@pytest.mark.parametrize("written,expected", [
    ("1.234,56 €", 1234.56),
    ("€1.234,56", 1234.56),
    ("EUR 1234.56", 1234.56),
    ("1,234.56 USD", 1234.56),
    ("$1,234.56", 1234.56),
    ("1 234,56", 1234.56),        # NO-BREAK SPACE as thousands separator
    ("1 234,56", 1234.56),        # NARROW NO-BREAK SPACE
    ("1 234,56", 1234.56),             # a plain space
    ("−1.234,56", -1234.56),      # U+2212 MINUS SIGN, not a hyphen
    ("310,00-", -310.0),               # trailing sign, as many packages export
    ("(310,00)", -310.0),
    ("  1.240,00 €  ", 1240.0),
])
def test_currency_and_invisible_characters_are_stripped(written, expected):
    """Every one of these is invisible or near-invisible in a spreadsheet, and
    each on its own is enough to make a figure parse as nothing."""
    assert imports.number(written) == expected


# --- Derivation under a partial mapping -----------------------------------
@pytest.mark.parametrize("mapping,expected", [
    # Only a total: net and VAT computed at the standard rate.
    ({"date": 0, "client": 1, "amount": 2}, (1240.0, 240.0, 0.24)),
    # Total and VAT: the rate follows from the two figures.
    ({"date": 0, "client": 1, "amount": 2, "vat_amount": 3}, (1240.0, 240.0, 0.24)),
])
def test_unmapped_figures_are_calculated_from_the_mapped_total(mapping, expected):
    parsed = imports.parse_transactions(csv_bytes([
        ("Ημ", "Πελ", "Σύνολο", "ΦΠΑ"),
        ("15/01/2026", "Νησίδα Café", "1240,00", "240,00"),
    ]), mapping=mapping)
    row = parsed.rows[0]
    assert (row["amount"], row["vat_amount"], row["vat_rate"]) == expected


def test_a_reduced_rate_is_derived_rather_than_assumed_under_a_mapping():
    parsed = imports.parse_transactions(csv_bytes([
        ("Ημ", "Πελ", "Καθαρό", "Φόρος"),
        ("02/12/2025", "Αφοί Γεωργίου", "500,00", "65,00"),
    ]), mapping={"date": 0, "client": 1, "net_amount": 2, "vat_amount": 3})
    row = parsed.rows[0]
    assert (row["amount"], row["vat_rate"]) == (565.0, 0.13)


# --- The endpoints --------------------------------------------------------
def test_process_imports_with_the_confirmed_mapping(api):
    mapping = {"date": 0, "client": 1, "afm": 2, "status": 3,
               "net_amount": 4, "vat_amount": 5}
    res = upload(api, "/api/import/process", csv_bytes(OPAQUE), form={
        "kind": "transactions", "mapping": json.dumps(mapping)})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["imported"] == 2
    assert body["failed"] == 0
    assert body["total_amount"] == 1860.0

    rows = {r["client"]: r for r in api.get("/api/transactions").json()["transactions"]}
    assert rows["Νησίδα Café"]["amount"] == 1240.0
    assert rows["Οδός Τεχνική"]["is_debt"] is True


def test_process_works_for_clients_too(api):
    mapping = {"name": 1, "afm": 2}
    res = upload(api, "/api/import/process", csv_bytes(OPAQUE), form={
        "kind": "clients", "mapping": json.dumps(mapping)})
    assert res.status_code == 200, res.text
    assert res.json()["imported"] == 2
    assert sorted(c["name"] for c in api.get("/api/v1/clients").json()["clients"]) == [
        "Νησίδα Café", "Οδός Τεχνική"]


def test_the_analyze_payload_round_trips_into_process(api):
    """What the browser actually does: take the mapping the server offered and
    post it straight back."""
    data = csv_bytes([
        ("Ημερομηνία", "Πελάτης", "Σύνολο"),
        ("2026-01-15", "Νησίδα Café", "1.240,00 €"),
    ])
    analysis = upload(api, "/api/import/analyze", data,
                      form={"kind": "transactions"}).json()
    res = upload(api, "/api/import/process", data, form={
        "kind": "transactions", "mapping": json.dumps(analysis["mapping"])})
    assert res.json()["imported"] == 1
    assert api.get("/api/transactions").json()["transactions"][0]["amount"] == 1240.0


def test_an_xlsx_analyzes_and_processes_the_same_way(api):
    data = xlsx_bytes([
        ("Col A", "Col B", "Col C"),
        (dt.date(2026, 1, 15), "Νησίδα Café", 1240.0),
    ])
    analysis = api.post("/api/import/analyze",
                        files={"file": ("k.xlsx", data, "application/vnd.ms-excel")},
                        data={"kind": "transactions"}).json()
    assert analysis["headers"] == ["Col A", "Col B", "Col C"]

    res = api.post("/api/import/process",
                   files={"file": ("k.xlsx", data, "application/vnd.ms-excel")},
                   data={"kind": "transactions",
                         "mapping": json.dumps({"date": 0, "client": 1, "amount": 2})})
    assert res.json()["imported"] == 1


@pytest.mark.parametrize("mapping", ["not json", "[1,2,3]", "{"])
def test_a_malformed_mapping_is_refused_cleanly(api, mapping):
    res = upload(api, "/api/import/process", csv_bytes(OPAQUE),
                 form={"kind": "transactions", "mapping": mapping})
    assert res.status_code == 422
    assert "{" not in res.json()["detail"]


def test_an_unknown_kind_is_a_404(api):
    res = upload(api, "/api/import/analyze", csv_bytes(OPAQUE),
                 form={"kind": "invoices"})
    assert res.status_code == 404


@pytest.mark.parametrize("path", ["/api/import/analyze", "/api/import/process"])
def test_the_mapped_import_requires_a_session(path):
    from fastapi.testclient import TestClient
    from server.main import app

    with TestClient(app) as client:
        res = client.post(path, files={"file": ("x.csv", b"a;b", "text/csv")},
                          data={"kind": "transactions"})
    assert res.status_code == 401
