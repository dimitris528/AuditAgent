"""
PostgreSQL data access — the replacement for airtable_client.py on the serving
path.

The function names and return shapes deliberately mirror the Airtable client
(records as {"id", "createdTime", "fields"} dicts) so server/main.py, auth.py
and the webhook keep their existing call sites and finance.py needs no change
at all. The storage engine swapped; the contract did not.

Every read is scoped by user_id, which is now enforced by a foreign key rather
than by remembering to add a Username filter to each query.
"""

from datetime import date, datetime, timezone

from sqlalchemy import func
from sqlmodel import select

from server.models import (
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    Client,
    Invoice,
    Transaction,
    User,
)


class StoreError(RuntimeError):
    """Database failure the caller should surface as a 502."""


# --- Users ----------------------------------------------------------------
def get_user_by_username(session, username):
    if not username:
        return None
    stmt = select(User).where(func.lower(User.username) == str(username).strip().lower())
    return session.exec(stmt).first()


def get_user_by_email(session, email):
    if not email:
        return None
    stmt = select(User).where(func.lower(User.email) == str(email).strip().lower())
    return session.exec(stmt).first()


def find_user(session, username=None, email=None):
    """Username first, email as fallback — the same precedence the Stripe
    webhook relied on with Airtable."""
    user = get_user_by_username(session, username)
    if user:
        return user
    return get_user_by_email(session, email)


def get_user_by_stripe_customer(session, customer_id):
    if not customer_id:
        return None
    stmt = select(User).where(User.stripe_customer_id == customer_id)
    return session.exec(stmt).first()


def create_user(session, username, email, password_hash,
                subscription_status="Active", trial_expiry=None):
    """Insert a new tenant. Callers must check availability first; the unique
    constraints on username/email are the real guard against a race."""
    user = User(
        username=str(username).strip(),
        email=str(email).strip().lower(),
        password_hash=password_hash,
        subscription_status=subscription_status,
        trial_expiry=trial_expiry,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def set_subscription_status(session, user, status):
    """Flip a tenant's subscription. Setting Active also clears trial_expiry,
    matching the Airtable behaviour: activation follows a PAID checkout, and a
    stale trial date would flip the paying account straight back to inactive."""
    user.subscription_status = status
    if status == "Active":
        user.trial_expiry = None
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def set_stripe_customer(session, user, customer_id):
    user.stripe_customer_id = customer_id
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def update_password(session, user, password_hash):
    user.password_hash = password_hash
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


# --- Clients (the old Airtable "Projects") --------------------------------
def get_active_projects(session, user):
    stmt = (select(Client)
            .where(Client.user_id == user.id, Client.status == STATUS_ACTIVE)
            .order_by(Client.name))
    return [c.to_record(user.username) for c in session.exec(stmt).all()]


def get_completed_projects(session, user):
    stmt = (select(Client)
            .where(Client.user_id == user.id, Client.status == STATUS_COMPLETED)
            .order_by(Client.name))
    return [c.to_record(user.username) for c in session.exec(stmt).all()]


def find_client(session, user, name):
    """Case-insensitive lookup within this tenant, mirroring how transactions
    are matched to clients everywhere else."""
    target = (name or "").strip().lower()
    if not target:
        return None
    stmt = select(Client).where(
        Client.user_id == user.id, func.lower(Client.name) == target)
    return session.exec(stmt).first()


def ensure_client(session, user, name):
    """Return this tenant's client row, creating it if the name is new.

    Airtable required the client to be created before a transaction could
    reference it; creating on demand means a first transaction for a new client
    just works, and the dashboard shows a card for it immediately.
    """
    existing = find_client(session, user, name)
    if existing:
        return existing
    client = Client(user_id=user.id, name=(name or "").strip(), status=STATUS_ACTIVE)
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


def close_client(session, user, client_id, closed_date=None):
    client = session.get(Client, client_id)
    if client is None or client.user_id != user.id:
        return None
    client.status = STATUS_COMPLETED
    client.closed_date = closed_date or date.today()
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


# --- Transactions ---------------------------------------------------------
def get_transactions(session, user, client=None):
    stmt = select(Transaction).where(Transaction.user_id == user.id)
    if client is not None:
        stmt = stmt.where(func.lower(Transaction.client) == client.strip().lower())
    rows = session.exec(stmt.order_by(Transaction.id)).all()
    return [t.to_record(user.username) for t in rows]


def get_transaction(session, user, record_id):
    """Fetch one row, scoped to the tenant. Returns None for another tenant's
    id, so a guessed id is indistinguishable from a missing one."""
    try:
        pk = int(record_id)
    except (TypeError, ValueError):
        return None
    txn = session.get(Transaction, pk)
    if txn is None or txn.user_id != user.id:
        return None
    return txn


def create_transaction(session, user, client, amount, description=None,
                       txn_date=None, type_=None, source=None,
                       vat_amount=None, vat_rate=None, file_hash=None):
    ensure_client(session, user, client)
    txn = Transaction(
        user_id=user.id,
        client=(client or "").strip(),
        amount=amount,
        type=type_,
        vat_amount=vat_amount,
        vat_rate=vat_rate,
        date=_coerce_date(txn_date),
        description=description,
        source=source,
        file_hash=file_hash,
    )
    session.add(txn)
    session.commit()
    session.refresh(txn)
    return txn


def resolve_debt(session, user, record_id, paid_date=None,
                 vat_amount=None, vat_rate=None):
    """Εξόφληση: flip a Χρεωστούμενο row to realised revenue and stamp its VAT.
    The amount is already positive, so the type flip alone moves it out of the
    debt KPI and into that client's revenue."""
    txn = get_transaction(session, user, record_id)
    if txn is None:
        return None
    txn.type = "Έσοδο"
    txn.date = _coerce_date(paid_date) or date.today()
    if vat_amount is not None:
        txn.vat_amount = vat_amount
    if vat_rate is not None:
        txn.vat_rate = vat_rate
    session.add(txn)
    session.commit()
    session.refresh(txn)
    return txn


def delete_transaction(session, user, record_id):
    txn = get_transaction(session, user, record_id)
    if txn is None:
        return False
    session.delete(txn)
    session.commit()
    return True


# --- Invoices -------------------------------------------------------------
def get_invoices(session, user):
    stmt = (select(Invoice).where(Invoice.user_id == user.id)
            .order_by(Invoice.issue_date.desc(), Invoice.id.desc()))
    return session.exec(stmt).all()


# --- helpers --------------------------------------------------------------
def _coerce_date(value):
    """Accept a date, datetime or ISO-ish string; return a date or None.

    Kept permissive on purpose: an unparseable date drops to None rather than
    failing the whole write, which is how the Airtable client behaved.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def utcnow():
    return datetime.now(timezone.utc)
