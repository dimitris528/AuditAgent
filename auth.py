"""
Authentication for the web backend (server/main.py).

Credentials are verified against the Airtable Users table (Username + Password,
pbkdf2_sha256 hash via passwords.py, legacy plaintext still accepted — the
retired Streamlit app wrote both formats). On success a signed JWT is minted;
every data endpoint resolves the tenant strictly from that token's `sub`, so
one user can never read another's rows.

Bootstrap/demo: when Airtable isn't configured yet (placeholder PAT) and demo
mode is on, a single fixed demo credential (demo / demo) logs in and sees the
in-memory demo dataset — so the whole auth flow + UI is testable before any
secrets exist. Once real Airtable credentials are set, the demo login is
rejected and only real Users-table accounts work.
"""

import os
import secrets
import time

import jwt

import airtable_client as db
import passwords

# --- JWT config ------------------------------------------------------------
# In production ALWAYS set JWT_SECRET (render.yaml generates one). The dev
# default keeps local runs working but is intentionally not a real secret.
_DEV_SECRET = "dev-insecure-change-me"
JWT_SECRET = (os.getenv("JWT_SECRET") or "").strip() or _DEV_SECRET
JWT_ALG = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "12"))

# Small tolerance for clock skew between Render's box and whatever minted the
# token. Without it a few seconds of drift rejects freshly-issued tokens.
JWT_LEEWAY_SECONDS = 30

# Claims every token must carry. Listing them explicitly means a token that
# somehow lost its `exp` is rejected instead of being treated as non-expiring.
_REQUIRED_CLAIMS = ["exp", "iat", "sub"]

JWT_SECRET_IS_DEFAULT = JWT_SECRET == _DEV_SECRET
if JWT_SECRET_IS_DEFAULT:
    print("[WARN] JWT_SECRET is unset — using the insecure development default. "
          "Set JWT_SECRET before serving real data.")


def _assert_secret_usable():
    """Refuse to mint or trust tokens when real data is reachable but the
    signing key is the published development default.

    That combination is not merely weak, it is forgeable by anyone who has read
    this file: they could sign a token for any `sub` and read that tenant's
    rows. Failing closed here (rather than at import) keeps the process up so
    /api/status can still explain exactly what is misconfigured.
    """
    if JWT_SECRET_IS_DEFAULT and db.is_configured():
        raise AuthError(
            "Ο διακομιστής δεν έχει ρυθμισμένο JWT_SECRET — η σύνδεση είναι "
            "απενεργοποιημένη για λόγους ασφαλείας.",
            status=503,
        )

DEMO_ENABLED = os.getenv("DASHBOARD_DEMO", "1") != "0"
DEMO_USER = "demo"
DEMO_PASSWORD = "demo"


class AuthError(Exception):
    """Auth failure that is NOT a bad password — e.g. the Users lookup itself
    failed. Carries an HTTP status so the endpoint can distinguish 503 (retry)
    from 401 (wrong credentials, which is a plain None return)."""

    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


def password_matches(stored, typed):
    """Accept either a pbkdf2_sha256$… hash or a legacy plaintext cell (rows
    predating hashing). Fails closed on anything empty."""
    if stored is None:
        return False
    if isinstance(stored, float) and stored.is_integer():
        stored = int(stored)
    stored = str(stored).strip()
    typed = (typed or "").strip()
    if not stored:
        return False
    if passwords.is_hashed(stored):
        return passwords.verify_password(stored, typed)
    return secrets.compare_digest(stored.encode("utf-8"), typed.encode("utf-8"))


def authenticate(username, password):
    """Return a user dict {username, subscription, demo} on success, or None on
    invalid credentials. Raises AuthError when the check can't be performed.

    - Airtable configured → verify against the Users table.
    - Airtable NOT configured + demo mode → accept the fixed demo credential.
    """
    username = (username or "").strip()
    if not username:
        return None

    if not db.is_configured():
        if DEMO_ENABLED and username == DEMO_USER and password == DEMO_PASSWORD:
            return {"username": DEMO_USER, "subscription": "Demo", "demo": True}
        return None

    try:
        user = db.find_user(username=username)
    except db.AirtableError as exc:
        raise AuthError(f"Ο έλεγχος ταυτότητας απέτυχε προσωρινά: {exc}", status=503)

    if not user:
        return None
    fields = user.get("fields", {})
    if not password_matches(fields.get("Password"), password):
        return None
    return {
        "username": username,
        "subscription": fields.get("SubscriptionStatus"),
        "demo": False,
    }


def create_access_token(username, extra=None):
    """Mint a signed HS256 access token whose `sub` is the tenant key.

    `extra` may carry non-authoritative flags (e.g. demo) but must never be
    allowed to overwrite the registered claims — a caller passing
    {"sub": "someone-else"} would otherwise mint a token for another tenant.
    """
    _assert_secret_usable()
    now = int(time.time())
    payload = dict(extra or {})
    payload.update({
        "sub": username,
        "iat": now,
        "exp": now + JWT_EXPIRE_HOURS * 3600,
    })
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token):
    """Return the JWT payload, or raise AuthError(401) on any invalid/expired
    token.

    `algorithms` is an explicit allowlist: without it PyJWT would honour the
    token's own `alg` header, which is how the classic "alg: none" and
    HMAC-vs-RSA confusion forgeries work.
    """
    _assert_secret_usable()
    try:
        return jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALG],
            leeway=JWT_LEEWAY_SECONDS,
            options={
                "require": _REQUIRED_CLAIMS,
                "verify_exp": True,
                "verify_iat": True,
                "verify_signature": True,
            },
        )
    except jwt.ExpiredSignatureError:
        raise AuthError("Η συνεδρία έληξε — συνδεθείτε ξανά.", status=401)
    except jwt.InvalidTokenError:
        raise AuthError("Μη έγκυρη συνεδρία.", status=401)
