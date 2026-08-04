"""
CSV export — the columns, the Excel dialect, and what the period filter lets
through.

The dialect assertions look fussy and are the point of the module: a file that
opens as one column of mojibake in a Greek Excel is not an export, and every
one of the three things that prevents (BOM, semicolons, decimal commas) is
invisible until someone actually double-clicks the file.
"""

import csv
import datetime as dt
import io

import pytest

from server import exports


def _rows(body):
    """Parse an exported file back, BOM and delimiter and all."""
    text = body.lstrip("﻿")
    delimiter = ";" if ";" in text.splitlines()[0] else ","
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


def _record(rid=1, client="Νησίδα Café", amount=124.0, type_="Έσοδο",
            vat=24.0, when="2026-07-14", **fields):
    """One finance-shaped record, the way store.Transaction.to_record emits."""
    body = {
        "Username": "tester",
        "Category": client,
        "Amount": amount,
        "Type": type_,
        "VAT_Amount": vat,
        "VAT_Rate": 0.24,
        "Date": when,
        "Description": None,
        "DocNumber": None,
        "DocType": None,
        "CounterpartyAFM": None,
        "DueDate": None,
        "DebtId": None,
        "Source": "Web",
        "FileHash": None,
    }
    body.update(fields)
    return {"id": str(rid), "createdTime": f"{when}T09:00:00Z", "fields": body}


# --- Columns --------------------------------------------------------------
def test_the_header_carries_every_requested_column():
    rows = _rows(exports.transactions_csv([]))
    assert rows[0] == list(exports.COLUMNS)
    for required in ("Ημερομηνία", "Πελάτης", "Α.Φ.Μ.", "Τύπος Παραστατικού",
                     "Καθαρή Αξία", "Φ.Π.Α.", "Συνολικό Ποσό",
                     "Κατάσταση Πληρωμής"):
        assert required in rows[0]


def test_a_row_carries_the_figures_and_the_clients_afm():
    body = exports.transactions_csv(
        [_record(doc_type="Τιμολόγιο Πώλησης", DocNumber="ΤΠΥ-1042",
                 DocType="Τιμολόγιο Πώλησης", Description="Ιούλιος")],
        afm_by_client={"νησίδα café": "123456789"},
        dialect=exports.ISO)
    row = dict(zip(exports.COLUMNS, _rows(body)[1]))
    assert row["Ημερομηνία"] == "2026-07-14"
    assert row["Πελάτης"] == "Νησίδα Café"
    assert row["Α.Φ.Μ."] == "123456789"
    assert row["Τύπος Παραστατικού"] == "Τιμολόγιο Πώλησης"
    assert row["Αρ. Παραστατικού"] == "ΤΠΥ-1042"
    assert row["Είδος Κίνησης"] == "Έσοδο"
    assert row["Καθαρή Αξία"] == "100.00"
    assert row["Φ.Π.Α."] == "24.00"
    assert row["Συνολικό Ποσό"] == "124.00"
    assert row["Περιγραφή"] == "Ιούλιος"


def test_an_expense_exports_as_a_positive_magnitude():
    """A spreadsheet has no colour, and a column of "-310,00" reads as a
    correction rather than a cost. Direction lives in Είδος Κίνησης."""
    body = exports.transactions_csv(
        [_record(amount=-124.0, type_="Έξοδο", vat=24.0)],
        dialect=exports.ISO)
    row = dict(zip(exports.COLUMNS, _rows(body)[1]))
    assert row["Συνολικό Ποσό"] == "124.00"
    assert row["Καθαρή Αξία"] == "100.00"
    assert row["Φ.Π.Α."] == "24.00"
    assert row["Είδος Κίνησης"] == "Έξοδο"
    # No minus sign survives on any AMOUNT column. (Not the whole row — the ISO
    # date legitimately contains hyphens.)
    for column in ("Καθαρή Αξία", "Φ.Π.Α.", "Συνολικό Ποσό"):
        assert "-" not in row[column], column


def test_doc_type_number_and_description_are_populated_when_present():
    """Reported as "missing". They are carried; what was missing was data —
    and an absent field must come out EMPTY, never as the string "None"."""
    body = exports.transactions_csv(
        [_record(DocType="Τιμολόγιο Πώλησης", DocNumber="ΤΠΥ-1042",
                 Description="Υπηρεσίες Ιουλίου")],
        dialect=exports.ISO)
    row = dict(zip(exports.COLUMNS, _rows(body)[1]))
    assert row["Τύπος Παραστατικού"] == "Τιμολόγιο Πώλησης"
    assert row["Αρ. Παραστατικού"] == "ΤΠΥ-1042"
    assert row["Περιγραφή"] == "Υπηρεσίες Ιουλίου"


def test_absent_text_fields_are_blank_not_the_word_none():
    """A stray "None" sorts, filters and looks like data."""
    body = exports.transactions_csv([_record()], dialect=exports.ISO)
    cells = _rows(body)[1]
    assert "None" not in cells
    row = dict(zip(exports.COLUMNS, cells))
    assert row["Τύπος Παραστατικού"] == ""
    assert row["Αρ. Παραστατικού"] == ""
    assert row["Περιγραφή"] == ""


def test_a_row_with_no_vat_exports_blanks_not_zeros():
    """A Χρεωστούμενο carries no VAT until it is settled. Exporting 0,00 would
    let someone reconcile a column that was never filled in."""
    body = exports.transactions_csv(
        [_record(type_="Χρεωστούμενο", vat=None, amount=500.0)],
        dialect=exports.ISO)
    row = dict(zip(exports.COLUMNS, _rows(body)[1]))
    assert row["Φ.Π.Α."] == ""
    # With nothing to subtract, the net value IS the gross.
    assert row["Καθαρή Αξία"] == "500.00"


def test_an_unknown_client_afm_is_blank_rather_than_missing():
    body = exports.transactions_csv([_record()], afm_by_client={},
                                    dialect=exports.ISO)
    assert dict(zip(exports.COLUMNS, _rows(body)[1]))["Α.Φ.Μ."] == ""


def test_rows_come_out_oldest_first():
    """A statement is read forwards, unlike the dashboard's newest-first list."""
    body = exports.transactions_csv(
        [_record(rid=1, when="2026-07-14"), _record(rid=2, when="2026-01-03"),
         _record(rid=3, when="2026-03-30")],
        dialect=exports.ISO)
    assert [r[0] for r in _rows(body)[1:]] == [
        "2026-01-03", "2026-03-30", "2026-07-14"]


# --- Κατάσταση Πληρωμής ---------------------------------------------------
def test_a_settled_movement_reads_as_paid():
    """An Έσοδο was received and an Έξοδο was paid; only a debt has a payment
    state to report."""
    assert exports.payment_status(_record(type_="Έσοδο")) == exports.PAID
    assert exports.payment_status(_record(type_="Έξοδο")) == exports.PAID


def test_an_untouched_debt_reads_as_unpaid():
    debt = _record(type_="Χρεωστούμενο", vat=None, DueDate="2099-01-01")
    assert exports.payment_status(debt) == exports.UNPAID


def test_a_part_paid_debt_reads_as_partial():
    debt = _record(type_="Χρεωστούμενο", vat=None, DueDate="2099-01-01")
    assert exports.payment_status(debt, paid_so_far=200.0) == exports.PARTIAL


def test_overdue_outranks_partially_paid():
    """A debt that is both is a collection problem first, and a sheet filtered
    to Ληξιπρόθεσμο has to show it."""
    debt = _record(type_="Χρεωστούμενο", vat=None, DueDate="2020-01-01")
    assert exports.payment_status(debt, paid_so_far=200.0) == exports.OVERDUE


def test_the_paid_column_is_driven_by_the_settlement_log():
    debt = _record(rid=7, type_="Χρεωστούμενο", vat=None, DueDate="2099-01-01")
    body = exports.transactions_csv([debt], paid_by_debt={7: 200.0},
                                    dialect=exports.ISO)
    row = dict(zip(exports.COLUMNS, _rows(body)[1]))
    assert row["Κατάσταση Πληρωμής"] == exports.PARTIAL


# --- The Excel dialect ----------------------------------------------------
def test_excel_dialect_writes_a_bom():
    """Without it Excel decodes the file as the system codepage and every
    Greek name arrives as mojibake."""
    body = exports.transactions_csv([_record()])
    assert body.startswith("﻿")
    assert body.encode("utf-8").startswith(b"\xef\xbb\xbf")


def test_excel_dialect_uses_semicolons_and_decimal_commas():
    """They go together: in an el-GR locale the comma is the decimal separator,
    so a comma-delimited file lands entirely in column A."""
    body = exports.transactions_csv([_record()])
    line = body.lstrip("﻿").splitlines()[1]
    assert ";" in line
    assert "124,00" in line
    assert "124.00" not in line


def test_iso_dialect_is_plain_rfc4180():
    """The opposite choice, for anything that is not Excel."""
    body = exports.transactions_csv([_record()], dialect=exports.ISO)
    assert not body.startswith("﻿")
    line = body.splitlines()[1]
    assert "124.00" in line
    assert ";" not in line


def test_a_description_containing_the_delimiter_is_quoted():
    """A Greek description routinely contains a semicolon (it is the Greek
    question mark); unquoted, it would shift every column after it."""
    body = exports.transactions_csv(
        [_record(Description="Τι έγινε; Ιούλιος")])
    row = dict(zip(exports.COLUMNS, _rows(body)[1]))
    assert row["Περιγραφή"] == "Τι έγινε; Ιούλιος"
    assert row["Κατάσταση Πληρωμής"] == exports.PAID


def test_an_unknown_dialect_falls_back_to_excel():
    assert exports.transactions_csv([], dialect="klingon").startswith("﻿")


# --- Filenames ------------------------------------------------------------
@pytest.mark.parametrize("kwargs,expected", [
    ({}, "transactions-all.csv"),
    ({"year": 2026}, "transactions-2026.csv"),
    ({"year": 2026, "quarter": 3}, "transactions-2026-Q3.csv"),
    ({"year": 2026, "month": 7}, "transactions-2026-M07.csv"),
    ({"client": 12, "year": 2026}, "transactions-client-12-2026.csv"),
])
def test_the_filename_describes_the_period(kwargs, expected):
    assert exports.filename(**kwargs) == expected


def test_the_filename_is_ascii_only():
    """A Greek filename needs RFC 5987 encoding that some browsers and Excel
    installs still mangle into something unopenable."""
    exports.filename(year=2026, quarter=3).encode("ascii")


# --- Endpoint -------------------------------------------------------------
def test_export_endpoint_returns_a_csv_attachment(api):
    api.post("/api/v1/clients", json={"name": "Νησίδα Café", "afm": "123456789"})
    api.post("/api/transactions", json={
        "client": "Νησίδα Café", "amount": 124, "type": "Έσοδο",
        "date": "2026-07-14", "doc_number": "ΤΠΥ-1042",
        "doc_type": "Τιμολόγιο Πώλησης",
    })

    res = api.get("/api/v1/exports/transactions.csv")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]
    assert ".csv" in res.headers["content-disposition"]

    body = res.content.decode("utf-8")
    rows = _rows(body)
    assert rows[0] == list(exports.COLUMNS)
    row = dict(zip(exports.COLUMNS, rows[1]))
    assert row["Πελάτης"] == "Νησίδα Café"
    # The ΑΦΜ is joined on from the clients table, not from the transaction.
    assert row["Α.Φ.Μ."] == "123456789"
    assert row["Συνολικό Ποσό"] == "124,00"


def test_export_honours_the_selected_period(api):
    api.post("/api/transactions", json={
        "client": "Α", "amount": 100, "type": "Έσοδο", "date": "2026-02-10"})
    api.post("/api/transactions", json={
        "client": "Α", "amount": 200, "type": "Έσοδο", "date": "2026-08-10"})

    everything = _rows(api.get("/api/v1/exports/transactions.csv").content.decode())
    assert len(everything) == 3  # header + 2

    q1 = _rows(api.get(
        "/api/v1/exports/transactions.csv?year=2026&quarter=1").content.decode())
    assert len(q1) == 2
    assert q1[1][0] == "2026-02-10"


def test_export_can_be_narrowed_to_one_client(api):
    first = api.post("/api/v1/clients", json={"name": "Πελάτης Α"}).json()
    api.post("/api/v1/clients", json={"name": "Πελάτης Β"})
    api.post("/api/transactions", json={
        "client": "Πελάτης Α", "amount": 100, "type": "Έσοδο"})
    api.post("/api/transactions", json={
        "client": "Πελάτης Β", "amount": 200, "type": "Έσοδο"})

    rows = _rows(api.get(
        f"/api/v1/exports/transactions.csv?client_id={first['id']}"
    ).content.decode())
    assert len(rows) == 2
    assert rows[1][1] == "Πελάτης Α"
    # The name is in the file, so the download name only needs to be unique.
    assert f"client-{first['id']}" in \
        api.get(f"/api/v1/exports/transactions.csv?client_id={first['id']}"
                ).headers["content-disposition"]


def test_exporting_another_tenants_client_is_a_404(api):
    assert api.get(
        "/api/v1/exports/transactions.csv?client_id=999999").status_code == 404


def test_the_iso_dialect_is_reachable_from_the_endpoint(api):
    api.post("/api/transactions", json={
        "client": "Α", "amount": 124, "type": "Έσοδο"})
    body = api.get(
        "/api/v1/exports/transactions.csv?dialect=iso").content.decode("utf-8")
    assert not body.startswith("﻿")
    assert "124.00" in body


def test_export_requires_a_session(api):
    bare = api.__class__(api.app)
    assert bare.get("/api/v1/exports/transactions.csv").status_code == 401


def test_an_expired_tenant_can_still_export_their_own_books(api):
    """A read, deliberately NOT behind the paywall. Locking a customer's data
    inside the product turns a billing problem into a grievance."""
    api.post("/api/transactions", json={
        "client": "Α", "amount": 100, "type": "Έσοδο"})

    from server import database, store, subscription
    with database.session_scope() as session:
        user = store.get_user_by_username(session, "tester")
        user.trial_ends_at = subscription.utcnow() - dt.timedelta(days=1)
        session.add(user)
        session.commit()

    # Writes are gone…
    assert api.post("/api/v1/clients", json={"name": "Β"}).status_code == 402
    # …the export is not.
    res = api.get("/api/v1/exports/transactions.csv")
    assert res.status_code == 200
    assert len(_rows(res.content.decode("utf-8"))) == 2
