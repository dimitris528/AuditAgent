"""
Bulk data import — a spreadsheet in, clients and historic transactions out.

Mounted by server/main.py:

    POST /api/import/clients
    POST /api/import/transactions
    GET  /api/import/templates/{kind}

This module is PURE: it turns uploaded bytes into validated rows and says what
was wrong with the ones it could not use. Nothing here touches the database —
the matching, the duplicate guard and the writes live in store.import_clients /
store.import_transactions, so the parsing rules below can be tested against a
literal file with no fixtures at all.

The file a real user actually has
---------------------------------
This is the front door for someone leaving Excel or a legacy Greek accounting
package, so the parser meets that file where it is rather than demanding a
canonical one:

  * **Any of three encodings.** UTF-8 (with or without a BOM) first, then
    cp1253 — what a Greek Windows Excel writes by default, and the reason an
    import of "Îáðáäüðïõëïò" is otherwise the first thing a new user sees.
  * **Any of three delimiters.** Semicolon, comma or tab, decided per file by
    counting them in the header. Greek Excel writes semicolons (the comma is
    the decimal separator there), and the sibling export module writes them for
    the same reason — see server/exports.py.
  * **Both decimal conventions.** "1.234,56" and "1,234.56" are the same
    €1234.56. _number resolves them by position rather than by locale, so one
    file may legitimately contain both.
  * **Day-first dates.** 03/04/2026 is 3 April, never 4 March. Greek documents
    are DD/MM/YYYY and guessing per row would silently scramble a year of
    history; ISO dates are recognised by shape and are unambiguous.
  * **Whatever the columns are called.** Headers are matched through an alias
    table (_CLIENT_COLUMNS / _TXN_COLUMNS), accent- and case-insensitively, so
    "ΑΦΜ", "Α.Φ.Μ." and "VAT number" are one column.

Errors are per ROW, never per file
----------------------------------
One unparseable date must not reject the other 499 rows. Every row is validated
independently and lands in exactly one of three buckets:

    rows      — usable, and about to be written
    errors    — NOT imported, with the row number and what was wrong
    warnings  — imported, but something was dropped or inferred

Only a file that cannot be read AT ALL (not a spreadsheet, no recognisable
header, too big) raises ImportFileError and rejects the whole upload. That
split is the difference between a migration tool and a validator: the user
fixes the twelve rows the summary names and re-uploads only those.
"""

import csv
import datetime as dt
import io
import re
from dataclasses import dataclass, field

import finance
from server.text import afm_key, name_key

# --- Limits ---------------------------------------------------------------
# Generous for a spreadsheet and small enough that a mis-picked file (a video,
# a database dump) is refused before it is read into memory. 5 MB of CSV is
# already tens of thousands of rows.
MAX_BYTES = 5 * 1024 * 1024
# The row cap is the one that actually binds. Each imported transaction is a
# duplicate-checked insert, and a request that walks a hundred thousand of them
# would hit the statement timeout with nothing to show for it.
MAX_ROWS = 5000

# How many issues travel back in the summary. The COUNTS are always exact; it
# is the per-row lists that are capped, because a wholly mis-mapped file
# produces one error per row and nobody reads the four-thousandth.
MAX_ISSUES = 100

CLIENTS = "clients"
TRANSACTIONS = "transactions"
KINDS = (CLIENTS, TRANSACTIONS)

# ZIP magic — every .xlsx is a zip archive. Sniffed from the CONTENT rather
# than the filename: browsers and legacy exporters disagree wildly about the
# MIME type of a spreadsheet, and a file renamed .csv is still a zip.
_XLSX_MAGIC = b"PK\x03\x04"
# OLE2 compound document — the pre-2007 .xls binary format, which openpyxl
# cannot read. Detected only so the refusal can say what to do about it.
_XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


class ImportFileError(RuntimeError):
    """The upload cannot be read at all. Message is user-facing Greek."""

    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


@dataclass
class ParsedFile:
    """The outcome of reading one upload.

    `total` counts the DATA rows that were looked at (blank ones excluded), so
    total == len(rows) + len(errors) always holds and the summary's arithmetic
    is checkable by the person reading it.
    """

    rows: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    total: int = 0


def _issue(row, message):
    return {"row": row, "message": message}


# --------------------------------------------------------------------------
# Header matching
# --------------------------------------------------------------------------
def _head(value):
    """The canonical form two column headings are compared by.

    name_key does the heavy lifting (casefold, drop accents and punctuation);
    spaces come out too, so "Συνολικό Ποσό" and "ΣΥΝΟΛΙΚΟΠΟΣΟ" agree.

    "%" is preserved as a `pct` suffix rather than dropped with the rest of the
    punctuation, and that is load-bearing: "Φ.Π.Α." is the VAT in euros while
    "Φ.Π.Α. %" is the rate, and folding them together would file a 24 % rate as
    €24 of VAT.
    """
    text = str(value or "")
    pct = "%" in text
    key = name_key(text).replace(" ", "")
    return f"{key}pct" if (pct and key) else key


def _aliases(spec):
    """{header key: field} from {field: (heading, ...)}, so the tables below
    read in the direction they are written in."""
    out = {}
    for field_name, headings in spec.items():
        for heading in headings:
            out.setdefault(_head(heading), field_name)
    return out


_CLIENT_COLUMNS = _aliases({
    "name": ("Επωνυμία", "Όνομα", "Πελάτης", "Επωνυμία Πελάτη", "Name",
             "Client", "Client Name", "Company", "Customer"),
    "afm": ("ΑΦΜ", "Α.Φ.Μ.", "AFM", "ΑΦΜ Πελάτη", "VAT", "VAT Number", "ΤΙΝ",
            "Tax ID", "Tax Number"),
    "phone": ("Τηλέφωνο", "Τηλ", "Τηλέφωνα", "Κινητό", "Phone", "Telephone",
              "Mobile", "Phone Number"),
    "email": ("Email", "E-mail", "Ηλεκτρονικό Ταχυδρομείο", "Mail",
              "Email Address"),
    "notes": ("Σημειώσεις", "Παρατηρήσεις", "Notes", "Comments"),
})

_TXN_COLUMNS = _aliases({
    "date": ("Ημερομηνία", "Ημ/νία", "Date", "Ημερομηνία Παραστατικού",
             "Invoice Date"),
    "client": ("Πελάτης", "Επωνυμία", "Όνομα", "Client", "Name", "Category",
               "Customer", "Supplier", "Προμηθευτής"),
    "afm": ("ΑΦΜ", "Α.Φ.Μ.", "AFM", "ΑΦΜ Πελάτη", "VAT Number", "ΤΙΝ",
            "Counterparty AFM"),
    "type": ("Είδος Κίνησης", "Είδος", "Τύπος Κίνησης", "Κίνηση", "Type",
             "Transaction Type", "Entry Type"),
    "doc_type": ("Τύπος Παραστατικού", "Παραστατικό", "Είδος Παραστατικού",
                 "Document Type", "Doc Type"),
    "doc_number": ("Αρ. Παραστατικού", "Αριθμός Παραστατικού", "Αρ. Τιμολογίου",
                   "Doc Number", "Document Number", "Invoice Number",
                   "Invoice No"),
    "amount": ("Συνολικό Ποσό", "Ποσό", "Σύνολο", "Αξία", "Total", "Amount",
               "Gross", "Gross Amount"),
    "net_amount": ("Καθαρή Αξία", "Καθαρό Ποσό", "Net", "Net Amount",
                   "Net Value"),
    # Ordered AFTER vat_amount would be wrong: _aliases keeps the FIRST field
    # a heading is claimed by, and "Φ.Π.Α. %" only reaches vat_rate because
    # _head gives it a distinct `pct` key.
    "vat_rate": ("Συντ. Φ.Π.Α.", "Συντελεστής Φ.Π.Α.", "Φ.Π.Α. %", "ΦΠΑ%",
                 "VAT Rate", "VAT %", "Rate"),
    "vat_amount": ("Φ.Π.Α.", "ΦΠΑ", "Ποσό Φ.Π.Α.", "VAT", "VAT Amount", "Tax"),
    "description": ("Περιγραφή", "Αιτιολογία", "Σχόλια", "Description",
                    "Notes", "Memo"),
    "due_date": ("Ημερομηνία Λήξης", "Λήξη", "Προθεσμία", "Due Date",
                 "Payment Due"),
})


def _index(headers, aliases):
    """{field: column index} for the headings this file actually has.

    First occurrence wins: a sheet with two "Ποσό" columns uses the left one
    rather than silently preferring whichever came last.
    """
    found = {}
    for position, heading in enumerate(headers):
        field_name = aliases.get(_head(heading))
        if field_name and field_name not in found:
            found[field_name] = position
    return found


# --------------------------------------------------------------------------
# Reading the bytes
# --------------------------------------------------------------------------
def _decode(data):
    """CSV bytes as text, trying the encodings a Greek book actually arrives in.

    cp1253 is not a fallback for tidiness: it is what "Save as CSV" produces in
    a Greek Windows Excel, and it decodes every byte sequence without raising —
    so it must be tried AFTER utf-8, or a perfectly good UTF-8 file would be
    silently mojibake'd.
    """
    for encoding in ("utf-8-sig", "cp1253"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    # latin-1 maps every byte to something. Reached only by a file that is
    # neither of the above, where garbled text still beats refusing to open it.
    return data.decode("latin-1", errors="replace")


def _delimiter(header_line):
    """Whichever of ; , tab appears most in the header row.

    Counting beats csv.Sniffer here: the sniffer looks at the whole sample and
    a single Greek description containing a comma is enough to talk it out of
    the semicolon the rest of the file is using.
    """
    counts = {d: header_line.count(d) for d in (";", ",", "\t")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else ";"


def _read_csv(data):
    text = _decode(data)
    if not text.strip():
        raise ImportFileError("Το αρχείο είναι κενό.")
    first = text.splitlines()[0]
    reader = csv.reader(io.StringIO(text), delimiter=_delimiter(first))
    return [row for row in reader]


def _read_xlsx(data):
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover - openpyxl is a hard requirement
        raise ImportFileError(
            "Η ανάγνωση αρχείων Excel δεν είναι διαθέσιμη σε αυτόν τον "
            "διακομιστή. Αποθηκεύστε το αρχείο ως CSV.", status=503)
    try:
        # read_only streams the sheet instead of building a cell object per
        # cell; data_only hands back the cached RESULT of a formula, which is
        # the number the user sees, rather than "=SUM(D2:D9)".
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ImportFileError(f"Το αρχείο Excel δεν μπόρεσε να διαβαστεί ({exc}).")
    try:
        sheet = book.worksheets[0] if book.worksheets else None
        if sheet is None:
            raise ImportFileError("Το αρχείο Excel δεν περιέχει φύλλα εργασίας.")
        # Capped one row past the limit so "too many rows" is still detectable
        # without materialising a runaway sheet.
        rows = []
        for values in sheet.iter_rows(values_only=True):
            rows.append(list(values))
            if len(rows) > MAX_ROWS + 1:
                break
        return rows
    finally:
        book.close()


def _table(data):
    """(header row, data rows) from CSV or XLSX bytes.

    Leading blank lines are skipped: an export that starts with a title row and
    a gap is common enough, and the first row with content is the header.
    """
    if not data:
        raise ImportFileError("Δεν στάλθηκε αρχείο.")
    if len(data) > MAX_BYTES:
        raise ImportFileError(
            f"Το αρχείο ξεπερνά τα {MAX_BYTES // (1024 * 1024)} MB.",
            status=413)
    if data.startswith(_XLS_MAGIC):
        raise ImportFileError(
            "Τα παλιά αρχεία .xls δεν υποστηρίζονται. Αποθηκεύστε το ως .xlsx "
            "ή ως CSV και δοκιμάστε ξανά.")
    rows = _read_xlsx(data) if data.startswith(_XLSX_MAGIC) else _read_csv(data)

    for position, row in enumerate(rows):
        if any(_cell(value) for value in row):
            return row, rows[position + 1:]
    raise ImportFileError("Το αρχείο δεν περιέχει δεδομένα.")


def _cell(value):
    """One cell as trimmed text. A date or a number from a spreadsheet arrives
    typed, so it is stringified only for the callers that want text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value).strip()


# --------------------------------------------------------------------------
# Field parsing
# --------------------------------------------------------------------------
def number(value):
    """A euro figure from a cell, or None when there is nothing to read.

    Both decimal conventions are accepted and resolved by POSITION rather than
    by locale, because one exported file routinely contains both:

        "1.234,56" and "1,234.56"  → 1234.56   (last separator is the decimal)
        "1.234.567"                → 1234567.0 (repeated ⇒ thousands)
        "1234,56"                  → 1234.56   (lone comma ⇒ Greek decimal)

    Accounting exports also write a negative in parentheses, so "(310,00)" is
    -310.0. Currency symbols and spaces are dropped.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^0-9,.\-]", "", text)
    if cleaned.startswith("-"):
        negative = True
    cleaned = cleaned.replace("-", "")
    if not cleaned:
        return None

    if "." in cleaned and "," in cleaned:
        decimal = "." if cleaned.rfind(".") > cleaned.rfind(",") else ","
        cleaned = cleaned.replace("," if decimal == "." else ".", "")
    elif cleaned.count(",") > 1 or cleaned.count(".") > 1:
        # Repeated separators can only be thousands markers.
        cleaned = cleaned.replace(",", "").replace(".", "")
        decimal = "."
    else:
        decimal = "," if "," in cleaned else "."
    cleaned = cleaned.replace(decimal, ".")

    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    return -parsed if negative else parsed


# Recognised by shape, in the order they are tried. ISO first because it is the
# only unambiguous one; everything after it is DAY-first — see the module
# docstring on why that is a rule and not a guess.
_DATE_PATTERNS = (
    (re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$"), (1, 2, 3)),
    (re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$"), (3, 2, 1)),
    (re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2})$"), (3, 2, 1)),
)


def date(value):
    """A date from a cell, or None.

    A spreadsheet cell formatted as a date arrives already typed, which is the
    common case for XLSX and skips the patterns entirely. A two-digit year is
    read as 20YY: this importer exists to load recent history, and 26 meaning
    1926 has never been what anyone meant.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value

    text = str(value).strip()
    if not text:
        return None
    # An ISO timestamp ("2026-01-15 00:00:00") is a date with noise after it.
    text = text.split("T")[0].split(" ")[0].strip()
    for pattern, (y, m, d) in _DATE_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        year, month, day = (int(match.group(y)), int(match.group(m)),
                            int(match.group(d)))
        if year < 100:
            year += 2000
        try:
            return dt.date(year, month, day)
        except ValueError:
            return None
    return None


_TYPE_ALIASES = {}
for _canonical, _spellings in {
    "Έσοδο": ("Έσοδο", "Έσοδα", "Είσπραξη", "Πώληση", "Τιμολόγιο",
              "Income", "Revenue", "Sale", "Sales", "Credit", "In"),
    "Έξοδο": ("Έξοδο", "Έξοδα", "Δαπάνη", "Πληρωμή", "Αγορά",
              "Expense", "Cost", "Purchase", "Debit", "Out"),
    finance.DEBT_TYPE: ("Χρεωστούμενο", "Χρεωστούμενα", "Χρέος", "Οφειλή",
                        "Ανεξόφλητο", "Debt", "Receivable", "Payable",
                        "Outstanding", "Unpaid"),
}.items():
    for _spelling in _spellings:
        _TYPE_ALIASES.setdefault(name_key(_spelling), _canonical)


def txn_type(value):
    """One of finance's three Types, or None when the cell says something else.

    Accent- and case-insensitive, and deliberately generous: a legacy export
    labels the same column "ΕΣΟΔΑ", "Πώληση" or "Income" depending on who wrote
    it, and all three mean the row is money coming in.
    """
    key = name_key(_cell(value))
    return _TYPE_ALIASES.get(key) if key else None


_DOC_TYPES = {name_key(d): d for d in finance.DOC_TYPES}
for _spelling, _doc in {
    "Τιμολόγιο": finance.DOC_SALES_INVOICE,
    "Τιμολόγιο Παροχής Υπηρεσιών": finance.DOC_SALES_INVOICE,
    "ΤΠΥ": finance.DOC_SALES_INVOICE,
    "Invoice": finance.DOC_SALES_INVOICE,
    "Απόδειξη": finance.DOC_SERVICE_RECEIPT,
    "Απόδειξη Παροχής Υπηρεσιών": finance.DOC_SERVICE_RECEIPT,
    "Receipt": finance.DOC_SERVICE_RECEIPT,
    "Πιστωτικό Τιμολόγιο": finance.DOC_CREDIT_NOTE,
    "Credit Note": finance.DOC_CREDIT_NOTE,
    "Δαπάνη": finance.DOC_EXPENSE,
    "Έξοδο": finance.DOC_EXPENSE,
    "Expense": finance.DOC_EXPENSE,
    "Λειτουργικό": finance.DOC_OPERATING_EXPENSE,
    "Operating Expense": finance.DOC_OPERATING_EXPENSE,
}.items():
    _DOC_TYPES.setdefault(name_key(_spelling), _doc)


def doc_type(value):
    """A finance.DOC_TYPES label, or None when the cell names something this
    book has no equivalent for."""
    key = name_key(_cell(value))
    return _DOC_TYPES.get(key) if key else None


def vat_rate(value):
    """A VAT rate as a decimal fraction, or None.

    Accepts every way the same rate is written: 24, "24%", 0.24 and a
    percent-formatted spreadsheet cell (which openpyxl already hands back as
    0.24). Anything above 1 is read as a percentage — no Greek VAT rate is
    100 %, so the rule cannot misfire on a legitimate figure.
    """
    parsed = number(value)
    if parsed is None:
        return None
    if parsed > 1:
        parsed = parsed / 100
    return round(parsed, 4)


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------
def _missing_columns(kind, required):
    return ImportFileError(
        "Δεν βρέθηκαν οι απαιτούμενες στήλες στο αρχείο. Απαιτούνται: "
        f"{', '.join(required)}. Κατεβάστε το πρότυπο αρχείο CSV για τη σωστή "
        f"μορφή ({kind})."
    )


def parse_clients(data):
    """Read a client list. Returns a ParsedFile of {name, afm, contact, notes}.

    Only the name is structurally required — an ΑΦΜ is genuinely absent for
    private individuals, and refusing those rows would reject exactly the
    clients a small book has most of.

    Phone and email are joined into the single `contact` field the Client table
    already has, in that order. Deliberately not two new columns: the drawer,
    the statement letterhead and the export all read `contact` today, and
    splitting it is a change to the client editor rather than to this importer.
    """
    header, body = _table(data)
    columns = _index(header, _CLIENT_COLUMNS)
    if "name" not in columns:
        raise _missing_columns("πελάτες", ("Επωνυμία", "Α.Φ.Μ.", "Τηλέφωνο",
                                           "Email"))

    result = ParsedFile()
    for offset, raw in enumerate(body):
        # +2: spreadsheets are 1-indexed and row 1 is the header, so this is the
        # number the user sees in Excel's own gutter.
        line = offset + 2
        if len(result.rows) + len(result.errors) >= MAX_ROWS:
            raise ImportFileError(
                f"Το αρχείο έχει περισσότερες από {MAX_ROWS} γραμμές. "
                "Χωρίστε το σε μικρότερα αρχεία.")

        def cell(name):
            position = columns.get(name)
            return _cell(raw[position]) if position is not None and position < len(raw) else ""

        name = cell("name")
        afm = cell("afm")
        phone = cell("phone")
        email = cell("email")
        notes = cell("notes")
        if not any((name, afm, phone, email, notes)):
            continue           # a blank separator row, not an error
        result.total += 1

        if not name:
            result.errors.append(_issue(line, "Λείπει η επωνυμία του πελάτη."))
            continue
        if len(name) > 200:
            result.errors.append(
                _issue(line, "Η επωνυμία ξεπερνά τους 200 χαρακτήρες."))
            continue
        if len(afm) > 32:
            result.errors.append(
                _issue(line, f"Μη έγκυρο Α.Φ.Μ. «{afm[:40]}»."))
            continue
        if afm and not afm_key(afm):
            result.errors.append(
                _issue(line, f"Μη έγκυρο Α.Φ.Μ. «{afm}»."))
            continue
        # A malformed address is reported but NOT dropped: it is far more often
        # a typo worth keeping and correcting than a value worth losing.
        if email and not _EMAIL.match(email):
            result.warnings.append(
                _issue(line, f"Το email «{email}» δεν φαίνεται έγκυρο — "
                             "αποθηκεύτηκε ως έχει."))

        contact = " · ".join(part for part in (phone, email) if part)[:200]
        result.rows.append({
            "row": line,
            "name": name,
            "afm": afm or None,
            "contact": contact or None,
            "notes": notes[:2000] or None,
        })
    return result


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------
def parse_transactions(data):
    """Read historic transactions. Returns a ParsedFile of rows ready to book.

    Each row carries the POSITIVE magnitude plus its Type; the sign convention
    and the VAT orientation are applied once, later, by finance.book_amounts —
    the same function the transaction form goes through. A file that writes its
    expenses as negatives therefore imports identically to one that does not,
    because the direction is taken from Είδος Κίνησης and the amount's own sign
    is only a fallback.
    """
    header, body = _table(data)
    columns = _index(header, _TXN_COLUMNS)
    if "date" not in columns:
        raise _missing_columns("κινήσεις", ("Ημερομηνία", "Πελάτης",
                                            "Είδος Κίνησης", "Συνολικό Ποσό"))
    if "client" not in columns and "afm" not in columns:
        raise _missing_columns("κινήσεις", ("Ημερομηνία", "Πελάτης",
                                            "Είδος Κίνησης", "Συνολικό Ποσό"))
    if "amount" not in columns and "net_amount" not in columns:
        raise _missing_columns("κινήσεις", ("Ημερομηνία", "Πελάτης",
                                            "Είδος Κίνησης", "Συνολικό Ποσό"))

    result = ParsedFile()
    for offset, raw in enumerate(body):
        line = offset + 2
        if len(result.rows) + len(result.errors) >= MAX_ROWS:
            raise ImportFileError(
                f"Το αρχείο έχει περισσότερες από {MAX_ROWS} γραμμές. "
                "Χωρίστε το σε μικρότερα αρχεία.")

        def value(name):
            position = columns.get(name)
            return raw[position] if position is not None and position < len(raw) else None

        def cell(name):
            return _cell(value(name))

        if not any(_cell(v) for v in raw):
            continue
        result.total += 1

        when = date(value("date"))
        if when is None:
            result.errors.append(_issue(
                line,
                f"Μη έγκυρη ημερομηνία «{cell('date')}»." if cell("date")
                else "Λείπει η ημερομηνία."))
            continue

        client = cell("client")
        afm = cell("afm")
        if not client and not afm:
            result.errors.append(
                _issue(line, "Λείπει ο πελάτης (επωνυμία ή Α.Φ.Μ.)."))
            continue

        gross = number(value("amount"))
        net = number(value("net_amount"))
        # Gross wins when both are present: it is the figure the row is stored
        # as, so taking it directly avoids a needless net→gross round trip that
        # could land a cent away from what the document says.
        basis = "gross" if gross is not None else "net"
        amount = gross if gross is not None else net
        if amount is None:
            result.errors.append(_issue(line, "Λείπει το ποσό."))
            continue
        if round(abs(amount), 2) == 0:
            result.errors.append(_issue(line, "Το ποσό είναι μηδενικό."))
            continue

        kind = txn_type(value("type"))
        if kind is None:
            # No usable Type: fall back to the amount's own sign, which is how
            # a great many legacy exports encode direction in the first place.
            kind = "Έξοδο" if amount < 0 else "Έσοδο"
            written = cell("type")
            result.warnings.append(_issue(
                line,
                f"Άγνωστο είδος κίνησης «{written}» — καταχωρήθηκε ως {kind}."
                if written else
                f"Δεν δόθηκε είδος κίνησης — καταχωρήθηκε ως {kind}."))

        document = None
        written_doc = cell("doc_type")
        if written_doc:
            document = doc_type(written_doc)
            if document is None:
                result.warnings.append(_issue(
                    line, f"Άγνωστος τύπος παραστατικού «{written_doc}» — "
                          "η κίνηση καταχωρήθηκε χωρίς αυτόν."))
        if document and finance.is_credit_note(document) and kind == finance.DEBT_TYPE:
            result.errors.append(_issue(
                line, "Το πιστωτικό δεν μπορεί να καταχωρηθεί ως χρεωστούμενο."))
            continue

        rate = vat_rate(value("vat_rate"))
        if rate is not None and not 0 <= rate <= 1:
            result.errors.append(
                _issue(line, f"Μη έγκυρος συντελεστής Φ.Π.Α. «{cell('vat_rate')}»."))
            continue
        if rate is None:
            # The statutory standard rate, exactly as the transaction form
            # defaults it. Stated in the template so an importer with no VAT
            # column knows what it is agreeing to.
            rate = finance.DEFAULT_VAT_RATE
        vat = number(value("vat_amount"))

        due = date(value("due_date")) if kind == finance.DEBT_TYPE else None

        result.rows.append({
            "row": line,
            "date": when,
            "client": client,
            "afm": afm or None,
            "type": kind,
            "doc_type": document,
            "doc_number": cell("doc_number")[:64] or None,
            # Magnitude only — see the docstring.
            "amount": round(abs(amount), 2),
            "basis": basis,
            "vat_rate": rate,
            "vat_amount": abs(vat) if vat is not None else None,
            "description": cell("description")[:500] or None,
            "due_date": due,
        })
    return result


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------
# Written in the SAME dialect the export module produces — UTF-8 with a BOM,
# semicolon-delimited, decimal commas — so a template downloaded, filled in and
# re-uploaded survives a round trip through a Greek Excel unaltered. See
# server/exports.py for why each of those three is required.
_BOM = "﻿"

_CLIENT_TEMPLATE_COLUMNS = ("Επωνυμία", "Α.Φ.Μ.", "Τηλέφωνο", "Email",
                            "Σημειώσεις")
_CLIENT_TEMPLATE_ROWS = (
    ("Νησίδα Café", "123456789", "2101234567", "info@nisida.gr",
     "Μηνιαία τιμολόγηση"),
    ("Παπαδόπουλος Α.Ε.", "987654321", "6941234567", "logistirio@papadopoulos.gr",
     ""),
)

_TXN_TEMPLATE_COLUMNS = ("Ημερομηνία", "Πελάτης", "Α.Φ.Μ.", "Είδος Κίνησης",
                         "Τύπος Παραστατικού", "Αρ. Παραστατικού",
                         "Συνολικό Ποσό", "Συντ. Φ.Π.Α.", "Φ.Π.Α.",
                         "Περιγραφή", "Ημερομηνία Λήξης")
_TXN_TEMPLATE_ROWS = (
    ("2026-01-15", "Νησίδα Café", "123456789", "Έσοδο", "Τιμολόγιο Πώλησης",
     "ΤΠΥ-1042", "1240,00", "24%", "240,00", "Υπηρεσίες Ιανουαρίου", ""),
    ("2026-01-20", "Παπαδόπουλος Α.Ε.", "987654321", "Έξοδο", "Δαπάνη/Έξοδο",
     "ΤΔΑ-88", "310,00", "24%", "60,00", "Προμήθεια υλικών", ""),
    ("2026-02-01", "Νησίδα Café", "123456789", "Χρεωστούμενο",
     "Τιμολόγιο Πώλησης", "ΤΠΥ-1051", "500,00", "24%", "",
     "Ανοιχτό υπόλοιπο", "2026-03-03"),
)

TEMPLATES = {
    CLIENTS: (_CLIENT_TEMPLATE_COLUMNS, _CLIENT_TEMPLATE_ROWS,
              "protypo-pelaton.csv"),
    TRANSACTIONS: (_TXN_TEMPLATE_COLUMNS, _TXN_TEMPLATE_ROWS,
                   "protypo-kiniseon.csv"),
}


def template(kind):
    """(csv text, ASCII filename) for one of the two templates.

    The example rows are part of the point: a header alone leaves the user
    guessing what "Είδος Κίνησης" accepts and how a date should look, and the
    third transaction row exists specifically to show a Χρεωστούμενο with its
    due date and no VAT figure.

    The filename is ASCII for the reason exports.filename gives — a Greek name
    in Content-Disposition needs RFC 5987 encoding that some Excel installs
    still mangle into something unopenable.
    """
    if kind not in TEMPLATES:
        raise ImportFileError(f"Άγνωστο πρότυπο «{kind}».", status=404)
    columns, rows, name = TEMPLATES[kind]
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quotechar='"',
                        quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(row)
    return _BOM + buffer.getvalue(), name
