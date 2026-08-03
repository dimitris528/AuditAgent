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
    connection — the callers use this to choose between live data and the demo
    dataset without paying for a round trip."""
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
)

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


def init_db():
    """Create missing tables, then apply the additive column migrations.

    Safe to call on every boot: create_all only issues CREATE TABLE for tables
    that do not exist, and the ALTERs are IF NOT EXISTS.
    """
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
        for statement in _ADDITIVE_MIGRATIONS:
            conn.execute(text(statement))
        linked = conn.execute(text(_BACKFILL_CLIENT_ID)).rowcount
    if linked:
        print(f"[INFO] Linked {linked} transaction(s) to their client row.")


def get_session():
    """FastAPI dependency yielding a session that is always closed."""
    with Session(get_engine()) as session:
        yield session


def session_scope():
    """Context manager for use outside request handling (scripts, startup)."""
    return Session(get_engine())
