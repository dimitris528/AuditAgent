"""
Shared FastAPI dependencies: who is calling, and what they are allowed to do.

Lifted out of server/main.py so the billing router (server/billing.py) can reuse
the same auth and the same subscription gate without importing main and creating
a cycle. main.py re-exports these under its old private names, so every existing
call site is unchanged.

The subscription gate
---------------------
resolve_user() is called by EVERY database-backed endpoint, read or write. That
is what makes trial expiry self-healing without a cron job: the first request
after the deadline resolves the account, sees the date has passed and persists
`inactive`.

Writes then fail with **402 Payment Required** — not 401, which the frontend
treats as "log in again", and not 403, which the browser and our own fetch
wrappers give no reason to handle specially. 402 is unambiguous here: the
session is perfectly valid, the account simply has not paid. Its body carries
`billing_url` so the UI can send the user somewhere useful rather than only
apologising.

Reads stay open on purpose. An expired tenant still owns their books; locking
them out of their own figures is a hostage situation, not a paywall. What they
lose is the ability to add to them.
"""

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import auth
from server import database, store, tenancy

_bearer = HTTPBearer(auto_error=False)

#: Where an unpaid tenant is sent. Relative — the frontend owns its own routing.
BILLING_PATH = "/billing"

_PAYWALL_MESSAGE = (
    "Η δοκιμαστική περίοδος έληξε. Ενεργοποιήστε συνδρομή για να "
    "καταχωρείτε νέες εγγραφές."
)


def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """The tenant key (the JWT `sub`). Never read from a body or a query param,
    so one user can only ever reach their own rows."""
    if cred is None or not cred.credentials:
        raise HTTPException(status_code=401, detail="Απαιτείται σύνδεση.")
    try:
        payload = auth.decode_token(cred.credentials)
    except auth.AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Μη έγκυρη συνεδρία.")
    return sub


def require_db():
    """Fail with 503 when the request needs a database and there is none."""
    if not database.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Η βάση δεδομένων δεν έχει ρυθμιστεί (ορίστε DATABASE_URL).")


def paywall_detail(state):
    """The 402 body. Structured rather than a bare string so the UI can redirect
    on `billing_url` instead of pattern-matching a Greek sentence."""
    return {
        "message": _PAYWALL_MESSAGE,
        "code": "subscription_inactive",
        "billing_url": BILLING_PATH,
        "subscription": state.to_dict(),
    }


def resolve_user_state(session, username, write=False):
    """(User, SubscriptionState) for the JWT subject.

    Raises 401 when the token outlived the account — a token must never resolve
    to nothing and be treated as harmless — and 402 when `write` is set and the
    subscription has lapsed.

    Also the single place the DATABASE-level tenant is declared. Every
    endpoint reaches its data through here, so stamping the tenant at this
    point means the Row Level Security policies (scripts/enable_rls.sql) are
    fed by the same resolution that decides tenancy in Python — one source of
    truth, checked twice.
    """
    user = store.get_user_by_username(session, username)
    if user is None:
        raise HTTPException(status_code=401, detail="Ο λογαριασμός δεν βρέθηκε.")
    # Set BEFORE any tenant-scoped query runs. From here on a query that
    # forgets its user_id filter returns nothing instead of somebody else's
    # rows — see server/tenancy.py.
    tenancy.set_current_tenant(user.id)
    state = store.refresh_subscription(session, user)
    if write and not state.allows_writes:
        raise HTTPException(status_code=402, detail=paywall_detail(state))
    return user, state


def resolve_user(session, username, write=False):
    """Just the tenant's row — the shape every existing call site expects."""
    return resolve_user_state(session, username, write=write)[0]


def subscription_state(session, username):
    """The tenant plus their resolved subscription, gating nothing — what the
    billing page reads."""
    return resolve_user_state(session, username)

