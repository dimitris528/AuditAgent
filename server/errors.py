"""
Turning a failure into something the person holding the mouse can act on.

Every database-backed endpoint used to answer a SQLAlchemy error with
``f"Σφάλμα βάσης δεδομένων: {exc}"``, and that string is not an error message —
it is a diagnostic dump. A single not-null violation rendered as:

    Σφάλμα βάσης δεδομένων: (builtins.Exception) null value in column "amount"
    of relation "transactions" violates not-null constraint
    DETAIL:  Failing row contains (12, 1, 3, null, null).
    [SQL: INSERT INTO transactions (user_id, client_id, client, amount, type)
     VALUES (%(user_id)s, ...)]
    [parameters: {'user_id': 1, 'client': 'Νησίδα Café', 'amount': None, ...}]
    (Background on this error at: https://sqlalche.me/e/20/gkpj)

Three things wrong with putting that on screen, in order of how much they
matter:

  * it tells the user nothing they can DO. The actionable fact — a row had no
    amount in it — is buried in the middle of a sentence about relations and
    constraints;
  * it leaks the schema, the statement and the bound parameters, which include
    the tenant's own data, to anywhere the message ends up (a screenshot, a
    support email, a bug tracker);
  * it reads as a crash, so a recoverable "check the amounts column" problem
    looks like the product falling over.

So the catalogue below maps the failures that actually happen onto fixed Greek
sentences, and the raw text is LOGGED rather than returned. Nothing from the
exception reaches the response — not a fragment, not a column name — because a
partial leak is the same leak with extra steps.

`sanitize` is the belt to that pair of braces: anything on its way to a user
goes through it, so a message from somewhere this module does not own still
cannot arrive carrying SQL or a dict.
"""

import re

from fastapi import HTTPException

#: What every unrecognised database failure says. Deliberately not "unknown
#: error": the user cannot act on the cause, but they CAN act on "try again,
#: and tell us if it persists", which is the whole content of this sentence.
GENERIC = ("Δεν ήταν δυνατή η ολοκλήρωση της ενέργειας λόγω προβλήματος στη "
           "βάση δεδομένων. Δοκιμάστε ξανά σε λίγο.")

#: The amounts message, used for every failure whose cause is a figure the
#: database would not take. Names the column to look at, because that is the
#: one thing the user can fix.
AMOUNTS = ("Δεν ήταν δυνατή η καταχώρηση των ποσών. Βεβαιωθείτε ότι το αρχείο "
           "περιέχει έγκυρους αριθμούς στη στήλη ποσού.")

# Matched against the LOWER-CASED exception text. Ordered: the first match
# wins, so the specific numeric signatures come before the generic not-null.
_SIGNATURES = (
    # A figure the numeric column would not accept — the import's own failure
    # mode, and the one worth naming precisely.
    (r"invalid input syntax for type (numeric|double)", AMOUNTS),
    (r"invalid input for query argument", AMOUNTS),
    (r"numeric field overflow|value out of range|is out of range",
     "Κάποιο ποσό είναι πολύ μεγάλο για να αποθηκευτεί. Ελέγξτε τη στήλη "
     "ποσού για λάθος υποδιαστολή ή επιπλέον μηδενικά."),
    # A not-null violation on a money column is the same problem wearing a
    # different hat: the row arrived with nothing in it.
    (r"not-null constraint.*(amount|vat|ποσ)", AMOUNTS),
    (r"null value in column \"(amount|vat_amount|vat_rate)\"", AMOUNTS),
    (r"not-null constraint|violates not-null",
     "Λείπει ένα υποχρεωτικό πεδίο από κάποια γραμμή. Ελέγξτε ότι κάθε "
     "γραμμή έχει ημερομηνία, πελάτη και ποσό."),
    (r"unique constraint|duplicate key",
     "Η εγγραφή υπάρχει ήδη και δεν καταχωρήθηκε ξανά."),
    (r"foreign key constraint",
     "Η εγγραφή συνδέεται με άλλα δεδομένα και δεν μπορεί να αποθηκευτεί ή "
     "να διαγραφεί όπως ζητήθηκε."),
    (r"check constraint",
     "Κάποια τιμή δεν είναι αποδεκτή. Ελέγξτε τα ποσά και τις ημερομηνίες."),
    (r"canceling statement due to statement timeout|statement timeout",
     "Η ενέργεια άργησε υπερβολικά και διακόπηκε. Δοκιμάστε με μικρότερο "
     "αρχείο ή λιγότερες εγγραφές."),
    (r"could not connect|connection (refused|reset|closed)|server closed|"
     r"terminating connection|operationalerror",
     "Δεν υπάρχει σύνδεση με τη βάση δεδομένων αυτή τη στιγμή. Δοκιμάστε "
     "ξανά σε λίγο."),
    (r"deadlock detected|could not obtain lock",
     "Η βάση δεδομένων ήταν απασχολημένη. Δοκιμάστε ξανά."),
)

# --- Sanitisation ---------------------------------------------------------
# SQLAlchemy appends the statement and the bound parameters in brackets; the
# parameters are a dict, which is where the braces the user reported come from.
_SQL_TAIL = re.compile(r"\[(SQL|parameters|Background on this error)[^\]]*\]?.*",
                       re.IGNORECASE | re.DOTALL)
# Anything that betrays machinery rather than describing a problem.
_TECHNICAL = re.compile(
    r"[{}]"                       # a dict or a JSON fragment
    r"|\btraceback\b"
    r"|\bfile \"[^\"]+\", line \d+"
    r"|\b(select|insert into|update|delete from|alter table|create table)\b"
    r"|\bpsycopg2\b|\bsqlalchemy\b|\bsqlalche\.me\b"
    r"|https?://"
    r"|\b\w+error\b(?!\w)",        # NotNullViolation, OperationalError, …
    re.IGNORECASE)


#: Below this many characters, what survived the stripping is a fragment
#: rather than an explanation — see the trailing-punctuation note below.
_MIN_USEFUL = 15


def sanitize(text, fallback=GENERIC):
    """A message safe to show, or `fallback` when nothing safe is left.

    Applied to messages this module did NOT write — a validation error, a
    third-party client, anything that might have picked up machinery on the
    way. The test is deliberately blunt: if what remains still looks technical
    it is replaced wholesale rather than patched up, because a half-scrubbed
    dump is still a dump and the user is no better off reading half of it.
    """
    if not text:
        return fallback
    cleaned = _SQL_TAIL.sub("", str(text))
    # Collapse the newlines a multi-line database error arrives with, so a
    # message that survives is one paragraph rather than a stack of fragments.
    # The trailing punctuation goes too: stripping "[SQL: …]" off
    # "Σφάλμα: [SQL: …]" leaves a dangling colon.
    cleaned = " ".join(cleaned.split()).strip(" :;,-—")
    if len(cleaned) < _MIN_USEFUL or _TECHNICAL.search(cleaned):
        return fallback
    return cleaned


def describe(exc, fallback=GENERIC):
    """The Greek sentence for a database failure. Never quotes the exception.

    Fixed catalogue text only: returning any part of `str(exc)` is how a leak
    creeps back in one "helpful" detail at a time.
    """
    text = str(exc or "").lower()
    for pattern, message in _SIGNATURES:
        if re.search(pattern, text):
            return message
    return fallback


def db_error(exc, fallback=GENERIC, status=502):
    """The HTTPException to raise for a database failure.

    Logs the full text — losing it entirely would trade a user-facing problem
    for an undebuggable one — and returns only the catalogue sentence.

        except SQLAlchemyError as exc:
            raise errors.db_error(exc)
    """
    print(f"[ERROR] Database failure: {type(exc).__name__}: {exc}")
    return HTTPException(status_code=status, detail=describe(exc, fallback))
