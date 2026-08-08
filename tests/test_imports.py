"""
Bulk import — the parser's tolerance, and what actually lands in the book.

Two halves, deliberately separable:

  * the PURE parser (server/imports.py), tested against literal file bytes,
    because the whole point of that module is that a real user's file — cp1253,
    semicolons, "1.234,56", DD/MM/YYYY — needs no preparation;
  * the ENDPOINTS, which additionally have to match rows to existing clients,
    skip what is already on file, and leave the VAT arithmetic identical to a
    transaction typed into the form.

That last one is the assertion worth keeping: an imported row and a typed row
describing the same invoice must store the same cents, or total VAT stops
equalling Σ per-client VATs and the dashboard quietly stops reconciling.
"""

import datetime as dt
import io

import pytest

import finance
from server import imports


# --- Building files -------------------------------------------------------
def csv_bytes(rows, delimiter=";", encoding="utf-8-sig"):
    """A spreadsheet as the bytes a user would actually upload."""
    text = "\r\n".join(delimiter.join(str(c) for c in row) for row in rows)
    return text.encode(encoding)


def xlsx_bytes(rows):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


CLIENT_HEADER = ("Επωνυμία", "Α.Φ.Μ.", "Τηλέφωνο", "Email")
TXN_HEADER = ("Ημερομηνία", "Πελάτης", "Α.Φ.Μ.", "Είδος Κίνησης",
              "Τύπος Παραστατικού", "Αρ. Παραστατικού", "Συνολικό Ποσό",
              "Συντ. Φ.Π.Α.", "Φ.Π.Α.", "Περιγραφή")


def upload(api, path, data, filename="data.csv"):
    return api.post(path, files={"file": (filename, data, "text/csv")})


# ==========================================================================
# The parser
# ==========================================================================
# --- Numbers --------------------------------------------------------------
@pytest.mark.parametrize("written,expected", [
    ("1234.56", 1234.56),
    ("1234,56", 1234.56),          # Greek decimal comma
    ("1.234,56", 1234.56),         # Greek thousands + decimal
    ("1,234.56", 1234.56),         # English thousands + decimal
    ("1.234.567", 1234567.0),      # repeated separator ⇒ thousands
    ("€ 1.240,00", 1240.0),
    ("-310,00", -310.0),
    ("(310,00)", -310.0),          # accounting negative
    ("", None),
    ("   ", None),
    (1240, 1240.0),                # already typed, from a spreadsheet cell
])
def test_amounts_survive_both_decimal_conventions(written, expected):
    assert imports.number(written) == expected


# --- Dates ----------------------------------------------------------------
@pytest.mark.parametrize("written", [
    "2026-04-03", "03/04/2026", "03-04-2026", "3.4.2026", "03/04/26",
    "2026-04-03T00:00:00", dt.date(2026, 4, 3), dt.datetime(2026, 4, 3, 9, 30),
])
def test_dates_are_read_day_first(written):
    """03/04/2026 is 3 April. Guessing per row would scramble a year of
    history one row at a time, so the rule is fixed rather than sniffed."""
    assert imports.date(written) == dt.date(2026, 4, 3)


def test_an_unreadable_date_is_none_rather_than_today():
    assert imports.date("άγνωστη") is None
    assert imports.date("32/13/2026") is None
    assert imports.date("") is None


# --- Types and rates ------------------------------------------------------
@pytest.mark.parametrize("written", ["Έσοδο", "έσοδα", "ΕΣΟΔΑ", "Πώληση",
                                     "Income", "revenue"])
def test_revenue_is_recognised_however_it_is_spelled(written):
    assert imports.txn_type(written) == "Έσοδο"


@pytest.mark.parametrize("written", ["Χρεωστούμενο", "χρεωστουμενα", "Χρέος",
                                     "debt", "Receivable"])
def test_debt_is_recognised_however_it_is_spelled(written):
    assert imports.txn_type(written) == finance.DEBT_TYPE


@pytest.mark.parametrize("written,expected", [
    ("24%", 0.24), ("24", 0.24), ("0,24", 0.24), (0.24, 0.24), ("13", 0.13),
    ("0", 0.0), ("", None),
])
def test_vat_rates_are_read_as_fractions(written, expected):
    assert imports.vat_rate(written) == expected


def test_the_vat_amount_column_is_not_confused_with_the_rate_column():
    """"Φ.Π.Α." is euros and "Φ.Π.Α. %" is a rate. Folding the two together
    would file a 24 % rate as €24 of VAT, which reconciles to nothing."""
    parsed = imports.parse_transactions(csv_bytes([
        ("Ημερομηνία", "Πελάτης", "Είδος Κίνησης", "Συνολικό Ποσό",
         "Φ.Π.Α. %", "Φ.Π.Α."),
        ("2026-01-15", "Νησίδα Café", "Έσοδο", "1240,00", "24%", "240,00"),
    ]))
    assert parsed.errors == []
    row = parsed.rows[0]
    assert row["vat_rate"] == 0.24
    assert row["vat_amount"] == 240.0


# --- Files ----------------------------------------------------------------
def test_a_greek_windows_csv_is_decoded_rather_than_mojibaked():
    """cp1253 is what "Save as CSV" produces in a Greek Excel — the single
    most likely file this feature will ever be handed."""
    parsed = imports.parse_clients(
        csv_bytes([CLIENT_HEADER, ("Παπαδόπουλος Α.Ε.", "123456789", "", "")],
                  encoding="cp1253"))
    assert parsed.rows[0]["name"] == "Παπαδόπουλος Α.Ε."


@pytest.mark.parametrize("delimiter", [";", ",", "\t"])
def test_all_three_delimiters_are_detected(delimiter):
    parsed = imports.parse_clients(
        csv_bytes([CLIENT_HEADER, ("Νησίδα Café", "123456789", "2101234567",
                                   "info@nisida.gr")], delimiter=delimiter))
    assert len(parsed.rows) == 1
    assert parsed.rows[0]["afm"] == "123456789"


def test_an_xlsx_workbook_reads_the_same_as_the_csv():
    parsed = imports.parse_transactions(xlsx_bytes([
        TXN_HEADER,
        ("2026-01-15", "Νησίδα Café", "123456789", "Έσοδο",
         "Τιμολόγιο Πώλησης", "ΤΠΥ-1042", 1240.0, 0.24, 240.0, "Ιανουάριος"),
    ]))
    assert parsed.errors == []
    row = parsed.rows[0]
    assert row["date"] == dt.date(2026, 1, 15)
    assert row["amount"] == 1240.0
    assert row["vat_rate"] == 0.24
    assert row["doc_type"] == finance.DOC_SALES_INVOICE


def test_headers_are_matched_through_their_aliases():
    """A legacy export names its columns whatever it likes; only the meaning
    has to survive."""
    parsed = imports.parse_clients(csv_bytes([
        ("Name", "VAT Number", "Phone", "E-mail"),
        ("Νησίδα Café", "EL123456789", "2101234567", "info@nisida.gr"),
    ]))
    assert parsed.rows[0] == {
        "row": 2, "name": "Νησίδα Café", "afm": "EL123456789",
        "contact": "2101234567 · info@nisida.gr", "notes": None,
    }


def test_a_file_without_the_required_columns_is_refused_whole():
    with pytest.raises(imports.ImportFileError) as excinfo:
        imports.parse_clients(csv_bytes([("Χρώμα", "Μέγεθος"), ("μπλε", "42")]))
    assert "πρότυπο" in str(excinfo.value)


def test_a_legacy_xls_says_what_to_do_about_it():
    with pytest.raises(imports.ImportFileError) as excinfo:
        imports.parse_clients(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    assert ".xlsx" in str(excinfo.value)


def test_blank_rows_are_skipped_rather_than_counted_as_failures():
    parsed = imports.parse_clients(csv_bytes([
        CLIENT_HEADER,
        ("Νησίδα Café", "123456789", "", ""),
        ("", "", "", ""),
        ("Παπαδόπουλος Α.Ε.", "987654321", "", ""),
    ]))
    assert parsed.total == 2
    assert len(parsed.rows) == 2
    assert parsed.errors == []


# --- Per-row validation ---------------------------------------------------
def test_one_bad_row_does_not_reject_the_others():
    """The whole difference between a migration tool and a validator."""
    parsed = imports.parse_clients(csv_bytes([
        CLIENT_HEADER,
        ("Νησίδα Café", "123456789", "", ""),
        ("", "111111111", "", ""),                 # no name
        ("Παπαδόπουλος Α.Ε.", "987654321", "", ""),
    ]))
    assert [r["name"] for r in parsed.rows] == ["Νησίδα Café", "Παπαδόπουλος Α.Ε."]
    assert parsed.errors == [{"row": 3, "message": "Λείπει η επωνυμία του πελάτη."}]
    assert parsed.total == len(parsed.rows) + len(parsed.errors)


def test_a_suspect_email_is_a_warning_and_the_value_is_kept():
    parsed = imports.parse_clients(csv_bytes([
        CLIENT_HEADER, ("Νησίδα Café", "123456789", "2101234567", "info@nisida"),
    ]))
    assert len(parsed.rows) == 1
    assert "info@nisida" in parsed.rows[0]["contact"]
    assert parsed.warnings[0]["row"] == 2


def test_a_transaction_without_a_date_is_refused_not_dated_today():
    """finance falls back to createdTime when a row has no date, which would
    file five years of history as this morning."""
    parsed = imports.parse_transactions(csv_bytes([
        TXN_HEADER,
        ("", "Νησίδα Café", "", "Έσοδο", "", "", "1240,00", "24%", "", ""),
    ]))
    assert parsed.rows == []
    assert parsed.errors[0]["message"] == "Λείπει η ημερομηνία."


def test_an_unknown_document_type_is_dropped_with_a_warning():
    """Erroring would block a whole legacy export over a label this book has
    no equivalent for; the transaction itself still books correctly."""
    parsed = imports.parse_transactions(csv_bytes([
        TXN_HEADER,
        ("2026-01-15", "Νησίδα Café", "", "Έσοδο", "ΧΕΙΡΟΓΡΑΦΟ ΔΕΛΤΙΟ", "",
         "1240,00", "24%", "", ""),
    ]))
    assert len(parsed.rows) == 1
    assert parsed.rows[0]["doc_type"] is None
    assert "ΧΕΙΡΟΓΡΑΦΟ ΔΕΛΤΙΟ" in parsed.warnings[0]["message"]


def test_a_missing_type_falls_back_to_the_amounts_own_sign():
    parsed = imports.parse_transactions(csv_bytes([
        ("Ημερομηνία", "Πελάτης", "Είδος Κίνησης", "Συνολικό Ποσό"),
        ("2026-01-15", "Νησίδα Café", "", "-310,00"),
        ("2026-01-16", "Νησίδα Café", "", "1240,00"),
    ]))
    assert [r["type"] for r in parsed.rows] == ["Έξοδο", "Έσοδο"]
    # Magnitude only — the Type carries the direction from here on.
    assert [r["amount"] for r in parsed.rows] == [310.0, 1240.0]
    assert len(parsed.warnings) == 2


def test_a_credit_note_cannot_be_imported_as_a_debt():
    parsed = imports.parse_transactions(csv_bytes([
        TXN_HEADER,
        ("2026-01-15", "Νησίδα Café", "", "Χρεωστούμενο", "Πιστωτικό", "",
         "500,00", "24%", "", ""),
    ]))
    assert parsed.rows == []
    assert "πιστωτικό" in parsed.errors[0]["message"].lower()


def test_a_zero_amount_is_refused():
    parsed = imports.parse_transactions(csv_bytes([
        TXN_HEADER,
        ("2026-01-15", "Νησίδα Café", "", "Έσοδο", "", "", "0,00", "24%", "", ""),
    ]))
    assert parsed.rows == []
    assert parsed.errors[0]["message"] == "Το ποσό είναι μηδενικό."


# --- Templates ------------------------------------------------------------
@pytest.mark.parametrize("kind", imports.KINDS)
def test_the_template_round_trips_through_its_own_parser(kind):
    """The strongest thing this file asserts: whatever the template shows a
    user, uploading it back has to work — including the Χρεωστούμενο row with
    its due date and no VAT figure."""
    body, name = imports.template(kind)
    assert name.endswith(".csv")
    assert body.startswith("﻿")          # Excel needs the BOM
    assert ";" in body.splitlines()[0]        # and the semicolon

    data = body.encode("utf-8")
    parsed = (imports.parse_clients(data) if kind == imports.CLIENTS
              else imports.parse_transactions(data))
    assert parsed.errors == []
    assert parsed.warnings == []
    assert len(parsed.rows) == parsed.total > 0


def test_the_transaction_template_shows_a_debt_with_its_due_date():
    body, _ = imports.template(imports.TRANSACTIONS)
    rows = imports.parse_transactions(body.encode("utf-8")).rows
    debt = [r for r in rows if r["type"] == finance.DEBT_TYPE]
    assert len(debt) == 1
    assert debt[0]["due_date"] == dt.date(2026, 3, 3)


# ==========================================================================
# The endpoints
# ==========================================================================
def test_importing_clients_reports_what_it_did(api):
    res = upload(api, "/api/import/clients", csv_bytes([
        CLIENT_HEADER,
        ("Νησίδα Café", "123456789", "2101234567", "info@nisida.gr"),
        ("Παπαδόπουλος Α.Ε.", "987654321", "6941234567", "info@papadopoulos.gr"),
        ("", "", "", ""),
    ]))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["imported"] == 2
    assert body["failed"] == 0
    assert body["total_rows"] == 2
    assert body["message"] == "Εισήχθησαν 2 πελάτες επιτυχώς!"

    listed = api.get("/api/v1/clients").json()["clients"]
    assert sorted(c["name"] for c in listed) == ["Νησίδα Café", "Παπαδόπουλος Α.Ε."]
    nisida = next(c for c in listed if c["name"] == "Νησίδα Café")
    assert nisida["afm"] == "123456789"
    assert nisida["contact"] == "2101234567 · info@nisida.gr"


def test_one_imported_client_reads_as_singular_greek(api):
    res = upload(api, "/api/import/clients",
                 csv_bytes([CLIENT_HEADER, ("Νησίδα Café", "", "", "")]))
    assert res.json()["message"] == "Εισήχθη 1 πελάτης επιτυχώς!"


def test_re_uploading_the_same_client_file_is_a_no_op(api):
    data = csv_bytes([CLIENT_HEADER, ("Νησίδα Café", "123456789", "", "")])
    assert upload(api, "/api/import/clients", data).json()["imported"] == 1

    body = upload(api, "/api/import/clients", data).json()
    assert body["imported"] == 0
    assert body["skipped"] == 1
    assert "Νησίδα Café" in body["skipped_rows"][0]["message"]
    assert len(api.get("/api/v1/clients").json()["clients"]) == 1


def test_a_client_listed_twice_in_one_file_is_created_once(api):
    """Under two spellings, which is exactly how a legacy export writes it."""
    body = upload(api, "/api/import/clients", csv_bytes([
        CLIENT_HEADER,
        ("Νησίδα Café", "123456789", "", ""),
        ("ΝΗΣΙΔΑ CAFE", "", "", ""),
        ("Εντελώς Άλλη Επωνυμία", "EL123456789", "", ""),   # same ΑΦΜ
    ])).json()
    assert body["imported"] == 1
    assert body["skipped"] == 2
    assert len(api.get("/api/v1/clients").json()["clients"]) == 1


def test_an_import_collides_with_a_client_typed_into_the_app(api):
    api.post("/api/v1/clients", json={"name": "Νησίδα Café", "afm": "123456789"})
    body = upload(api, "/api/import/clients", csv_bytes([
        CLIENT_HEADER, ("νησιδα cafe", "", "", ""),
    ])).json()
    assert body["imported"] == 0
    assert body["skipped"] == 1


def test_importing_transactions_matches_clients_by_afm_then_name(api):
    api.post("/api/v1/clients", json={"name": "Νησίδα Café", "afm": "123456789"})

    body = upload(api, "/api/import/transactions", csv_bytes([
        TXN_HEADER,
        # Matched by ΑΦΜ despite the name being spelled differently.
        ("2026-01-15", "ΝΗΣΙΔΑ CAFE ΑΕ", "EL123456789", "Έσοδο",
         "Τιμολόγιο Πώλησης", "ΤΠΥ-1042", "1240,00", "24%", "240,00", "Ιαν"),
        # Matched by name, no ΑΦΜ given.
        ("2026-01-20", "Νησίδα Café", "", "Έξοδο", "Δαπάνη/Έξοδο", "ΤΔΑ-88",
         "310,00", "24%", "60,00", "Υλικά"),
    ])).json()

    assert body["imported"] == 2
    assert body["clients_created"] == 0
    assert body["message"] == "Εισήχθησαν 2 κινήσεις επιτυχώς!"
    # One client, not three.
    assert len(api.get("/api/v1/clients").json()["clients"]) == 1

    rows = api.get("/api/transactions").json()["transactions"]
    assert {r["client"] for r in rows} == {"Νησίδα Café"}


def test_a_transaction_for_an_unknown_client_creates_it(api):
    body = upload(api, "/api/import/transactions", csv_bytes([
        TXN_HEADER,
        ("2026-01-15", "Καινούριος Πελάτης", "123456789", "Έσοδο", "", "",
         "1240,00", "24%", "240,00", ""),
    ])).json()
    assert body["imported"] == 1
    assert body["clients_created"] == 1

    clients = api.get("/api/v1/clients").json()["clients"]
    assert clients[0]["name"] == "Καινούριος Πελάτης"
    assert clients[0]["afm"] == "123456789"


def test_an_imported_row_stores_the_same_cents_as_a_typed_one(api):
    """The assertion this whole feature rests on: two ways in, one arithmetic.
    A second rounding rule is how total VAT stops equalling Σ per-client VATs."""
    api.post("/api/transactions", json={
        "client": "Πληκτρολογημένος", "amount": 1240.0, "type": "Έσοδο",
        "vat_rate": 0.24, "date": "2026-01-15",
        "doc_type": finance.DOC_SALES_INVOICE,
    })
    upload(api, "/api/import/transactions", csv_bytes([
        TXN_HEADER,
        ("2026-01-15", "Εισηγμένος", "", "Έσοδο", "Τιμολόγιο Πώλησης", "",
         "1240,00", "24%", "", ""),
    ]))

    rows = {r["client"]: r for r in api.get("/api/transactions").json()["transactions"]}
    typed, imported = rows["Πληκτρολογημένος"], rows["Εισηγμένος"]
    assert imported["amount"] == typed["amount"] == 1240.0
    assert imported["vat_amount"] == typed["vat_amount"]
    assert imported["net_amount"] == typed["net_amount"]


def test_an_expense_is_booked_negative_and_a_debt_positive(api):
    upload(api, "/api/import/transactions", csv_bytes([
        TXN_HEADER,
        ("2026-01-20", "Προμηθευτής", "", "Έξοδο", "", "", "310,00", "24%", "", ""),
        ("2026-02-01", "Οφειλέτης", "", "Χρεωστούμενο", "", "", "500,00",
         "24%", "", ""),
    ]))
    rows = {r["client"]: r for r in api.get("/api/transactions").json()["transactions"]}
    # The API reports magnitudes; the sign shows in the flags and the VAT.
    assert rows["Προμηθευτής"]["is_revenue"] is False
    assert rows["Προμηθευτής"]["vat_amount"] == 60.0
    assert rows["Οφειλέτης"]["is_debt"] is True
    # A Χρεωστούμενο carries only the rate — VAT is stamped on Εξόφληση.
    assert rows["Οφειλέτης"]["vat_amount"] is None


def test_a_credit_note_import_reverses_within_its_own_bucket(api):
    upload(api, "/api/import/transactions", csv_bytes([
        TXN_HEADER,
        ("2026-01-15", "Νησίδα Café", "", "Έσοδο", "Πιστωτικό", "ΠΙΣ-1", "124,00",
         "24%", "", ""),
    ]))
    header = api.get("/api/dashboard").json()["header"]
    # Less revenue, NOT an expense.
    assert header["total_gross_rev"] == -124.0
    assert header["total_gross_exp"] == 0.0


def test_an_invoice_already_on_file_is_skipped(api):
    data = csv_bytes([
        TXN_HEADER,
        ("2026-01-15", "Νησίδα Café", "123456789", "Έσοδο", "Τιμολόγιο Πώλησης",
         "ΤΠΥ-1042", "1240,00", "24%", "240,00", ""),
    ])
    assert upload(api, "/api/import/transactions", data).json()["imported"] == 1

    body = upload(api, "/api/import/transactions", data).json()
    assert body["imported"] == 0
    assert body["skipped"] == 1
    assert "ΤΠΥ-1042" in body["skipped_rows"][0]["message"]
    assert len(api.get("/api/transactions").json()["transactions"]) == 1


def test_a_partly_broken_file_imports_the_rows_it_can(api):
    body = upload(api, "/api/import/transactions", csv_bytes([
        TXN_HEADER,
        ("2026-01-15", "Νησίδα Café", "", "Έσοδο", "", "", "1240,00", "24%", "", ""),
        ("όχι ημερομηνία", "Νησίδα Café", "", "Έσοδο", "", "", "500,00", "24%", "", ""),
        ("2026-01-17", "Νησίδα Café", "", "Έσοδο", "", "", "620,00", "24%", "", ""),
    ])).json()

    assert body["imported"] == 2
    assert body["failed"] == 1
    assert body["errors"][0]["row"] == 3
    assert body["total_rows"] == body["imported"] + body["skipped"] + body["failed"]


def test_a_file_that_is_not_a_spreadsheet_is_refused_with_a_reason(api):
    res = upload(api, "/api/import/clients", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
                 filename="photo.png")
    assert res.status_code == 422
    assert "πρότυπο" in res.json()["detail"]


def test_an_oversized_upload_is_refused_before_it_is_parsed(api):
    res = upload(api, "/api/import/clients", b"a" * (imports.MAX_BYTES + 1024))
    assert res.status_code == 413


def test_an_empty_upload_is_refused(api):
    assert upload(api, "/api/import/clients", b"").status_code == 422


# --- Templates over the wire ----------------------------------------------
@pytest.mark.parametrize("kind,filename", [
    ("clients", "protypo-pelaton.csv"),
    ("transactions", "protypo-kiniseon.csv"),
])
def test_the_template_downloads_as_an_excel_ready_csv(api, kind, filename):
    res = api.get(f"/api/import/templates/{kind}")
    assert res.status_code == 200
    assert filename in res.headers["content-disposition"]
    assert res.content.startswith(b"\xef\xbb\xbf")     # UTF-8 BOM, for Excel


def test_an_unknown_template_is_a_404(api):
    assert api.get("/api/import/templates/invoices").status_code == 404


# --- Tenancy --------------------------------------------------------------
@pytest.mark.parametrize("path", [
    "/api/import/clients", "/api/import/transactions",
])
def test_import_requires_a_session(path):
    from fastapi.testclient import TestClient
    from server.main import app

    with TestClient(app) as client:
        res = client.post(path, files={"file": ("x.csv", b"a;b", "text/csv")})
    assert res.status_code == 401


def test_an_import_lands_only_in_the_callers_own_book(api):
    """Tenancy comes from the token, and there is no field in the file that
    could point the rows anywhere else."""
    upload(api, "/api/import/clients",
           csv_bytes([CLIENT_HEADER, ("Νησίδα Café", "123456789", "", "")]))

    other = api.post("/api/v1/auth/register", json={
        "username": "outsider", "email": "outsider@example.com",
        "password": "correct-horse-battery",
    })
    token = other.json()["access_token"]
    listed = api.get("/api/v1/clients",
                     headers={"Authorization": f"Bearer {token}"}).json()
    assert listed["clients"] == []
