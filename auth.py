"""
Authentication for the web backend (server/main.py).

Credentials are verified against the PostgreSQL `users` table (login by
username OR email + password_hash, pbkdf2_sha256 via passwords.py, legacy
plaintext still accepted — rows backfilled from Airtable may carry either).
On success a signed JWT is minted; every data endpoint resolves the tenant
strictly from that token's `sub`, so one user can never read another's rows.

`sub` remains the USERNAME rather than the numeric id: it is what every
existing token carries, so keeping it means the database migration does not
log every active session out.

A configured database is REQUIRED. The fixed demo/demo credential that used to
be accepted when DATABASE_URL was absent is gone, and so is the login page's
hint advertising it — see the note above AuthError. Without a database every
login is refused rather than falling back to a shared account.
"""

import os
import secrets
import time

import jwt

import passwords
from server import database, store, subscription

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
    if JWT_SECRET_IS_DEFAULT and database.is_configured():
        raise AuthError(
            "Ο διακομιστής δεν έχει ρυθμισμένο JWT_SECRET — η σύνδεση είναι "
            "απενεργοποιημένη για λόγους ασφαλείας.",
            status=503,
        )

# The fixed demo/demo credential that used to be accepted whenever
# DATABASE_URL was absent has been REMOVED, along with the login page's hint
# advertising it. It was a published password: anyone who read this file could
# sign in to any deployment that had not yet been given a database, and the
# login screen told every visitor exactly what to type. Authentication is now
# only ever against the users table.
#
# Deliberately not replaced with an env-gated version. A credential that is
# disabled by configuration is one misconfiguration away from being live, and
# "the demo account is on in production" is not a failure anything here would
# surface.


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


def authenticate(identifier, password):
    """Return a user dict {username, email, subscription} on success, or None on
    invalid credentials. Raises AuthError when the check can't be performed.

    `identifier` is a username OR an email address — registration collects an
    email, so people reasonably try to log in with it.

    Requires a configured database. Without one there is nothing to verify
    against and every login is refused — there is no demo fallback any more.
    """
    identifier = (identifier or "").strip()
    if not identifier:
        return None

    if not database.is_configured():
        return None

    try:
        with database.session_scope() as session:
            user = store.find_user(session, username=identifier, email=identifier)
            if not user:
                return None
            if not password_matches(user.password_hash, password):
                return None
            return {
                "username": user.username,
                "email": user.email,
                # Resolved, not read raw: signing in is the natural moment to
                # notice that a trial lapsed while nobody was looking, and
                # refresh_subscription persists that verdict.
                "subscription": store.refresh_subscription(session, user).to_dict(),
            }
    except AuthError:
        raise
    except Exception as exc:  # connection refused, timeout, bad credentials…
        raise AuthError(f"Ο έλεγχος ταυτότητας απέτυχε προσωρινά: {exc}", status=503)


def public_user(row):
    """The shape authenticate() returns, built from a User row already in hand.

    Exists so the second leg of a 2FA login can mint a session without
    re-checking the password: by that point the credential has been verified
    once and the challenge token is the proof. Same shape either way, so
    server/main.py has one payload builder rather than two that can drift.
    """
    return {
        "username": row.username,
        "email": row.email,
        "subscription": subscription.resolve(row.subscription_status,
                                             row.trial_ends_at).to_dict(),
    }


def create_access_token(username, extra=None):
    """Mint a signed HS256 access token whose `sub` is the tenant key.

    `extra` may carry non-authoritative flags but must never be
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
