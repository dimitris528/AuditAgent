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

# Half a cent — the tolerance euro figures are compared with, so a VAT of
# 240.001 against a gross of 240.00 does not read as an impossible row. Same
# value store._CENT uses, for the same reason.
_CENT = 0.005


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


def _aliases(primary, fallback=None):
    """{header key: (field, rank)} from {field: (heading, ...)} tables.

    Two ranks, because some headings are only meaningful in the absence of a
    better one. "Αξία" is the case that forced this: in Greek bookkeeping it
    usually means the NET value, but plenty of exports use it for the total, so
    it is a last-resort match for the gross — a file carrying both "Αξία" and
    "Σύνολο" must read the total from "Σύνολο" whichever comes first.

    Before this existed _index took the first column that matched positionally,
    so exactly that file read the net figure as the gross and understated every
    row by its VAT.
    """
    out = {}
    for rank, spec in enumerate((primary, fallback or {})):
        for field_name, headings in spec.items():
            for heading in headings:
                out.setdefault(_head(heading), (field_name, rank))
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
    # Payment state. NOT the same thing as Είδος Κίνησης and cannot replace it
    # — "Εξοφλημένο" does not say whether money came in or went out — but
    # "Εκκρεμεί" does say the row is a Χρεωστούμενο, which is the half a
    # legacy export most often carries instead of a type column.
    "status": ("Κατάσταση", "Κατάσταση Πληρωμής", "Status", "Payment Status",
               "Παρακολούθηση"),
    "doc_type": ("Τύπος Παραστατικού", "Παραστατικό", "Είδος Παραστατικού",
                 "Document Type", "Doc Type"),
    "doc_number": ("Αρ. Παραστατικού", "Αριθμός Παραστατικού", "Αρ. Τιμολογίου",
                   "Doc Number", "Document Number", "Invoice Number",
                   "Invoice No"),
    # The GROSS (VAT-inclusive) total — what the row is stored as.
    "amount": ("Συνολικό Ποσό", "Σύνολο", "Συνολική Αξία", "Ποσό", "Μεικτό",
               "Μικτό", "Μεικτή Αξία", "Μικτή Αξία", "Τελικό Ποσό",
               "Πληρωτέο", "Πληρωτέο Ποσό", "Total", "Total Amount", "Gross",
               "Gross Amount", "Grand Total", "Amount"),
    "net_amount": ("Καθαρή Αξία", "Καθαρό Ποσό", "Καθαρό", "Καθαρή", "Καθαρά",
                   "Αξία Χωρίς Φ.Π.Α.", "Χωρίς Φ.Π.Α.", "Προ Φ.Π.Α.",
                   "Πριν Φ.Π.Α.", "Net", "Net Amount", "Net Value",
                   "Subtotal", "Sub Total"),
    # Listed BEFORE vat_amount on purpose. _aliases keeps the first field a
    # heading is claimed by, and the two are told apart only by _head's `pct`
    # suffix — "Φ.Π.Α." is euros, "Φ.Π.Α. %" is a rate.
    "vat_rate": ("Συντ. Φ.Π.Α.", "Συντελεστής Φ.Π.Α.", "Συντελεστής",
                 "Φ.Π.Α. %", "ΦΠΑ%", "VAT Rate", "VAT %", "Rate"),
    "vat_amount": ("Φ.Π.Α.", "ΦΠΑ", "Ποσό Φ.Π.Α.", "Αξία Φ.Π.Α.", "Φόρος",
                   "VAT", "VAT Amount", "Tax", "Tax Amount"),
    "description": ("Περιγραφή", "Αιτιολογία", "Σχόλια", "Description",
                    "Notes", "Memo"),
    "due_date": ("Ημερομηνία Λήξης", "Λήξη", "Προθεσμία", "Due Date",
                 "Payment Due"),
}, {
    # Last resort only — see _aliases. A bare "Αξία" is genuinely ambiguous
    # between the net and the total, so it fills the gross when the file offers
    # nothing better and yields to any explicit total column when it does.
    "amount": ("Αξία", "Value"),
})


def _index(headers, aliases):
    """{field: column index} for the headings this file actually has.

    A better-ranked heading wins wherever it sits in the row; among equals the
    leftmost wins, so a sheet with two "Ποσό" columns uses the first rather
    than silently preferring whichever came last.
    """
    best = {}
    for position, heading in enumerate(headers):
        match = aliases.get(_head(heading))
        if match is None:
            continue
        field_name, rank = match
        current = best.get(field_name)
        if current is None or rank < current[0]:
            best[field_name] = (rank, position)
    return {field_name: position for field_name, (_, position) in best.items()}


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
    return [[row for row in reader]]


# How many worksheets are considered, and how far down each one the header is
# looked for. Both are bounds on work rather than judgements about real files:
# the header is in the first handful of rows or it is not a header, and a
# workbook with a dozen tabs has already made its point.
MAX_SHEETS = 12
_HEADER_SEARCH_ROWS = 12


def _read_xlsx(data):
    """Every worksheet as a list of row lists.

    ALL of them, not just the first: an export routinely leads with a cover or
    parameters tab, and reading sheet 0 unconditionally means refusing a
    perfectly good workbook because page one says "Έκθεση".
    """
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
        if not book.worksheets:
            raise ImportFileError("Το αρχείο Excel δεν περιέχει φύλλα εργασίας.")
        sheets = []
        for sheet in book.worksheets[:MAX_SHEETS]:
            rows = []
            for values in sheet.iter_rows(values_only=True):
                rows.append(list(values))
                # Capped one row past the limit so "too many rows" is still
                # detectable without materialising a runaway sheet.
                if len(rows) > MAX_ROWS + 1:
                    break
            sheets.append(rows)
        return sheets
    finally:
        book.close()


def _score_header(rows, aliases):
    """(index of the likeliest header row, how many fields it matched).

    The header is the row that RECOGNISES the most columns, not the first row
    with anything in it. Real exports lead with a title, a date stamp and a
    blank line, and taking the first non-empty row means scoring "ΚΙΝΗΣΕΙΣ
    2026" as the header — zero columns matched, and a file refused for having
    a title.

    Ties go to the earliest row, so a data row that happens to echo a heading
    cannot outrank the heading above it.
    """
    best_at, best_score = None, 0
    for index, row in enumerate(rows[:_HEADER_SEARCH_ROWS]):
        if not any(_cell(value) for value in row):
            continue
        score = len(_index(row, aliases))
        if score > best_score:
            best_at, best_score = index, score
    return best_at, best_score


def _table(data, aliases):
    """(header row, data rows) from CSV or XLSX bytes.

    Picks the sheet AND the header row by how many known columns each one
    recognises, so a workbook whose data sits behind a cover tab, or under a
    title and a blank line, reads the same as a clean one.

    Falls back to the first non-empty row of the first sheet when nothing
    matches anywhere — that path exists so the caller still raises its own
    "these columns are required" error, which names what is missing, rather
    than a vaguer one from here.
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
    sheets = _read_xlsx(data) if data.startswith(_XLSX_MAGIC) else _read_csv(data)

    best = None
    for rows in sheets:
        at, score = _score_header(rows, aliases)
        if at is not None and (best is None or score > best[0]):
            best = (score, rows, at)
    if best is not None:
        _score, rows, at = best
        return rows[at], rows[at + 1:]

    for rows in sheets:
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
# A plausible leading thousands group: one to three digits. "1" in "1.000",
# "12" in "12.500". Four or more digits before the separator is not a thousands
# group, so "1234.567" keeps its decimal point.
_THOUSANDS_LEAD = re.compile(r"^[1-9]\d{0,2}$")

# Currency marks, written before the figure as often as after it, and in words
# as often as in symbols.
_CURRENCY = re.compile(r"€|\$|£|¥|EUR|USD|GBP|ΕΥΡΩ|ΔΡΧ", re.IGNORECASE)

# Every space-like character a spreadsheet puts inside a number. The plain one
# is the least of them: Excel's thousands separator in several locales is a
# NO-BREAK SPACE (U+00A0), and a NARROW NO-BREAK SPACE (U+202F) is what a
# French or Swiss export writes. All three are invisible, none of them is
# " ", and a value that still contains one parses as nothing at all.
_ANY_SPACE = re.compile(r"[\s    ⁠]")

# Unicode dashes that mean "minus". U+2212 is what a spreadsheet writes when
# it formats a negative properly, and it is not the ASCII hyphen that every
# parser looks for.
_MINUS_SIGNS = {ord(ch): "-" for ch in "−‒–—―"}


def number(value):
    """A euro figure from a cell, or None when there is nothing to read.

    Both decimal conventions are accepted and resolved by POSITION rather than
    by locale, because one exported file routinely contains both:

        "1.234,56" and "1,234.56"  → 1234.56   (last separator is the decimal)
        "1.234.567"                → 1234567.0 (repeated ⇒ thousands)
        "1234,56"                  → 1234.56   (lone comma ⇒ decimal)

    A LONE separator followed by exactly three digits is a THOUSANDS separator,
    not a decimal point:

        "1.000" → 1000.0      "1.240" → 1240.0      "1,000" → 1000.0

    This is the rule that matters most in practice and the one this function
    originally got wrong: a Greek export writes a round thousand as "1.240",
    and reading that as €1.24 understates the row by a factor of a thousand
    while raising no error at all — the amount parses, it is simply wrong. Euro
    amounts carry two decimal places, so three digits after the separator is
    not a fraction. The lead has to look like a thousands group for the rule to
    fire, which is what keeps "0,240" (a VAT rate, and "1234.567" (four leading
    digits) reading as decimals.

    The residual ambiguity is real and decided deliberately: "1.500" is read as
    €1500, not €1.50. In a Greek book the former is overwhelmingly what is
    meant, and €1.50 is written "1,50".

    Everything that is not a digit or a separator is stripped first: currency
    symbols and codes (€, $, EUR, USD), every space-like character including
    the INVISIBLE ones a spreadsheet uses as a thousands separator (U+00A0,
    U+202F), and the Unicode minus signs that are not the ASCII hyphen every
    parser looks for.

    Negatives are written three ways and all three are read: a leading sign, a
    TRAILING one ("310,00-", which is what a great many accounting packages
    export), and parentheses ("(310,00)").
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None
    text = text.translate(_MINUS_SIGNS)
    text = _CURRENCY.sub("", text)
    text = _ANY_SPACE.sub("", text)
    negative = ((text.startswith("(") and text.endswith(")"))
                or text.startswith("-") or text.endswith("-"))
    cleaned = re.sub(r"[^0-9,.]", "", text)
    if not cleaned:
        return None

    if "." in cleaned and "," in cleaned:
        decimal = "." if cleaned.rfind(".") > cleaned.rfind(",") else ","
        cleaned = cleaned.replace("," if decimal == "." else ".", "")
    elif cleaned.count(",") > 1 or cleaned.count(".") > 1:
        # Repeated separators can only be thousands markers.
        cleaned = cleaned.replace(",", "").replace(".", "")
        decimal = "."
    elif "," in cleaned or "." in cleaned:
        separator = "," if "," in cleaned else "."
        lead, _, group = cleaned.partition(separator)
        if len(group) == 3 and _THOUSANDS_LEAD.match(lead):
            cleaned = lead + group          # thousands — see the docstring
            decimal = "."
        else:
            decimal = separator
    else:
        decimal = "."
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


# Payment states, in the two directions that carry information. Only the
# OUTSTANDING half can decide a Type on its own: an unpaid row is a
# Χρεωστούμενο, while a settled one says nothing about whether the money came
# in or went out, so it defers to the amount's sign.
_STATUS_OUTSTANDING = frozenset(name_key(word) for word in (
    "Εκκρεμεί", "Εκκρεμές", "Ανεξόφλητο", "Ανεξόφλητα", "Απλήρωτο", "Οφειλή",
    "Σε εκκρεμότητα", "Ληξιπρόθεσμο", "Unpaid", "Pending", "Open",
    "Outstanding", "Due", "Overdue",
))
_STATUS_SETTLED = frozenset(name_key(word) for word in (
    "Εξοφλημένο", "Εξοφλήθηκε", "Πληρωμένο", "Πληρώθηκε", "Τακτοποιημένο",
    "Paid", "Settled", "Closed", "Complete", "Completed",
))


def payment_status(value):
    """"outstanding", "settled", or None for a cell that says neither."""
    key = name_key(_cell(value))
    if not key:
        return None
    if key in _STATUS_OUTSTANDING:
        return "outstanding"
    if key in _STATUS_SETTLED:
        return "settled"
    return None


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


# A rate derived from two euro figures never lands exactly on 0.24; snap it to
# the statutory rate it is nearest, and only when it is genuinely close. Same
# treatment server/ocr.py gives a rate inferred from a scanned invoice, and for
# the same reason: the stored rate should say "24 %", not "23.9908 %".
_RATE_TOLERANCE = 0.005


def _snap_rate(rate):
    if rate is None or not 0 <= rate <= 1:
        return None
    nearest = min(finance.VAT_RATES, key=lambda r: abs(r - rate))
    return nearest if abs(nearest - rate) <= _RATE_TOLERANCE else round(rate, 4)


# How many data rows the positional fallback inspects before deciding a column
# holds money. Enough to be sure, few enough that a 5000-row file does not pay
# for the guess twice over.
_SNIFF_ROWS = 25

# An identifier typed as a NUMBER is the thing most likely to be mistaken for
# money by anything that just looks for numeric values — a Greek ΑΦΜ is nine
# digits, a phone number ten. Both are whole numbers with no cents, and a
# transaction of ten million euro is not the case this product optimises for,
# so "eight or more digits and no fractional part" separates the two cleanly.
_IDENTIFIER_DIGITS = 8

# Headings that say "this column is an identifier" even when the alias table
# did not match them exactly — "ΑΦΜ κωδικός" is nobody's total. Listed as word
# PREFIXES so Greek inflection ("κωδικός", "κωδικοί") is covered without an
# entry each.
#
# Put through name_key here rather than trusted as typed, so both sides of the
# comparison are normalised identically. Written literally, "έτος" would never
# match anything: casefold maps the final sigma ς onto σ, so the heading
# normalises to "ετοσ" while the token stayed "ετος" — the exact trap
# server/text.py documents, and invisible on inspection.
_IDENTIFIER_WORDS = tuple(name_key(word) for word in (
    "αφμ", "afm", "τιν", "tin", "vatnumber", "κωδικ", "code", "τηλ", "phone",
    "mobile", "αριθμ", "number", "έτος", "year", "ποσοστ", "percent",
))


def _looks_like_identifier(values):
    return bool(values) and all(
        float(v).is_integer() and len(str(int(abs(v)))) >= _IDENTIFIER_DIGITS
        for v in values)


def _named_like_identifier(heading):
    """True when a heading names an identifier rather than an amount.

    Matched word by word, NOT as a substring of the whole heading. The
    substring form is wrong in a way that is easy to miss and hard to explain
    afterwards: "τηλ" (phone) sits inside "στήλη" (column), so a column headed
    "Στήλη Δ" was classified as a phone number and the fallback then refused
    the only money column in the file.
    """
    words = name_key(heading).split()
    return any(word.startswith(token)
               for word in words for token in _IDENTIFIER_WORDS)


def find_amount_column(header, body, taken):
    """(index, heading) of the column that most likely holds the total, or None.

    The last resort, used only when NO amount, net or VAT column could be
    identified by name. Everything past that point is a guess, so the guess is
    made as narrow as the evidence allows and is always reported to the user
    (parse_transactions raises a warning naming the column it chose).

    A column qualifies when every non-empty value in the sample parses as a
    number. Columns already claimed by another field are excluded — the date
    and the ΑΦΜ would otherwise be the first two numeric columns in a typical
    sheet — and anything that still looks like an identifier is skipped, BY
    HEADING and by shape. Without those two guards a column headed "ΑΦΜ
    κωδικός", which the alias table does not match, reads as a €94 million
    transaction: wrong, and wrong in a way the warning does not undo.

    The LEFTMOST survivor wins: spreadsheets put identity on the left and money
    on the right, so the first numeric column after the identifying ones is the
    total far more often than not.
    """
    width = max((len(row) for row in body[:_SNIFF_ROWS]), default=0)
    width = max(width, len(header))
    for position in range(width):
        if position in taken:
            continue
        heading = _cell(header[position]) if position < len(header) else ""
        if _named_like_identifier(heading):
            continue
        values = []
        for row in body[:_SNIFF_ROWS]:
            raw = row[position] if position < len(row) else None
            if not _cell(raw):
                continue
            parsed = number(raw)
            if parsed is None:
                values = []
                break
            values.append(parsed)
        # Every non-empty cell had to parse, at least one had to be non-zero,
        # and a column of long whole numbers is an identifier, not money.
        if (not values or all(v == 0 for v in values)
                or _looks_like_identifier(values)):
            continue
        return position, heading
    return None


def resolve_amounts(gross, net, vat, rate):
    """Fill in whatever the row did not state. Returns (gross, net, vat, rate).

    A real export gives some two of the three euro figures and often no rate at
    all, so each is derived from the others rather than demanded:

        net + VAT           → gross           gross − VAT → net
        gross − net         → VAT             VAT / net   → rate

    The RATE is derived before it is defaulted, and that ordering is the point:
    a 13 % invoice stating €500 net and €65 VAT would otherwise be stored at
    the standard rate, so the row would reconcile to the cent while describing
    itself wrongly — and every later recalculation from that rate would be off.
    The 24 % default (finance.DEFAULT_VAT_RATE) is the last resort, for a row
    that states one figure and nothing else.

    Every argument is a POSITIVE magnitude; direction belongs to the Type. All
    four may be None, and a row that yields no euro figure at all is the
    caller's error to report.
    """
    if gross is None and net is not None and vat is not None:
        gross = round(net + vat, 2)
    if net is None and gross is not None and vat is not None:
        net = round(gross - vat, 2)
    if vat is None and gross is not None and net is not None:
        vat = round(gross - net, 2)

    if rate is None and vat is not None:
        # From the net where there is one — it is the base VAT is charged on,
        # so the division is exact rather than a rearrangement.
        if net:
            rate = _snap_rate(vat / net)
        elif gross and gross != vat:
            rate = _snap_rate(vat / (gross - vat))
    if rate is None:
        # Only now, with no rate stated and none derivable, is the standard
        # rate assumed — and the missing euro figures follow from it.
        rate = finance.DEFAULT_VAT_RATE
        if vat is None and gross is None and net is not None:
            gross = finance.gross_from_net(net, rate)
        if vat is None and gross is not None:
            vat = abs(finance.vat_of(gross, rate))

    # A second fill, because the derivations above can supply the figure an
    # earlier one was missing: a row stating only its gross has no net until
    # the VAT has been worked out from the rate.
    if net is None and gross is not None and vat is not None:
        net = round(gross - vat, 2)
    if gross is None and net is not None and vat is not None:
        gross = round(net + vat, 2)
    return gross, net, vat, rate


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# The mapping contract
# --------------------------------------------------------------------------
# What the mapping screen offers, in the order it offers it. Defined HERE
# rather than in the modal so the labels a user maps against and the fields the
# parser reads cannot drift: the UI renders this list, posts back the field
# keys in it, and every key is one this module actually consumes.
#
# `group` marks an either/or. A transaction needs SOME way to name its client
# and SOME figure to book, but either column of each pair will do — demanding
# both would reject a file that carries an ΑΦΜ and no name, which is common,
# or a net value and no total, which is commoner still.
FIELDS = {
    CLIENTS: (
        {"key": "name", "label": "Επωνυμία", "required": True,
         "hint": "Η επωνυμία του πελάτη"},
        {"key": "afm", "label": "Α.Φ.Μ.", "required": False,
         "hint": "Ο αριθμός φορολογικού μητρώου"},
        {"key": "phone", "label": "Τηλέφωνο", "required": False,
         "hint": "Αποθηκεύεται στο πεδίο επικοινωνίας"},
        {"key": "email", "label": "Email", "required": False,
         "hint": "Αποθηκεύεται στο πεδίο επικοινωνίας"},
        {"key": "notes", "label": "Σημειώσεις", "required": False, "hint": ""},
    ),
    TRANSACTIONS: (
        {"key": "date", "label": "Ημερομηνία", "required": True,
         "hint": "ΗΗ/ΜΜ/ΕΕΕΕ ή ΕΕΕΕ-ΜΜ-ΗΗ"},
        {"key": "client", "label": "Όνομα / Επωνυμία", "required": False,
         "group": "client", "hint": "Ο πελάτης της κίνησης"},
        {"key": "afm", "label": "Α.Φ.Μ.", "required": False,
         "group": "client", "hint": "Εναλλακτικά του ονόματος"},
        {"key": "amount", "label": "Συνολικό Ποσό / Μεικτό", "required": False,
         "group": "amount", "hint": "Με Φ.Π.Α."},
        {"key": "net_amount", "label": "Καθαρό Ποσό", "required": False,
         "group": "amount", "hint": "Χωρίς Φ.Π.Α."},
        {"key": "vat_amount", "label": "Φ.Π.Α.", "required": False,
         "hint": "Υπολογίζεται αν λείπει"},
        {"key": "vat_rate", "label": "Συντ. Φ.Π.Α.", "required": False,
         "hint": "π.χ. 24% — προεπιλογή 24%"},
        {"key": "type", "label": "Είδος Κίνησης", "required": False,
         "hint": "Έσοδο / Έξοδο / Χρεωστούμενο"},
        {"key": "status", "label": "Κατάσταση", "required": False,
         "hint": "Εκκρεμεί / Εξοφλημένο"},
        {"key": "doc_type", "label": "Τύπος Παραστατικού", "required": False,
         "hint": ""},
        {"key": "doc_number", "label": "Αρ. Παραστατικού", "required": False,
         "hint": "Χρησιμοποιείται για τον έλεγχο διπλοεγγραφών"},
        {"key": "description", "label": "Περιγραφή", "required": False,
         "hint": ""},
        {"key": "due_date", "label": "Ημ. Λήξης", "required": False,
         "hint": "Μόνο για χρεωστούμενα"},
    ),
}

#: Data rows returned with an analysis, so the mapping screen can show what a
#: column actually CONTAINS. Three, because one is not enough to tell a date
#: column from a column that happens to start with a date.
SAMPLE_ROWS = 3


def _field_keys(kind):
    return {field["key"] for field in FIELDS[kind]}


def apply_mapping(mapping, kind, width):
    """A caller's {field: column index} as a validated column index.

    Unknown field names and out-of-range indices are DROPPED rather than
    rejected: the mapping comes from a form the user filled in against a file
    they have since possibly changed, and losing one dropdown is a better
    outcome than refusing the upload. What cannot be dropped — a required
    field with nothing mapped to it — is caught by the caller, which can say
    which field it was.
    """
    known = _field_keys(kind)
    columns = {}
    for field, position in (mapping or {}).items():
        if field not in known or position is None or position == "":
            continue
        try:
            index = int(position)
        except (TypeError, ValueError):
            continue
        if 0 <= index < width:
            columns[field] = index
    return columns


def analyze(data, kind):
    """What the mapping screen needs: the file's headers, a few real rows, and
    the mapping this module would have chosen on its own.

    Writes nothing and decides nothing. The auto-detection is offered as a
    STARTING POINT — every guess it makes is pre-selected and every one of them
    is overridable, which is the whole point of the step: a file whose columns
    this module cannot recognise is no longer a file it cannot import.

    The header row is chosen exactly as the import will choose it, so the
    indices returned here address the same columns the import will read.
    """
    if kind not in KINDS:
        raise ImportFileError(f"Άγνωστος τύπος εισαγωγής «{kind}».", status=404)
    aliases = _CLIENT_COLUMNS if kind == CLIENTS else _TXN_COLUMNS
    header, body = _table(data, aliases)
    detected = _index(header, aliases)

    if kind == TRANSACTIONS and not {"amount", "net_amount", "vat_amount"} & set(detected):
        # Offer the positional guess too — pre-selected, and visible in a
        # dropdown the user can correct, which is a far safer place for it
        # than silently inside an import.
        guess = find_amount_column(header, body, set(detected.values()))
        if guess is not None:
            detected["amount"] = guess[0]

    rows = [row for row in body if any(_cell(value) for value in row)]
    width = max([len(header)] + [len(row) for row in rows[:SAMPLE_ROWS]])
    return {
        "kind": kind,
        # Blank headings still need a name in the dropdown, or the user is
        # choosing between several identical empty options.
        "headers": [
            _cell(header[i]) if i < len(header) and _cell(header[i])
            else f"Στήλη {i + 1}"
            for i in range(width)
        ],
        "sample": [
            [_cell(row[i]) if i < len(row) else "" for i in range(width)]
            for row in rows[:SAMPLE_ROWS]
        ],
        "mapping": detected,
        "fields": list(FIELDS[kind]),
        "rows": len(rows),
    }


def _missing_columns(kind, required):
    return ImportFileError(
        "Δεν βρέθηκαν οι απαιτούμενες στήλες στο αρχείο. Απαιτούνται: "
        f"{', '.join(required)}. Κατεβάστε το πρότυπο αρχείο CSV για τη σωστή "
        f"μορφή ({kind})."
    )


def parse_clients(data, mapping=None):
    """Read a client list. Returns a ParsedFile of {name, afm, contact, notes}.

    `mapping` is the user's own {field: column index} from the mapping screen.
    When given it is AUTHORITATIVE and detection is skipped entirely — the
    point of that screen is to override what this module guessed, so quietly
    re-adding a guess for a field the user left blank would defeat it.

    Only the name is structurally required — an ΑΦΜ is genuinely absent for
    private individuals, and refusing those rows would reject exactly the
    clients a small book has most of.

    Phone and email are joined into the single `contact` field the Client table
    already has, in that order. Deliberately not two new columns: the drawer,
    the statement letterhead and the export all read `contact` today, and
    splitting it is a change to the client editor rather than to this importer.
    """
    header, body = _table(data, _CLIENT_COLUMNS)
    columns = (_index(header, _CLIENT_COLUMNS) if mapping is None
               else apply_mapping(mapping, CLIENTS, len(header)))
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
def parse_transactions(data, mapping=None):
    """Read historic transactions. Returns a ParsedFile of rows ready to book.

    `mapping` is the user's own {field: column index} from the mapping screen,
    and when given it is AUTHORITATIVE — detection and the positional fallback
    are both skipped, because the user has just told us where everything is.

    Each row carries the POSITIVE magnitude plus its Type; the sign convention
    and the VAT orientation are applied once, later, by finance.book_amounts —
    the same function the transaction form goes through. A file that writes its
    expenses as negatives therefore imports identically to one that does not,
    because the direction is taken from Είδος Κίνησης and the amount's own sign
    is only a fallback.
    """
    header, body = _table(data, _TXN_COLUMNS)
    explicit = mapping is not None
    columns = (_index(header, _TXN_COLUMNS) if not explicit
               else apply_mapping(mapping, TRANSACTIONS, len(header)))
    if "date" not in columns:
        raise _missing_columns("κινήσεις", ("Ημερομηνία", "Πελάτης",
                                            "Είδος Κίνησης", "Συνολικό Ποσό"))
    if "client" not in columns and "afm" not in columns:
        raise _missing_columns("κινήσεις", ("Ημερομηνία", "Πελάτης",
                                            "Είδος Κίνησης", "Συνολικό Ποσό"))

    result = ParsedFile()

    # Positional fallback. Only when NONE of the three money columns was
    # recognised by name — a file naming any one of them is telling us where
    # its figures are, and guessing alongside that would be second-guessing
    # the user rather than helping them. Skipped entirely for an explicit
    # mapping, for the same reason twice over.
    if explicit and not {"amount", "net_amount"} & set(columns):
        # Said at FILE level rather than once per row. The mapping screen
        # blocks this already, so reaching here means the mapping was posted
        # against a file that has since changed shape — and "map a total or a
        # net column" is a far more useful answer than four hundred identical
        # "λείπει το ποσό" lines.
        raise _missing_columns("κινήσεις", ("Συνολικό Ποσό", "Καθαρό Ποσό"))

    guessed = None
    if not explicit and not {"amount", "net_amount", "vat_amount"} & set(columns):
        guessed = find_amount_column(header, body, set(columns.values()))
        if guessed is None:
            raise _missing_columns("κινήσεις", ("Ημερομηνία", "Πελάτης",
                                                "Είδος Κίνησης", "Συνολικό Ποσό"))
        position, heading = guessed
        columns["amount"] = position
        # Announced, never silent. Reading an unnamed column as money is a
        # guess about somebody's books, and the one thing worse than guessing
        # wrong is guessing wrong quietly.
        result.warnings.append(_issue(
            1,
            f"Δεν εντοπίστηκαν στήλες ποσών από την επικεφαλίδα — "
            f"χρησιμοποιήθηκε η στήλη «{heading or position + 1}» ως συνολικό "
            "ποσό, με Φ.Π.Α. 24 %. Ελέγξτε τα ποσά."))
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

        stated_gross = number(value("amount"))
        stated_net = number(value("net_amount"))
        stated_vat = number(value("vat_amount"))
        if stated_gross is None and stated_net is None:
            result.errors.append(_issue(line, "Λείπει το ποσό."))
            continue

        # Direction, for a row whose Type is missing, comes from the sign as
        # WRITTEN — captured before the magnitudes are taken, because a legacy
        # export routinely encodes an expense as a negative and nothing else.
        signed_hint = stated_gross if stated_gross is not None else stated_net

        # Everything downstream is a magnitude; the Type carries direction.
        # Taken here so the derivations below cannot mix a signed gross with an
        # unsigned VAT and produce a net larger than the total.
        gross = abs(stated_gross) if stated_gross is not None else None
        net = abs(stated_net) if stated_net is not None else None
        vat = abs(stated_vat) if stated_vat is not None else None

        # Guards on what the FILE said, before anything is derived from it.
        # Both fire on a mis-mapped column — the failure mode that has to be
        # loud, because the arithmetic downstream is perfectly happy to carry a
        # wrong figure all the way into the book without complaining.
        if gross is not None and vat is not None and vat >= gross + _CENT:
            result.errors.append(_issue(
                line, f"Το Φ.Π.Α. ({vat:.2f}) δεν μπορεί να ξεπερνά το "
                      f"συνολικό ποσό ({gross:.2f}). Ελέγξτε τις στήλες."))
            continue
        if gross is not None and net is not None and net > gross + _CENT:
            result.errors.append(_issue(
                line, f"Η καθαρή αξία ({net:.2f}) δεν μπορεί να ξεπερνά το "
                      f"συνολικό ποσό ({gross:.2f}). Ελέγξτε τις στήλες."))
            continue

        kind = txn_type(value("type"))
        if kind is None and payment_status(value("status")) == "outstanding":
            # A file with no Είδος Κίνησης but a Κατάσταση of "Εκκρεμεί" is
            # telling us this row is unpaid, which is exactly what a
            # Χρεωστούμενο is. Not a guess, so no warning. The settled state
            # says nothing about direction and deliberately does not land here.
            kind = finance.DEBT_TYPE
        if kind is None:
            # No usable Type: fall back to the amount's own sign, which is how
            # a great many legacy exports encode direction in the first place.
            kind = "Έξοδο" if (signed_hint or 0) < 0 else "Έσοδο"
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

        # Whatever the row left out is derived from what it gave, and the rate
        # is derived before the standard 24 % is assumed — see resolve_amounts.
        gross, net, vat, rate = resolve_amounts(gross, net, vat, rate)

        # Gross wins whenever it is known, derived or not: it is the figure the
        # row is STORED as, so passing it through avoids a net→gross round trip
        # that could land a cent away from what the document says. A row that
        # gave only a net value keeps the net basis, and book_amounts converts.
        amount, basis = ((gross, "gross") if gross is not None
                         else (net, "net"))
        if amount is None or round(amount, 2) == 0:
            result.errors.append(_issue(line, "Το ποσό είναι μηδενικό."))
            continue

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
            "amount": round(amount, 2),
            "basis": basis,
            "vat_rate": rate,
            "vat_amount": vat,
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
    ("Νησίδα Café", "123456789", "2101234567", "nisida@example.gr",
     "Μηνιαία τιμολόγηση"),
    ("Παπαδόπουλος Α.Ε.", "987654321", "6941234567", "logistirio@example.gr",
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
