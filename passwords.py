"""
Password hashing for the multi-tenant login (Users table, Password column).

Scheme: PBKDF2-HMAC-SHA256 — salted SHA-256 done properly, from the Python
standard library alone (no bcrypt wheel to build on Render). Each hash gets
its own random 16-byte salt and 600 000 iterations (OWASP's current PBKDF2
recommendation), stored self-describing in a single text cell:

    pbkdf2_sha256$600000$<salt hex>$<digest hex>

The iteration count travels inside the string, so it can be raised later
without invalidating existing rows — old hashes keep verifying with their
recorded count, new ones get the new count.

Migrating existing plaintext passwords in Airtable
--------------------------------------------------
Nothing breaks on deploy: the login gate (auth.password_matches) accepts BOTH
formats. A Password cell that doesn't start with "pbkdf2_sha256$" is treated as
legacy plaintext and still verifies. So the options are:

  A. Do nothing — every account self-migrates the next time it logs in.
  B. Migrate a row by hand — run locally:

         python passwords.py

     type the password at the hidden prompt, then paste the printed
     pbkdf2_sha256$… string into that row's Password cell.

Either way the Password column must stay a TEXT field in Airtable; a Number
column can't hold the hash string.

New accounts: paste a `python passwords.py` hash into the Password cell at
creation time (plaintext also works and self-migrates, but never storing it
is better).
"""

import hashlib
import hmac
import secrets

_SCHEME = "pbkdf2_sha256"
_ITERATIONS = 600_000
_SALT_BYTES = 16


def hash_password(plain, iterations=_ITERATIONS):
    """Return the self-describing hash string for one plaintext password."""
    salt = secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", plain.encode("utf-8"), salt.encode("ascii"), iterations
    ).hex()
    return f"{_SCHEME}${iterations}${salt}${digest}"


def is_hashed(value):
    """True when a Password cell already holds a hash (vs legacy plaintext)."""
    return isinstance(value, str) and value.startswith(_SCHEME + "$")


def verify_password(stored, typed):
    """Check a typed password against a stored pbkdf2_sha256$… string.

    Constant-time comparison; any malformed stored value fails closed.
    """
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != _SCHEME:
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", typed.encode("utf-8"), salt.encode("ascii"),
            int(iterations),
        ).hex()
    except (AttributeError, ValueError):
        return False
    return hmac.compare_digest(candidate, digest)


if __name__ == "__main__":
    # Admin helper: print the hash for one password, ready to paste into the
    # Airtable Password cell. Prompted (hidden), so the password never lands
    # in shell history.
    import getpass

    pw = getpass.getpass("Password to hash: ").strip()
    if not pw:
        raise SystemExit("Empty password — nothing hashed.")
    print(hash_password(pw))
