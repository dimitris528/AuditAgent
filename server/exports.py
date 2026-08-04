"""
Data exports — the book as a spreadsheet, mounted by server/main.py:

    GET /api/v1/exports/transactions.csv

Honours the same year/quarter/month period the dashboard is showing, plus an
optional client filter, so what downloads is what was on screen.

Excel, specifically
-------------------
"CSV" is not one format, and the difference is the whole reason this module
exists rather than a csv.writer inline in main.py. A file that opens cleanly in
a GREEK Excel needs three things that a plain `csv.writer` gets wrong:

  * a UTF-8 BOM, or Excel decodes the file as the system codepage and every
    Greek name arrives as mojibake;
  * a SEMICOLON delimiter, because in an el-GR locale the comma is the decimal
    separator and a comma-delimited file lands entirely in column A;
  * decimal COMMAS in the numbers, so Excel reads 1234,56 as a number it can
    sum rather than as text.

Those three go together — decimal commas are only safe once the delimiter is
not a comma — and they are what `dialect="excel"` (the default) produces. The
`iso` dialect is the opposite choice for anything that is not Excel: no BOM,
comma-delimited, decimal points, i.e. RFC 4180 that pandas and every other tool
reads without arguments.

Signed amounts
--------------
Amounts are exported SIGNED, the way they are stored: revenue and debt
positive, expenses negative. So a plain SUM over the Σύνολο column is the net
result for the period, which is the first thing anyone does with this file. The
Είδος column carries the direction in words for anyone reading rather than
summing.
"""

import csv
import datetime as dt
import io

import finance

# --- Dialects -------------------------------------------------------------
EXCEL = "excel"
ISO = "iso"
DIALECTS = (EXCEL, ISO)

# U+FEFF. Written as the first character of the file, not as bytes, so the
# single encode() at the end produces the standard UTF-8 BOM.
_BOM = "﻿"

# The Greek column headings, in export order. The eight the brief names, plus
# the document number (an export nobody can tie back to a παραστατικό is not
# much of an audit trail) and the free-text description.
COLUMNS = (
    "Ημερομηνία",
    "Πελάτης",
    "Α.Φ.Μ.",
    "Τύπος Παραστατικού",
    "Αρ. Παραστατικού",
    "Είδος",
    "Καθαρή Αξία",
    "Φ.Π.Α.",
    "Σύνολο",
    "Κατάσταση Πληρωμής",
    "Περιγραφή",
)

# --- Κατάσταση Πληρωμής ---------------------------------------------------
# Anything that is not a Χρεωστούμενο has already moved money: an Έσοδο was
# received and an Έξοδο was paid. Only a debt row has a payment state to report.
PAID = "Εξοφλημένο"
UNPAID = "Ανεξόφλητο"
PARTIAL = "Μερικώς εξοφλημένο"
OVERDUE = "Ληξιπρόθεσμο"


def payment_status(record, paid_so_far=0.0, today=None):
    """What the Κατάσταση Πληρωμής column says for one transaction.

    Overdue OUTRANKS partially-paid: a debt that is both is a collection
    problem first, and a spreadsheet filtered to "Ληξιπρόθεσμο" has to show it.
    """
    if not finance.is_debt(record):
        return PAID
    if finance.debt_status(record, today=today) == "overdue":
        return OVERDUE
    return PARTIAL if paid_so_far > 0 else UNPAID


# --- Number and date formatting ------------------------------------------
def _number(value, dialect):
    """A euro amount as the chosen dialect writes it, or "" for a missing one.

    Blank rather than 0 when there is no figure at all: a row with no VAT
    recorded is not a row with zero VAT, and exporting 0,00 would let someone
    reconcile a column that was never filled in.
    """
    if value is None:
        return ""
    text = f"{round(float(value), 2):.2f}"
    return text.replace(".", ",") if dialect == EXCEL else text


def _date(record):
    when = finance.txn_date(record)
    return when.isoformat() if when else ""


def client_key(name):
    """How a client name is matched to its ΑΦΜ. Case-folded in PYTHON, not in
    SQL, for the reason store._key gives: Postgres lower() is collation
    dependent on Greek and SQLite's is ASCII-only."""
    return (name or "").strip().lower()


def _row(record, afm_by_client, paid_by_debt, dialect, today=None):
    fields = record.get("fields", {})
    gross = finance.amount(record)
    # The STORED VAT, deliberately not finance.txn_vat: that helper derives a
    # figure at the default rate when none was recorded, which would print a
    # confident VAT next to a Χρεωστούμενο that has none. An exported column
    # someone reconciles against must be blank when the book is blank.
    stored_vat = fields.get("VAT_Amount")
    vat = None if stored_vat is None else float(stored_vat)
    net = finance.txn_net(record)
    # txn_net is None for a row carrying no VAT at all, in which case the net
    # value IS the gross — there is nothing to subtract.
    if net is None:
        net = gross

    client = (fields.get("Category") or "").strip()
    try:
        paid = paid_by_debt.get(int(record.get("id")), 0.0)
    except (TypeError, ValueError):
        paid = 0.0

    return [
        _date(record),
        client,
        afm_by_client.get(client_key(client), ""),
        fields.get("DocType") or "",
        fields.get("DocNumber") or "",
        fields.get("Type") or "",
        _number(net, dialect),
        _number(vat, dialect),
        _number(gross, dialect),
        payment_status(record, paid, today=today),
        (fields.get("Description") or "").strip(),
    ]


def transactions_csv(records, afm_by_client=None, paid_by_debt=None,
                     dialect=EXCEL, today=None):
    """Render transactions as CSV text. Returns str; the caller encodes.

    Rows come out OLDEST FIRST — a statement is read forwards, unlike the
    dashboard's newest-first list.
    """
    dialect = dialect if dialect in DIALECTS else EXCEL
    afm_by_client = afm_by_client or {}
    paid_by_debt = paid_by_debt or {}

    buffer = io.StringIO()
    # QUOTE_MINIMAL with an explicit quotechar: a Greek description routinely
    # contains the semicolon delimiter, and an unquoted one would shift every
    # column after it by one.
    writer = csv.writer(buffer, delimiter=";" if dialect == EXCEL else ",",
                        quotechar='"', quoting=csv.QUOTE_MINIMAL,
                        lineterminator="\r\n")
    writer.writerow(COLUMNS)
    ordered = sorted(records, key=lambda r: finance.txn_date(r) or dt.date.min)
    for record in ordered:
        writer.writerow(_row(record, afm_by_client, paid_by_debt, dialect,
                             today=today))

    text = buffer.getvalue()
    return (_BOM + text) if dialect == EXCEL else text


def filename(year=None, quarter=None, month=None, client=None):
    """A descriptive, ASCII-only download name.

    ASCII deliberately: a Greek filename in Content-Disposition needs RFC 5987
    encoding that older Excel installs and a couple of browsers still mangle
    into an unopenable name. The period is what makes one download
    distinguishable from the next, which is what the name is for.
    """
    parts = ["transactions"]
    if client:
        # Latin transliteration is not worth it — the client is already in the
        # file. An id keeps the name unique without inventing a spelling.
        parts.append(f"client-{client}")
    if year:
        parts.append(str(year))
    if quarter:
        parts.append(f"Q{quarter}")
    if month:
        parts.append(f"M{month:02d}")
    if not year and not quarter and not month:
        parts.append("all")
    return "-".join(parts) + ".csv"
