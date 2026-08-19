"""
What counts as an acceptable password, in one place.

Pure and dependency-free — no database, no config, no HTTP — so registration,
the password reset and the admin-creation script can all apply the same rule
instead of each inventing a slightly different one. `problem()` returns a Greek
sentence a user can act on, or None.

The shape of the rule, and why
------------------------------
A minimum LENGTH does the overwhelming majority of the work, so it is the first
and hardest gate. What follows is deliberately not the classic
"upper+lower+digit+symbol" mandate: that rule is well documented (NIST SP
800-63B) as producing `Password1!` — which satisfies every clause and is on
every wordlist — while rejecting a genuinely strong passphrase. So:

  * 8 characters minimum, matching what the API already enforced;
  * anything from 16 characters up is accepted on length alone, because a
    passphrase does not need decoration to be strong;
  * below 16, at least two distinct character classes, which rules out
    `12345678` and `password` without demanding a symbol nobody can type on a
    Greek keyboard layout;
  * never a known-common password, whatever its shape;
  * never something derived from the account's own identity — an office called
    "Λογιστές ΑΕ" whose password is "logistes" is one guess away from open,
    and the attacker starts with the company name because it is on the invoice.

The messages are Greek because every other user-facing string in this API is,
and this one is read at the exact moment somebody is deciding whether the
product is worth the trouble.
"""

import re
import unicodedata

# The only import, and a deliberate one: Greek↔Latin is the difference between
# refusing "papadopoulos1" for an office called Παπαδόπουλος and waving it
# through. server/text.py is itself stdlib-only, so this module stays pure.
from server.text import latinise

#: The floor. Mirrored by server/main.MIN_PASSWORD_LENGTH and by the signup
#: form's client-side hint; both defer to this module for the real check.
MIN_LENGTH = 8

#: Above this, length alone is enough — see the module docstring.
PASSPHRASE_LENGTH = 16

#: An upper bound, and not an arbitrary one. PBKDF2 hashes the whole input at
#: 600 000 iterations (passwords.py), so an unbounded field is a free way to
#: make the server do unbounded work on an UNAUTHENTICATED endpoint.
MAX_LENGTH = 200

#: Passwords that are common enough to be tried first by anything automated.
#: Deliberately short: a real wordlist belongs in a rate limiter's threat model,
#: not in application code, and this list exists to catch the handful that people
#: genuinely type into a signup form. Greek-keyboard and Greek-transliterated
#: entries are here because an English-only list misses exactly the ones this
#: product's users would choose.
COMMON_PASSWORDS = frozenset({
    "password", "password1", "password123", "passw0rd", "p@ssword",
    "12345678", "123456789", "1234567890", "12345678910", "87654321",
    "qwertyui", "qwerty123", "qwertyuiop", "asdfghjk", "asdfghjkl",
    "11111111", "00000000", "abcd1234", "1q2w3e4r", "1qaz2wsx",
    "iloveyou", "sunshine", "princess", "football", "baseball",
    "welcome1", "welcome123", "admin123", "administrator", "letmein1",
    "monkey123", "dragon123", "trustno1", "starwars", "superman",
    # Greek, and Greeklish — what a Greek user actually reaches for.
    "kwdikos", "kodikos", "κωδικός", "κωδικος", "ελλαδα", "ελλάδα",
    "olympiakos", "panathinaikos", "thessaloniki", "kalimera",
    "logistis", "logistiki", "logistes", "timologio",
})


def _fold(value):
    """Lower-case and strip accents, so "Λογιστές" and "λογιστες" compare equal.

    NFD splits an accented character into base + combining mark; dropping the
    marks (category Mn) is what makes the comparison accent-insensitive without
    a transliteration table.
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFD", str(value).strip().lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


#: Latin AND Greek in each class: a Greek user typing "κωδικόςΜου7" is using
#: three classes, and a Latin-only test would score it one and reject it.
_LOWER = re.compile(r"[a-zα-ωάέήίόύώϊϋΐΰς]")
_UPPER = re.compile(r"[A-ZΑ-ΩΆΈΉΊΌΎΏΪΫ]")
_DIGIT = re.compile(r"\d")
_SYMBOL = re.compile(r"[^0-9A-Za-zΑ-Ωα-ωΆΈΉΊΌΎΏΪΫάέήίόύώϊϋΐΰς\s]|_")


def _classes(password):
    """How many distinct character classes the password draws on."""
    return sum(bool(pattern.search(password))
               for pattern in (_LOWER, _UPPER, _DIGIT, _SYMBOL))


def _split(text):
    """The whole string plus each word in it, as comparison fragments."""
    if not text:
        return set()
    return {text} | {part for part in re.split(r"[^\w]+", text) if part}


def _identity_fragments(*values):
    """The pieces of an account's identity a password must not simply repeat.

    An email is split at the "@" and again on separators, so
    "maria.papadopoulou@grafeio.gr" contributes "maria", "papadopoulou" and
    "grafeio" — the parts somebody would actually reuse — rather than only the
    whole address, which nobody types as their password.

    Each value contributes BOTH its accent-folded form and its transliteration,
    which matters here more than it would in an English product: the office is
    registered as "Παπαδόπουλος Λογιστική" and the password its owner reaches
    for is "papadopoulos1". Comparing only the Greek would miss that entirely,
    and it is the single most predictable password this product will ever be
    offered.
    """
    fragments = set()
    for value in values:
        folded = _fold(value)
        if not folded:
            continue
        if "@" in folded:
            local, _, domain = folded.partition("@")
            folded = local
            # The domain's own name, without the TLD.
            fragments.update(p for p in re.split(r"[^\w]+", domain)[:-1] if p)
        fragments |= _split(folded)
        fragments |= _split(latinise(folded))
    # Anything shorter than four characters is too generic to be evidence: "ae"
    # from "Λογιστές ΑΕ" would ban every password containing those two letters.
    return {f for f in fragments if len(f) >= 4}


def problem(password, *, email=None, username=None, company_name=None,
            full_name=None):
    """Return a Greek explanation of why this password is unacceptable, or None.

    Every caller gets the same verdict. The identity arguments are all optional
    — a password reset knows the account, a signup knows the form — and each one
    given makes the check slightly stricter rather than changing its shape.
    """
    if password is None or not str(password).strip():
        return "Δώστε κωδικό πρόσβασης."

    # NOT stripped: leading and trailing spaces are legitimate characters in a
    # password, and silently trimming them here would store something different
    # from what the user typed — and then refuse their login.
    password = str(password)

    if len(password) < MIN_LENGTH:
        return (f"Ο κωδικός πρέπει να έχει τουλάχιστον {MIN_LENGTH} χαρακτήρες.")
    if len(password) > MAX_LENGTH:
        return (f"Ο κωδικός δεν μπορεί να ξεπερνά τους {MAX_LENGTH} χαρακτήρες.")

    folded = _fold(password)

    if folded in COMMON_PASSWORDS:
        return ("Αυτός ο κωδικός είναι από τους πιο συνηθισμένους και "
                "δοκιμάζεται πρώτος. Επιλέξτε κάτι διαφορετικό.")

    if len(set(password)) == 1:
        return "Ο κωδικός δεν μπορεί να είναι ο ίδιος χαρακτήρας επαναλαμβανόμενος."

    # A run of consecutive keys or digits — "12345678", "abcdefgh" — passes a
    # naive class check and is guessed instantly.
    if _is_sequential(password):
        return ("Ο κωδικός δεν μπορεί να είναι μια συνεχόμενη σειρά χαρακτήρων "
                "(π.χ. 12345678).")

    # Compared against both spellings of the password for the same reason the
    # fragments carry both: "Παπαδόπουλος" and "papadopoulos" are the same
    # secret, and only one of them is what the owner typed.
    haystacks = {folded, latinise(folded)}
    for fragment in _identity_fragments(email, username, company_name, full_name):
        if any(fragment in hay for hay in haystacks):
            return ("Ο κωδικός δεν πρέπει να περιέχει το email, το όνομά σας ή "
                    "την επωνυμία του γραφείου.")

    # Length alone is enough for a passphrase — see the module docstring.
    if len(password) < PASSPHRASE_LENGTH and _classes(password) < 2:
        return ("Ο κωδικός χρειάζεται τουλάχιστον δύο από: πεζά, κεφαλαία, "
                f"αριθμούς ή σύμβολα — ή {PASSPHRASE_LENGTH}+ χαρακτήρες.")

    return None


def _is_sequential(password):
    """True when the whole password is one ascending or descending run.

    Only the whole thing: "abc" inside a longer password is not a weakness, and
    banning it would reject a great many perfectly good passphrases.
    """
    if len(password) < 4:
        return False
    deltas = {ord(b) - ord(a) for a, b in zip(password, password[1:])}
    return deltas in ({1}, {-1})


def is_acceptable(password, **identity):
    """Boolean form, for callers that only need the verdict."""
    return problem(password, **identity) is None
