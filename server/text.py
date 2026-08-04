"""
Greek-aware text normalisation, used ONLY for duplicate detection.

Client names in this book are overwhelmingly Greek, and the same company is
routinely typed three different ways: "Νησίδα Café", "νησιδα cafe",
"ΝΗΣΙΔΑ CAFE". A plain .lower() comparison — what the rest of the store uses to
decide which transactions belong to which client — treats those as three
separate companies, so the duplicate guard needs a stricter key.

Deliberately SEPARATE from store._key(). That one is a data-ownership
question: widening it retroactively would make two near-duplicate clients that
ALREADY exist start claiming each other's transactions, double-counting both.
The keys here only ever answer "does this new name/ΑΦΜ collide with an existing
one?", which is safe to make as aggressive as it needs to be.
"""

import re
import unicodedata

# Anything that is not a letter, digit or space. \w is Unicode-aware here, so
# Greek letters survive and only the punctuation goes.
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")
# Greece's ΑΦΜ is written both bare and with the intra-community VAT prefix.
_VAT_PREFIX = re.compile(r"^(?:EL|GR)")


def strip_accents(value):
    """Drop combining marks: ί → ι, ϊ → ι, é → e.

    NFD splits a precomposed letter into its base plus a combining mark, so
    dropping the combining characters leaves the bare letter behind. Callers
    fold case FIRST — folding is what turns ΐ into a form that decomposes.
    """
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def name_key(value):
    """The canonical form two client names are compared by.

    casefold() rather than lower(): it maps the Greek final sigma ς onto σ, so
    "ΟΔΟΣ" and "οδός" agree — lower() would leave "οδος" and "οδόσ" different.
    Punctuation is dropped so "Νησίδα Α.Ε." matches "Νησίδα ΑΕ", and runs of
    whitespace collapse to a single space.
    """
    if not value:
        return ""
    folded = strip_accents(str(value).casefold())
    return _SPACES.sub(" ", _PUNCT.sub("", folded)).strip()


def afm_key(value):
    """The canonical form two ΑΦΜ / VAT numbers are compared by.

    Formatting varies wildly on invoices ("123 456 789", "EL123456789",
    "123456789"), but the digits are the legal identity. Only the Greek EL/GR
    prefix is stripped — a foreign VAT number's country code is part of it.
    """
    if not value:
        return ""
    raw = re.sub(r"[^0-9A-Za-z]", "", str(value)).upper()
    return _VAT_PREFIX.sub("", raw)


def doc_key(value):
    """The canonical form two document numbers are compared by.

    Series prefixes are typed inconsistently ("ΤΠΥ-1042", "ΤΠΥ 1042",
    "τπυ1042"), so case, accents, punctuation and spaces all come out.
    """
    if not value:
        return ""
    folded = strip_accents(str(value).casefold())
    return _SPACES.sub("", _PUNCT.sub("", folded))
