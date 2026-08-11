"""
Data exports — the book as a spreadsheet, mounted by server/main.py:

    GET /api/v1/exports/transactions.csv
    GET /api/v1/exports/clients.csv
    GET /api/v1/exports/invoices.csv

All three honour the same year/quarter/month period the dashboard is showing,
plus an optional client filter, so what downloads is what was on screen.

The three tables
----------------
* transactions — every movement in the period. The ledger.
* invoices     — the SUBSET carrying a παραστατικό (a DocType or a DocNumber).
                 Not a separate store: an invoice here is a transaction that
                 was issued against a document, which is exactly the set an
                 accountant reconciles against the ΜΥΦ / e-books. Cash entries
                 and bare corrections have no document and are excluded, which
                 is the whole point of offering it apart from the ledger.
* clients      — the πελατολόγιο: identity columns plus the same period-scoped
                 figures the client cards show, so the sheet can be pivoted
                 without re-deriving anything.

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

Positive amounts
----------------
Every figure is exported as a POSITIVE magnitude, including expenses. Storage
signs them (expenses negative) and the dashboard shows magnitude-plus-colour,
but a spreadsheet has no colour and a column of "-310,00" reads as a correction
rather than as a cost. Direction lives in the Είδος column, in words, which is
also what an accountant filters and pivots on.

The consequence is deliberate and worth knowing: SUM(Σύνολο) is turnover, NOT
the net result. Subtotal by Είδος to get revenue and expenses separately.
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
    # Έσοδο / Έξοδο / Χρεωστούμενο — the ONLY thing carrying direction now that
    # the amounts are unsigned, so it sits immediately before them.
    "Είδος Κίνησης",
    "Καθαρή Αξία",
    "Φ.Π.Α.",
    "Συνολικό Ποσό",
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

    Always a POSITIVE magnitude — see the module docstring. abs() is applied
    here, once, rather than at each of the three call sites, so a new column
    cannot accidentally reintroduce a minus sign.

    Blank rather than 0 when there is no figure at all: a row with no VAT
    recorded is not a row with zero VAT, and exporting 0,00 would let someone
    reconcile a column that was never filled in.
    """
    if value is None:
        return ""
    text = f"{abs(round(float(value), 2)):.2f}"
    return text.replace(".", ",") if dialect == EXCEL else text


def _date(record):
    when = finance.txn_date(record)
    return when.isoformat() if when else ""


def client_key(name):
    """How a client name is matched to its ΑΦΜ. Case-folded in PYTHON, not in
    SQL, for the reason store._key gives: Postgres lower() is collation
    dependent on Greek and SQLite's is ASCII-only."""
    return (name or "").strip().lower()


def _text(value):
    """A trimmed cell value, or "" — never the string "None".

    Every text column goes through this. The fields it guards (DocType,
    DocNumber, Description) are legitimately absent on cash entries and on rows
    written before those columns existed, and a stray "None" in a spreadsheet
    column is worse than an empty one: it sorts, it filters, and it looks like
    data.
    """
    if value is None:
        return ""
    return " ".join(str(value).split())


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
        _text(afm_by_client.get(client_key(client))),
        _text(fields.get("DocType")),
        _text(fields.get("DocNumber")),
        _text(fields.get("Type")),
        _number(net, dialect),
        _number(vat, dialect),
        _number(gross, dialect),
        payment_status(record, paid, today=today),
        _text(fields.get("Description")),
    ]


def transactions_csv(records, afm_by_client=None, paid_by_debt=None,
                     dialect=EXCEL, today=None):
    """Render transactions as CSV text. Returns str; the caller encodes.

    Rows come out OLDEST FIRST — a statement is read forwards, unlike the
    dashboard's newest-first list.
    """
    return _render(COLUMNS, _oldest_first(records),
                   lambda r, d: _row(r, afm_by_client or {}, paid_by_debt or {},
                                     d, today=today),
                   dialect)


# --- Παραστατικά ----------------------------------------------------------
# The invoice sheet's own columns. Document identity comes FIRST — this is the
# sheet someone opens to find "ΤΠΥ-1042", not to read a date column — and the
# VAT rate is carried as well as the amount, because a ΜΥΦ reconciliation is
# done per rate.
INVOICE_COLUMNS = (
    "Ημερομηνία",
    "Τύπος Παραστατικού",
    "Αρ. Παραστατικού",
    "Πελάτης",
    "Α.Φ.Μ.",
    "Είδος Κίνησης",
    "Καθαρή Αξία",
    "Φ.Π.Α.",
    "Συντελεστής Φ.Π.Α.",
    "Συνολικό Ποσό",
    "Κατάσταση Πληρωμής",
    "Ημ/νία Λήξης",
    "Περιγραφή",
)


def is_invoice(record):
    """True when a transaction was issued against a document.

    Either field is enough: a scanned receipt routinely arrives with a type and
    no legible number, and a hand-entered one with a number and no type. A row
    with NEITHER is a cash movement or a correction, and putting it in a sheet
    called Παραστατικά would mean the sheet no longer answers the question it
    was opened to answer.
    """
    fields = record.get("fields", {})
    return bool(_text(fields.get("DocType")) or _text(fields.get("DocNumber")))


def _percent(rate, dialect):
    """A VAT rate as a percentage cell: 0.24 -> "24" / "24,0".

    The caller only reaches this once a VAT AMOUNT was actually recorded — see
    _invoice_row. A rate printed beside a blank Φ.Π.Α. column would invite
    someone to multiply the two and reconcile against a figure the book never
    contained.
    """
    if rate is None:
        return ""
    text = f"{round(float(rate) * 100, 1):g}"
    return text.replace(".", ",") if dialect == EXCEL else text


def _invoice_row(record, afm_by_client, paid_by_debt, dialect, today=None):
    fields = record.get("fields", {})
    gross = amount = finance.amount(record)
    stored_vat = fields.get("VAT_Amount")
    vat = None if stored_vat is None else float(stored_vat)
    net = finance.txn_net(record)
    if net is None:
        net = amount

    client = (fields.get("Category") or "").strip()
    try:
        paid = paid_by_debt.get(int(record.get("id")), 0.0)
    except (TypeError, ValueError):
        paid = 0.0

    due = finance.debt_due_date(record) if finance.is_debt(record) else None

    return [
        _date(record),
        _text(fields.get("DocType")),
        _text(fields.get("DocNumber")),
        client,
        _text(afm_by_client.get(client_key(client))),
        _text(fields.get("Type")),
        _number(net, dialect),
        _number(vat, dialect),
        # Blank whenever the amount is blank: a Χρεωστούμενο carries no VAT
        # until it is settled, but rows written by the form still arrive with a
        # default RATE attached, and printing that alone would manufacture a
        # figure the book does not have.
        _percent(fields.get("VAT_Rate"), dialect) if vat is not None else "",
        _number(gross, dialect),
        payment_status(record, paid, today=today),
        due.isoformat() if due else "",
        _text(fields.get("Description")),
    ]


def invoices_csv(records, afm_by_client=None, paid_by_debt=None,
                 dialect=EXCEL, today=None):
    """The παραστατικά among `records`, oldest first.

    Filtering happens HERE rather than in the endpoint so that "what counts as
    an invoice" has exactly one definition, and the header is emitted even when
    nothing matches — an empty sheet with its columns is a readable answer,
    a zero-byte file is not.
    """
    return _render(INVOICE_COLUMNS,
                   [r for r in _oldest_first(records) if is_invoice(r)],
                   lambda r, d: _invoice_row(r, afm_by_client or {},
                                             paid_by_debt or {}, d, today=today),
                   dialect)


# --- Πελατολόγιο ----------------------------------------------------------
# Identity first, then the period figures. The two halves are deliberately in
# one sheet: a contact list nobody can sort by turnover gets exported once and
# never opened again.
CLIENT_COLUMNS = (
    "Επωνυμία",
    "Α.Φ.Μ.",
    "Επικοινωνία",
    "Κατάσταση",
    "Κινήσεις",
    "Έσοδα",
    "Έξοδα",
    "Καθαρό Φ.Π.Α.",
    "Ανεξόφλητα",
    "Καθαρό Αποτέλεσμα",
    "Ημ/νία Δημιουργίας",
    "Ημ/νία Κλεισίματος",
    "Σημειώσεις",
)

ACTIVE = "Ενεργός"
ARCHIVED = "Αρχειοθετημένος"


def _signed(value, dialect):
    """A figure that KEEPS its sign, for the two columns where it is the point.

    The deliberate exception to the positive-magnitudes rule in the module
    docstring: a negative Καθαρό Αποτέλεσμα is a loss and a negative Καθαρό
    Φ.Π.Α. is a refund due, and stripping those minus signs would not tidy the
    sheet, it would invert its meaning.
    """
    if value is None:
        return ""
    figure = round(float(value), 2)
    text = f"{figure:.2f}"
    return text.replace(".", ",") if dialect == EXCEL else text


def _client_row(client, metrics, dialect):
    # Missing figures become 0, NOT blank — the opposite of the rule the ledger
    # applies to VAT. There, blank means "never recorded" and a 0,00 would let
    # someone reconcile against a number the book does not have. Here the figure
    # IS known: a client with no movements in the period earned exactly nothing,
    # and a blank would break every SUM and pivot over the column.
    m = {k: (0.0 if v is None else v) for k, v in (metrics or {}).items()}
    m = {**{"gross_rev": 0.0, "gross_exp": 0.0, "net_vat": 0.0, "debt": 0.0,
            "net_profit": 0.0, "count": 0}, **m}
    return [
        _text(client.get("name")),
        _text(client.get("afm")),
        _text(client.get("contact")),
        ARCHIVED if client.get("archived") else ACTIVE,
        str(int(m["count"])),
        _number(m["gross_rev"], dialect),
        _number(m["gross_exp"], dialect),
        _signed(m["net_vat"], dialect),
        _number(m["debt"], dialect),
        _signed(m["net_profit"], dialect),
        _text(client.get("created_at"))[:10],
        _text(client.get("closed_date"))[:10],
        _text(client.get("notes")),
    ]


def clients_csv(clients, metrics_by_key=None, dialect=EXCEL):
    """The πελατολόγιο as a spreadsheet.

    `clients` are dicts in the shape store.Client.to_detail() emits;
    `metrics_by_key` maps client_key(name) -> the figures finance already
    computed for the period. A client with no metrics exports with ZEROS rather
    than being dropped: an empty quarter is a fact about the client, and a
    πελατολόγιο that silently omits its quiet clients is not a πελατολόγιο.

    Sorted by name, case-folded — a contact list is looked up alphabetically,
    unlike the ledger, which is read chronologically.
    """
    metrics_by_key = metrics_by_key or {}
    ordered = sorted(clients, key=lambda c: client_key(c.get("name")))
    return _render(
        CLIENT_COLUMNS, ordered,
        lambda c, d: _client_row(c, metrics_by_key.get(client_key(c.get("name"))), d),
        dialect)


# --- Rendering ------------------------------------------------------------
def _oldest_first(records):
    """A statement is read forwards, unlike the dashboard's newest-first list."""
    return sorted(records, key=lambda r: finance.txn_date(r) or dt.date.min)


def _render(columns, rows, row_fn, dialect):
    """Header + rows in the chosen dialect. Returns str; the caller encodes.

    The one place the writer is configured, so a new sheet cannot quietly ship
    with a different delimiter or lose the BOM.
    """
    dialect = dialect if dialect in DIALECTS else EXCEL
    buffer = io.StringIO()
    # QUOTE_MINIMAL with an explicit quotechar: a Greek description routinely
    # contains the semicolon delimiter, and an unquoted one would shift every
    # column after it by one.
    writer = csv.writer(buffer, delimiter=";" if dialect == EXCEL else ",",
                        quotechar='"', quoting=csv.QUOTE_MINIMAL,
                        lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(row_fn(row, dialect))
    text = buffer.getvalue()
    return (_BOM + text) if dialect == EXCEL else text


def filename(kind="transactions", year=None, quarter=None, month=None,
             client=None):
    """A descriptive, ASCII-only download name.

    ASCII deliberately: a Greek filename in Content-Disposition needs RFC 5987
    encoding that older Excel installs and a couple of browsers still mangle
    into an unopenable name. The period is what makes one download
    distinguishable from the next, which is what the name is for.
    """
    parts = [kind]
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
