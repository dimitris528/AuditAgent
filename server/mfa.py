"""
Two-factor authentication: TOTP codes, and the devices allowed to skip them.

Native TOTP (RFC 6238) rather than Supabase MFA, because this application does
not use Supabase Auth — it mints its own tokens in auth.py and Supabase is the
hosted Postgres and nothing more. There is no Supabase session to enrol a
factor against. TOTP is the same second factor by the same standard, and it
works with the authenticator app the user already has.

The login flow, and why it has three legs
-----------------------------------------
    POST /api/auth/login          password  → {"mfa_required": true, challenge}
    POST /api/v1/auth/mfa/verify  code      → {"access_token": ...}

The challenge is a short-lived token that says "this password was correct" and
nothing else: it carries no tenant, opens no data, and expires in minutes. The
session token is minted only after the second factor, so a stolen password
alone never yields anything that can read a book.

Trusting a device
-----------------
"Εμπιστοσύνη σε αυτή τη συσκευή για 30 ημέρες" swaps the prompt for a cookie:
32 bytes of CSPRNG output, stored hashed (see models.TrustedDevice), returned
as an httpOnly cookie the browser cannot read. On the next login the cookie is
presented alongside the password and, if it resolves to a live row for THAT
user, the second factor is skipped.

Three properties that make that safe rather than merely convenient:

  * the cookie is httpOnly and SameSite=Lax, so script on the page cannot read
    it and another origin cannot make the browser send it;
  * it is bound to one user id. Presenting another account's device token
    proves nothing about this one, and is treated as no token at all;
  * it is a database row, so it can be REVOKED. A self-contained signed token
    would be valid until it expired no matter what the user did about the
    laptop they left on a train.

The bypass is for the PROMPT, never for the password. A trusted device still
authenticates in full; it is the second factor it is excused from.
"""

import datetime as dt
import hashlib
import secrets
import time
from urllib.parse import quote

import jwt

import auth

try:  # pragma: no cover - exercised by whether the dependency is installed
    import pyotp
except ImportError:  # pragma: no cover
    pyotp = None


class MfaError(RuntimeError):
    """A second factor that could not be accepted. Message is user-facing."""

    def __init__(self, message, status=401):
        super().__init__(message)
        self.status = status


# --- TOTP -----------------------------------------------------------------
#: The issuer shown in the authenticator app's list.
ISSUER = "ΛογιστήριοPro"

#: How many 30-second steps either side of now are accepted. One step covers
#: the phone clock being a little out and the user typing slowly; more than
#: that widens the window a stolen code stays usable in.
VALID_WINDOW = 1


def is_available():
    """False when pyotp is not installed. Checked before enrolment is offered,
    so a deployment without the dependency hides the feature rather than
    presenting a setup screen that cannot finish."""
    return pyotp is not None


def _require_pyotp():
    if pyotp is None:
        raise MfaError(
            "Η ταυτοποίηση δύο παραγόντων δεν είναι διαθέσιμη σε αυτόν τον "
            "διακομιστή.", status=503)


def new_secret():
    _require_pyotp()
    return pyotp.random_base32()


def provisioning_uri(secret, account):
    """The otpauth:// URI an authenticator app scans.

    Returned as a URI rather than a rendered QR image: the browser draws it,
    and the secret never becomes a PNG sitting in an HTTP cache.
    """
    _require_pyotp()
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=ISSUER)


def qr_payload(secret, account):
    """The same URI, percent-encoded for embedding in a chart URL if a client
    wants one. Kept here so no caller hand-rolls the encoding."""
    return quote(provisioning_uri(secret, account), safe="")


def verify_code(secret, code):
    """True when `code` is a live TOTP for `secret`.

    Whitespace is stripped because every authenticator app displays the six
    digits as "123 456" and people copy what they see.
    """
    _require_pyotp()
    if not secret or not code:
        return False
    cleaned = "".join(str(code).split())
    if not cleaned.isdigit():
        return False
    return pyotp.TOTP(secret).verify(cleaned, valid_window=VALID_WINDOW)


# --- The challenge between the two legs of login --------------------------
#: Minutes a challenge stays usable. Long enough to fetch a phone, short
#: enough that one left in a terminal's history is worthless.
CHALLENGE_MINUTES = 5

_CHALLENGE_AUDIENCE = "mfa-challenge"


def issue_challenge(username):
    """A token saying "this password was correct", and nothing else.

    Signed with the same key as a session token but carrying a distinct
    audience, which is what stops it being presented AS one: decode_token
    rejects it because the audience does not match, and verify_challenge
    rejects a session token for the same reason. Without that separation, the
    token handed out for passing leg one would open every endpoint on its own
    — the exact thing the second factor exists to prevent.
    """
    now = int(time.time())
    return jwt.encode(
        {
            "sub": username,
            "aud": _CHALLENGE_AUDIENCE,
            "iat": now,
            "exp": now + CHALLENGE_MINUTES * 60,
        },
        auth.JWT_SECRET,
        algorithm=auth.JWT_ALG,
    )


def verify_challenge(token):
    """The username a challenge belongs to, or raise MfaError."""
    try:
        payload = jwt.decode(
            token,
            auth.JWT_SECRET,
            algorithms=[auth.JWT_ALG],
            audience=_CHALLENGE_AUDIENCE,
            leeway=auth.JWT_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "sub", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        raise MfaError("Η επαλήθευση έληξε. Συνδεθείτε ξανά.")
    except jwt.InvalidTokenError:
        raise MfaError("Μη έγκυρη επαλήθευση. Συνδεθείτε ξανά.")
    sub = payload.get("sub")
    if not sub:
        raise MfaError("Μη έγκυρη επαλήθευση. Συνδεθείτε ξανά.")
    return sub


# --- Trusted devices ------------------------------------------------------
#: The cookie the browser holds. Named for what it is; nothing in it is
#: readable by script.
DEVICE_COOKIE = "device_trust"

#: Thirty days, which is what the checkbox promises. Stated once, here, so the
#: label and the expiry cannot drift apart.
TRUST_DAYS = 30

#: 32 bytes of CSPRNG output. Long enough that guessing is not a strategy.
_TOKEN_BYTES = 32


def new_device_token():
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_device_token(token):
    """sha256 of the raw token — what the table stores.

    Plain sha256 rather than a password KDF, for the reason store.py gives
    about reset tokens: this input is 256 bits of random, not a human-chosen
    secret, so there is no dictionary to slow an attacker down against.
    """
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def trust_expiry(now=None):
    return (now or dt.datetime.now(dt.timezone.utc)) + dt.timedelta(days=TRUST_DAYS)


def device_label(user_agent):
    """A short, human-recognisable name for a browser.

    The user-agent, trimmed. Deliberately NOT a fingerprint: this exists so the
    user can tell one row from another in a list, not so we can identify a
    machine that has cleared its cookies.
    """
    text = " ".join(str(user_agent or "").split())
    if not text:
        return "Άγνωστη συσκευή"
    return text[:200]


def cookie_kwargs(secure):
    """The flags the device cookie is set with, in one place.

    httpOnly so page script cannot read a 2FA bypass. SameSite=Lax so another
    origin cannot make the browser present it. Secure in production, and NOT
    over plain-HTTP local development, where a Secure cookie is simply never
    stored and the feature would appear broken.
    """
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": bool(secure),
        "max_age": TRUST_DAYS * 24 * 60 * 60,
        "path": "/",
    }
