"""
PostgreSQL (Supabase) engine and session handling.

Connection
----------
DATABASE_URL comes from the environment (config.py strips it). Supabase hands
you a URL starting "postgresql://"; SQLAlchemy 2 needs an explicit driver, so
it is normalised to "postgresql+psycopg2://" here rather than making every
deployment remember to write it.

TWO things about the Supabase URL routinely break deployments, so both are
handled explicitly instead of failing as an opaque connection error:

1. Password escaping. The pooler password is generated and frequently contains
   "/", "@", ":" or "?". Those are URL-structural characters: an unescaped "/"
   terminates the userinfo section, so a URL of the shape
       postgresql://user:pa/ss@host:6543/postgres
   parses with host="user" and port="pa" — and the resulting error mentions a
   port, not a password, which sends you looking in the wrong place entirely.
   The password MUST be percent-encoded in DATABASE_URL (/ -> %2F, @ -> %40,
   : -> %3A, ? -> %3F). _require_url validates this up front and says so.

2. pgbouncer. Port 6543 is Supabase's TRANSACTION-mode pooler, which hands a
   different backend connection to every transaction. Layering SQLAlchemy's own
   pool on top of that pools something that is already pooled and leaks
   session state across tenants, so the engine uses NullPool. The
   "?pgbouncer=true" query param is a client hint that libpq does not
   understand, so it is stripped before it reaches psycopg2.

Port 5432 (the direct, session-mode connection) also works and is fine for the
one-shot backfill script, but 6543 is the right choice for a web service on
Render's free tier, where connection count is the scarce resource.
"""

import os

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine

# Read through config rather than os.getenv: config.load_dotenv() is what makes
# a local .env visible, and config strips surrounding whitespace. Reading the
# environment directly here made the value depend on whether config happened to
# be imported first — under uvicorn it was not, so a local .env was ignored and
# the API silently fell back to demo mode.
from config import DATABASE_URL

# Imported for its side effect: SQLModel.metadata only knows about tables whose
# classes have been imported, so init_db() would create nothing without this.
from server import models  # noqa: F401

# Statement timeout so one pathological query cannot pin a pooler connection.
_STATEMENT_TIMEOUT_MS = 15000

_engine = None


class DatabaseNotConfigured(RuntimeError):
    """DATABASE_URL is missing or unusable. Raised lazily so the process can
    still boot and report the problem through /api/status."""


def _normalise(raw):
    """Return a SQLAlchemy URL with the psycopg2 driver and pgbouncer's
    client-only query params removed."""
    url = make_url(raw)
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg2")
    # pgbouncer=true is meaningful to Supabase's docs and to some clients, but
    # libpq rejects it as an unknown connection option.
    query = {k: v for k, v in url.query.items() if k != "pgbouncer"}
    return url.set(query=query)


def _require_url():
    if not DATABASE_URL:
        raise DatabaseNotConfigured(
            "DATABASE_URL is not set. Copy the Supabase connection string into "
            "the environment, percent-encoding any /, @, : or ? in the password."
        )
    try:
        url = _normalise(DATABASE_URL)
    except (ArgumentError, ValueError) as exc:
        raise DatabaseNotConfigured(
            f"DATABASE_URL could not be parsed ({exc}). The usual cause is an "
            f"un-escaped special character in the password — percent-encode it "
            f"(/ -> %2F, @ -> %40, : -> %3A, ? -> %3F)."
        ) from exc
    # Only meaningful for a network database: this is the signature of the
    # un-escaped-password failure, where the "/" swallows the host and port.
    # A file-backed URL (sqlite:///path) legitimately has no host.
    if url.drivername.startswith("postgresql") and (not url.host or not url.database):
        raise DatabaseNotConfigured(
            "DATABASE_URL is missing a host or database name. An un-escaped "
            "'/' in the password does exactly this — percent-encode it as %2F."
        )
    return url


def get_engine():
    """Lazily build the process-wide engine. Lazy so importing this module (and
    therefore the app) never requires a reachable database."""
    global _engine
    if _engine is None:
        url = _require_url()
        kwargs = {"pool_pre_ping": True}
        if is_postgres(url):
            # psycopg2-specific; passing these to any other driver raises.
            kwargs["poolclass"] = NullPool   # see module docstring: pgbouncer
            kwargs["connect_args"] = {
                "connect_timeout": 10,
                "options": f"-c statement_timeout={_STATEMENT_TIMEOUT_MS}",
            }
        _engine = create_engine(url, **kwargs)
    return _engine


def is_postgres(url=None):
    """True when the configured database is PostgreSQL.

    Keeps the Postgres-only pieces (pgbouncer pooling, statement_timeout, the
    ADD COLUMN IF NOT EXISTS migrations) from firing against another backend —
    which is what lets the endpoint suite run on a local SQLite file when the
    Supabase credentials are not to hand.
    """
    try:
        url = url or _require_url()
    except DatabaseNotConfigured:
        return False
    return url.drivername.startswith("postgresql")


def is_configured():
    """True when DATABASE_URL is present and parseable. Does NOT open a
    connection — the callers use it to answer 503 immediately rather than
    paying for a round trip that cannot succeed."""
    if not DATABASE_URL:
        return False
    try:
        _require_url()
        return True
    except DatabaseNotConfigured:
        return False


# Columns added after the tables first shipped. create_all() only issues
# CREATE TABLE and never ALTERs, so a table that already exists keeps its
# original shape forever — new model fields simply would not exist in the
# database, and every query naming them would fail.
#
# These statements are ADDITIVE ONLY and idempotent (IF NOT EXISTS). Nothing
# here drops, renames or retypes a column: that class of change loses data and
# belongs in a reviewed migration, not in a startup hook.
_ADDITIVE_MIGRATIONS = (
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS afm VARCHAR(32)",
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS contact VARCHAR(200)",
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS notes VARCHAR(2000)",
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS client_id INTEGER "
    "REFERENCES clients(id)",
    "CREATE INDEX IF NOT EXISTS ix_transactions_client_id "
    "ON transactions (client_id)",
    # Invoice identity + the partial-settlement back-link. The debt_payments
    # table itself needs nothing here: create_all() issues CREATE TABLE for
    # tables that do not exist yet, and only skips ones that do.
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS doc_number VARCHAR(64)",
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS counterparty_afm VARCHAR(32)",
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS debt_id INTEGER "
    "REFERENCES transactions(id)",
    "CREATE INDEX IF NOT EXISTS ix_transactions_doc_number "
    "ON transactions (doc_number)",
    "CREATE INDEX IF NOT EXISTS ix_transactions_counterparty_afm "
    "ON transactions (counterparty_afm)",
    "CREATE INDEX IF NOT EXISTS ix_transactions_debt_id "
    "ON transactions (debt_id)",
    # Document type + the debt due date behind the overdue alerts.
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS doc_type VARCHAR(64)",
    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS due_date DATE",
    "CREATE INDEX IF NOT EXISTS ix_transactions_doc_type "
    "ON transactions (doc_type)",
    "CREATE INDEX IF NOT EXISTS ix_transactions_due_date "
    "ON transactions (due_date)",
    # Billing. Covers the case where `users` predates the trial columns
    # entirely; the rename below covers the far commoner case where it has them
    # under the old name.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(128)",
    # Pending cancellation: the date Stripe will end a subscription the tenant
    # has asked to cancel. NULL for everyone who has not asked.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_cancel_at TIMESTAMPTZ",
    # Two-factor authentication. The secret is staged at enrolment and the flag
    # only flips once a code has verified, so a half-finished setup cannot lock
    # anyone out — see store.set_mfa_secret. Defaulted FALSE rather than NULL:
    # a null here would be read as "unknown" by anything doing a boolean test,
    # and the safe reading of unknown is "no second factor configured", which
    # is what every existing row genuinely is.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_secret VARCHAR(64)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE",
    # trusted_devices needs nothing here: create_all() issues CREATE TABLE for
    # a table that does not exist yet. The indexes are listed because a
    # database that somehow had the table without them would do a sequential
    # scan per login, and the lookup is by token_hash and nothing else.
    "CREATE INDEX IF NOT EXISTS ix_trusted_devices_token_hash "
    "ON trusted_devices (token_hash)",
    "CREATE INDEX IF NOT EXISTS ix_trusted_devices_user_id "
    "ON trusted_devices (user_id)",
    # password_reset_tokens needs nothing here — create_all() issues CREATE
    # TABLE for a table that does not exist yet. The index is listed because a
    # database that somehow has the table WITHOUT it would do a sequential scan
    # per reset attempt, and the lookup is by token_hash and nothing else.
    "CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_token_hash "
    "ON password_reset_tokens (token_hash)",
    "CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_user_id "
    "ON password_reset_tokens (user_id)",
)

# The ONE non-additive statement in this file, and the reason it is here rather
# than in a hand-run migration: `users.trial_expiry` was renamed to
# `trial_ends_at`, and until it runs, every query naming the new column fails on
# an already-deployed database — i.e. the whole app is down, not degraded.
#
# It is data-preserving (RENAME moves the column, values and all) and idempotent
# by construction: the DO block renames ONLY when the old name is present and
# the new one is not, so a second boot, a fresh database, and a half-applied
# state all converge on the same schema. It touches nothing else.
#
# The status lower-casing that follows is the data half of the same change. The
# column used to hold Airtable's Title Case single-select ("Active"); the code
# now compares against lower-case constants (server/subscription.py). Written
# once here so the stored values match what is read, rather than leaving every
# comparison to remember to fold case.
_RENAME_MIGRATIONS = (
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'users'
                      AND column_name = 'trial_expiry')
           AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                            WHERE table_schema = current_schema()
                              AND table_name = 'users'
                              AND column_name = 'trial_ends_at')
        THEN
            ALTER TABLE users RENAME COLUMN trial_expiry TO trial_ends_at;
        END IF;
    END $$;
    """,
)

_NORMALISE_STATUS = """
UPDATE users
   SET subscription_status = lower(btrim(subscription_status))
 WHERE subscription_status IS NOT NULL
   AND subscription_status <> lower(btrim(subscription_status))
"""

# Link transactions written before client_id existed to their client, matching
# on the denormalised name within the same tenant. Only fills NULLs, so it can
# run on every boot and will never overwrite a real association.
_BACKFILL_CLIENT_ID = """
UPDATE transactions t
   SET client_id = c.id
  FROM clients c
 WHERE t.client_id IS NULL
   AND c.user_id = t.user_id
   AND lower(btrim(c.name)) = lower(btrim(t.client))
"""


#: Whether the web process migrates its own schema at startup.
#:
#: On by default, which is what makes a fresh deployment work with no extra
#: step. Turned OFF once the application connects as a restricted role
#: (scripts/create_app_role.sql): ALTER TABLE requires ownership, so every
#: statement below would raise for a role that deliberately does not own its
#: tables — and a web process that can rewrite its own schema on restart is a
#: much larger blast radius than one that cannot.
MIGRATE_ON_BOOT = (os.getenv("DB_MIGRATE_ON_BOOT", "1").strip().lower()
                   not in ("0", "false", "no", "off"))


def init_db():
    """Create missing tables, then apply the additive column migrations.

    Safe to call on every boot: create_all only issues CREATE TABLE for tables
    that do not exist, and the ALTERs are IF NOT EXISTS.
    """
    if not MIGRATE_ON_BOOT:
        print("[INFO] DB_MIGRATE_ON_BOOT is off — skipping schema migration. "
              "Run it as the table owner when you deploy.")
        return
    engine = get_engine()
    SQLModel.metadata.create_all(engine)

    # The ALTERs below exist purely to bring ALREADY-DEPLOYED Postgres tables up
    # to the current model. A fresh database of any other dialect just got the
    # full schema from create_all, so there is nothing to add — and neither the
    # IF NOT EXISTS form nor the UPDATE...FROM backfill is portable.
    if not is_postgres():
        return

    from sqlalchemy import text  # local import keeps the module surface small

    with engine.begin() as conn:
        # Renames FIRST: the ALTERs below would otherwise add a second, empty
        # trial_ends_at alongside the populated trial_expiry, and the rename
        # would then never fire — silently losing every trial date.
        for statement in _RENAME_MIGRATIONS:
            conn.execute(text(statement))
        for statement in _ADDITIVE_MIGRATIONS:
            conn.execute(text(statement))
        folded = conn.execute(text(_NORMALISE_STATUS)).rowcount
        linked = conn.execute(text(_BACKFILL_CLIENT_ID)).rowcount
    if folded:
        print(f"[INFO] Lower-cased subscription_status on {folded} user row(s).")
    if linked:
        print(f"[INFO] Linked {linked} transaction(s) to their client row.")


def get_session():
    """FastAPI dependency yielding a session that is always closed."""
    with Session(get_engine()) as session:
        yield session


def session_scope():
    """Context manager for use outside request handling (scripts, startup)."""
    return Session(get_engine())
