"""
What a failure says out loud.

A database error is not an error message. Left alone, SQLAlchemy renders a
not-null violation as the statement, the bound parameters (a dict, complete
with the tenant's own client names) and a link to its documentation — which
tells the user nothing they can act on, leaks the schema and the data into
every screenshot and support email it reaches, and reads as a crash rather
than as "check the amounts column".

These assert the two halves of the guarantee: the right sentence comes out,
and NOTHING of the exception does.
"""

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from server import errors, store


def _integrity(message):
    """An IntegrityError shaped like the ones psycopg2 actually raises."""
    return IntegrityError(
        "INSERT INTO transactions (user_id, client, amount) "
        "VALUES (%(user_id)s, %(client)s, %(amount)s)",
        {"user_id": 1, "client": "Νησίδα Café", "amount": None},
        Exception(message),
    )


NOT_NULL = _integrity(
    'null value in column "amount" of relation "transactions" violates '
    "not-null constraint\nDETAIL:  Failing row contains (12, 1, 3, null)."
)


# --- The sentence ---------------------------------------------------------
def test_a_missing_amount_names_the_column_to_fix():
    assert errors.describe(NOT_NULL) == errors.AMOUNTS
    assert "στήλη ποσού" in errors.describe(NOT_NULL)


@pytest.mark.parametrize("message,expected", [
    ('invalid input syntax for type numeric: "1.240,00"', errors.AMOUNTS),
    ("numeric field overflow", "πολύ μεγάλο"),
    ("duplicate key value violates unique constraint", "υπάρχει ήδη"),
    ("insert or update violates foreign key constraint", "συνδέεται"),
    ("canceling statement due to statement timeout", "άργησε"),
    ("could not connect to server", "σύνδεση"),
    ("deadlock detected", "απασχολημένη"),
])
def test_each_known_failure_gets_its_own_explanation(message, expected):
    assert expected in errors.describe(_integrity(message))


def test_an_unrecognised_failure_still_says_what_to_do():
    """Not "unknown error": the user cannot act on the cause, but they can act
    on "try again, and tell us if it persists"."""
    described = errors.describe(_integrity("something nobody has seen before"))
    assert described == errors.GENERIC
    assert "Δοκιμάστε ξανά" in described


# --- The leak -------------------------------------------------------------
@pytest.mark.parametrize("exc", [
    NOT_NULL,
    _integrity("invalid input syntax for type numeric"),
    OperationalError("SELECT 1", {}, Exception("could not connect to server")),
])
def test_nothing_from_the_exception_reaches_the_user(exc):
    """The whole point. A partial leak is the same leak with extra steps, so
    the assertion is that NONE of it survives — not that it is tidied up."""
    described = errors.describe(exc)
    for leaked in ("{", "}", "[SQL", "parameters", "INSERT INTO", "SELECT",
                   "psycopg2", "sqlalche", "relation", "Νησίδα Café",
                   "transactions", "http"):
        assert leaked not in described, f"{leaked!r} leaked into the message"


def test_the_http_error_carries_only_the_clean_sentence():
    raised = errors.db_error(NOT_NULL)
    assert raised.status_code == 502
    assert raised.detail == errors.AMOUNTS
    assert "{" not in raised.detail


# --- sanitize -------------------------------------------------------------
@pytest.mark.parametrize("noise", [
    "{'error': 'null value in column amount'}",
    "Σφάλμα: [SQL: INSERT INTO transactions (id) VALUES (1)]",
    "[parameters: {'amount': None}]",
    "Traceback (most recent call last):",
    'File "server/store.py", line 42, in create_transaction',
    "psycopg2.errors.NotNullViolation",
    "See https://sqlalche.me/e/20/gkpj",
    "IntegrityError",
])
def test_technical_noise_is_replaced_wholesale(noise):
    assert errors.sanitize(noise) == errors.GENERIC


@pytest.mark.parametrize("clean", [
    "Ο πελάτης δεν βρέθηκε.",
    "Το αρχείο ξεπερνά τα 5 MB.",
    "Απαιτείται σύνδεση.",
])
def test_a_message_written_for_people_passes_through(clean):
    assert errors.sanitize(clean) == clean


def test_a_multi_line_database_error_does_not_arrive_as_fragments():
    assert "\n" not in errors.sanitize("πρώτη γραμμή\nδεύτερη γραμμή")


# --- Strict float coercion ------------------------------------------------
@pytest.mark.parametrize("given,expected", [
    (1240, 1240.0),               # int
    (1240.004, 1240.0),           # rounded to cents
    (1240.006, 1240.01),
    ("1240.5", 1240.5),           # a numeric string from a JSON body
    (-310.0, -310.0),
    (None, None),
])
def test_money_coerces_what_a_database_will_take(given, expected):
    assert store.money(given) == expected


def test_money_accepts_a_decimal_from_a_spreadsheet_cell():
    """openpyxl can hand back a Decimal, which raises the moment it meets a
    float in finance.py's arithmetic."""
    from decimal import Decimal

    assert store.money(Decimal("1240.00")) == 1240.0


@pytest.mark.parametrize("given", [
    float("nan"),
    float("inf"),
    float("-inf"),
    "χίλια διακόσια",
    "",
    object(),
    True,          # bool is an int subclass — True would become 1.00 €
])
def test_money_refuses_what_postgres_would_refuse(given):
    """NaN and infinity are ordinary floats to Python and pass float() without
    complaint; the column rejects them. Caught here, they cost one row —
    reaching the insert, they take the whole batch down."""
    with pytest.raises(store.AmountError):
        store.money(given)


def test_a_required_amount_cannot_be_none():
    assert store.money(None, allow_none=True) is None
    with pytest.raises(store.AmountError):
        store.money(None, allow_none=False)


# --- End to end -----------------------------------------------------------
def test_an_import_row_with_an_unusable_amount_is_skipped_not_fatal(api, monkeypatch):
    """One bad figure must cost its own row. Left to the insert it fails the
    whole batch at commit — four hundred good rows lost to one, reported as a
    column name."""
    import finance

    real = finance.book_amounts
    calls = {"n": 0}

    def poisoned(*args, **kwargs):
        calls["n"] += 1
        signed, vat = real(*args, **kwargs)
        # Second row only: a NaN, which float() accepts and Postgres does not.
        return (float("nan"), vat) if calls["n"] == 2 else (signed, vat)

    monkeypatch.setattr("server.store.finance.book_amounts", poisoned)

    csv = (
        "Ημερομηνία;Πελάτης;Είδος Κίνησης;Σύνολο\r\n"
        "2026-01-15;Νησίδα Café;Έσοδο;1240,00\r\n"
        "2026-01-16;Οδός Τεχνική;Έσοδο;620,00\r\n"
        "2026-01-17;Αφοί Γεωργίου;Έσοδο;310,00\r\n"
    ).encode("utf-8-sig")

    res = api.post("/api/import/transactions",
                   files={"file": ("k.csv", csv, "text/csv")})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["imported"] == 2
    assert body["skipped"] == 1
    assert "ποσού" in body["skipped_rows"][0]["message"]
    # And the message the user reads carries no machinery.
    assert "{" not in body["skipped_rows"][0]["message"]


def test_an_import_never_books_a_row_worth_nothing(api):
    """"Rows imported, total 0 €" is not a book — it is a file whose amount
    column was misread, and storing it would put worthless rows in the ledger."""
    csv = (
        "Ημερομηνία;Πελάτης;Είδος Κίνησης;Σύνολο\r\n"
        "2026-01-15;Νησίδα Café;Έσοδο;1240,00\r\n"
    ).encode("utf-8-sig")
    body = api.post("/api/import/transactions",
                    files={"file": ("k.csv", csv, "text/csv")}).json()
    assert body["imported"] == 1
    assert body["total_amount"] == 1240.0
