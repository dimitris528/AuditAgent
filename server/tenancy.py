"""
Database-level tenant isolation — the belt to the application's braces.

Every query in server/store.py already filters on `user_id`, and that is what
keeps tenants apart today. It works, and it is one forgotten `.where()` away
from not working. This module adds the second layer: PostgreSQL Row Level
Security, so a query that forgets its tenant filter returns nothing rather than
returning somebody else's books.

Why not `auth.uid()`
--------------------
The canonical Supabase policy is

    CREATE POLICY tenant_isolation ON clients FOR ALL USING (auth.uid() = tenant_id);

and it cannot work here. `auth.uid()` reads the JWT claims that PostgREST sets
as request-scoped GUCs when a request arrives through Supabase's own API. This
application never goes near PostgREST: it connects with psycopg2 as a SINGLE
database role and mints its own HS256 tokens (auth.py). Through that
connection `auth.uid()` is NULL on every row, so the policy above has exactly
two possible outcomes, and both are bad:

  * the connecting role owns the tables, RLS is bypassed for owners, and the
    policy silently does nothing — security theatre that reads as protection;
  * the role does not own them, `NULL = user_id` is never true, and the
    application is denied every row in the database.

It is also a type error — auth.uid() is a uuid and users.id is an integer.

So the tenant is carried the way a backend with its own connection pool has to
carry it: as a session variable set at the start of every transaction, which
the policies read with current_setting(). Same guarantee, same failure mode
(no tenant set ⇒ no rows), reached by the mechanism this architecture actually
has.

Why `SET LOCAL`, re-applied per transaction
-------------------------------------------
Not a session-level `SET`. Supabase's port 6543 is pgbouncer in TRANSACTION
mode, which hands a different backend connection to every transaction — a
value set outside one belongs to whichever backend happened to answer, and the
next transaction may get another. `SET LOCAL` is scoped to the transaction, so
it travels with the statements it governs.

That is also why this hooks `after_begin` rather than setting the variable once
per session. store.py commits several times inside one session_scope, and each
commit ends a transaction: a value set once would be gone by the second write,
and under RLS that write would silently affect nothing.

Safe to deploy before the policies exist
----------------------------------------
Setting a GUC nothing reads is a no-op, so this can ship, be verified in the
logs, and only then have scripts/enable_rls.sql applied. The reverse order —
policies first — locks out any code path that has not been taught to set it.
"""

import contextlib
import contextvars

from sqlalchemy import event, text
from sqlmodel import Session

#: The tenant every statement in this context belongs to, or None outside a
#: request. A ContextVar rather than a module global because FastAPI serves
#: requests concurrently on one process: a global would let two tenants
#: overwrite each other's value between a query being built and executed.
_current_tenant = contextvars.ContextVar("current_tenant_id", default=None)

#: The GUC the policies read. Namespaced ("app.") because PostgreSQL only
#: allows custom settings with a prefix.
SETTING = "app.tenant_id"


def set_current_tenant(tenant_id):
    """Declare whose rows the rest of this request may touch.

    Returns the ContextVar token, so a caller that needs to restore the
    previous value can. Called once per request, from server/deps.py, the
    moment the JWT has been resolved to a real user — and never from a request
    body, which is the whole point of taking it from the token.
    """
    return _current_tenant.set(None if tenant_id is None else int(tenant_id))


def current_tenant():
    return _current_tenant.get()


def bind_session(session, tenant_id=None):
    """Apply the tenant to the transaction ALREADY in progress.

    The listener below stamps transactions as they BEGIN, which is not enough
    on its own, and the gap is the kind that only shows up against a database
    with the policies actually on.

    A request resolves its tenant by looking the user up — and that lookup is
    itself a query, so it opens the transaction. `after_begin` therefore fires
    while the tenant is still None and sets nothing, and every later statement
    in that same transaction runs unstamped. With RLS enforcing, those
    statements match no policy and the endpoint answers 200 with an empty
    book: not an error anywhere, just a dashboard that has quietly lost its
    data.

    So the tenant is pushed into the live transaction here, the moment it is
    known. The listener still matters for every transaction after it — store.py
    commits several times inside one session — and the two together cover the
    whole request.
    """
    tenant = _current_tenant.get() if tenant_id is None else int(tenant_id)
    if tenant is None:
        return
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT set_config(:name, :value, true)"),
        {"name": SETTING, "value": str(tenant)},
    )


def reset(token):
    """Restore the value from before set_current_tenant. Failing to restore is
    not a leak — the next request sets its own — but a task that outlives its
    request would otherwise inherit a stale tenant."""
    try:
        _current_tenant.reset(token)
    except (ValueError, LookupError):
        # Token from another context; the ContextVar is already correct.
        pass


def declare(session, tenant_id):
    """Declare the tenant AND stamp the transaction already in progress.

    The two calls belong together and were being made together in every place
    that made them at all, so they are one call now: set_current_tenant governs
    every transaction from here on, bind_session covers the one already open.
    Making either on its own is a bug, and both shapes of it are silent — see
    the note on bind_session.

    Returns the ContextVar token, for a caller that has to restore.
    """
    token = set_current_tenant(tenant_id)
    bind_session(session)
    return token


@contextlib.contextmanager
def tenant_scope(session, tenant_id):
    """`declare`, undone on the way out.

    For the legs of login that run BEFORE there is a session token: verifying a
    2FA code, and looking up or recording a trusted device. Those touch
    trusted_devices, which is tenant-scoped and carries an RLS policy like any
    other table of tenant data — so they need a declared tenant exactly as much
    as a dashboard request does, and it was not obvious that they did. What
    made it non-obvious is that nothing about the failure points here: the
    lookup returns no rows (so a trusted device is quietly never trusted) and
    the insert is refused by WITH CHECK (so completing a 2FA login reports a
    database problem).

    The tenant is legitimate at both call sites: by then the password has been
    verified, or the challenge token proving it has. What it is NOT is the
    caller's session — there isn't one yet — so it is scoped to the work that
    needs it and released after, rather than left declared on a request that
    may still be about to fail its second factor.
    """
    token = declare(session, tenant_id)
    try:
        yield
    finally:
        reset(token)


@event.listens_for(Session, "after_begin")
def _apply_tenant(session, transaction, connection):
    """Stamp the tenant onto every transaction, as it begins.

    Registered on the Session CLASS, so it covers every session this process
    opens — including the ones inside store.py that nobody remembered to
    change. That is the property that makes this a safety net rather than
    another thing to remember.

    Deliberately silent when there is no tenant (startup, migrations, the
    login lookup that runs BEFORE a tenant is known) and on any backend that
    is not PostgreSQL: the test suite runs on SQLite, which has neither
    current_setting nor RLS, and raising there would fail the suite for a
    production-only feature.
    """
    tenant = _current_tenant.get()
    if tenant is None:
        return
    if connection.dialect.name != "postgresql":
        return
    # set_config() rather than SET LOCAL: it takes a bind parameter, so the id
    # cannot be spliced into SQL text. The id is an int from our own token
    # rather than user input, but a security boundary is the last place to
    # rely on that staying true.
    connection.execute(
        text("SELECT set_config(:name, :value, true)"),
        {"name": SETTING, "value": str(tenant)},
    )
