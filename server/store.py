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


def _key(name):
    """The canonical form used to compare client names.

    Case folding happens in PYTHON, not in SQL. Postgres lower() depends on the
    database collation (under LC_CTYPE=C it leaves Greek untouched) and SQLite's
    is ASCII-only — so a SQL-side comparison would match "ΠΑΠΑΔΟΠΟΥΛΟΣ" to
    "Παπαδόπουλος" on one deployment and not another. Client names here are
    overwhelmingly Greek, so that has to be deterministic.
    """
    return (name or "").strip().lower()


def find_client(session, user, name):
    """Case-insensitive lookup within this tenant, mirroring how transactions
    are matched to clients everywhere else."""
    target = _key(name)
    if not target:
        return None
    for client in session.exec(
            select(Client).where(Client.user_id == user.id)).all():
        if _key(client.name) == target:
            return client
    return None


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


def get_client(session, user, client_id):
    """One client, scoped to the tenant. Returns None for another tenant's id,
    so a guessed id is indistinguishable from a missing one."""
    try:
        pk = int(client_id)
    except (TypeError, ValueError):
        return None
    client = session.get(Client, pk)
    if client is None or client.user_id != user.id:
        return None
    return client


def list_clients(session, user, include_archived=True):
    stmt = select(Client).where(Client.user_id == user.id)
    if not include_archived:
        stmt = stmt.where(Client.status == STATUS_ACTIVE)
    return session.exec(stmt.order_by(Client.name)).all()


def create_client(session, user, name, afm=None, contact=None, notes=None):
    client = Client(user_id=user.id, name=(name or "").strip(),
                    status=STATUS_ACTIVE, afm=afm, contact=contact, notes=notes)
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


def update_client(session, user, client_id, **fields):
    """Patch a client. Only keys present in `fields` are touched, so the caller
    can send a partial update without blanking everything else.

    A RENAME also rewrites transactions.client for this client's rows.
    finance.py groups transactions by name, so leaving the denormalised copy
    behind would split the client's history in two on the dashboard: the old
    name would keep its figures and the renamed card would show zero.
    """
    client = get_client(session, user, client_id)
    if client is None:
        return None

    if "name" in fields:
        new_name = (fields["name"] or "").strip()
        if new_name and new_name != client.name:
            old_key = _key(client.name)
            client.name = new_name
            # Rows are matched by the FK when they have one, and by name for
            # rows predating client_id. Comparison is Python-side — see _key.
            for txn in session.exec(
                select(Transaction).where(Transaction.user_id == user.id)
            ).all():
                if txn.client_id == client.id or _key(txn.client) == old_key:
                    txn.client = new_name
                    txn.client_id = client.id
                    session.add(txn)

    for key in ("afm", "contact", "notes"):
        if key in fields:
            value = fields[key]
            setattr(client, key, (value or "").strip() or None)

    if "archived" in fields:
        archived = bool(fields["archived"])
        client.status = STATUS_COMPLETED if archived else STATUS_ACTIVE
        # Stamp the close date on archive; clear it on restore so the row does
        # not look closed while being active.
        client.closed_date = date.today() if archived else None

    session.add(client)
    session.commit()
    session.refresh(client)
    return client


def close_client(session, user, client_id, closed_date=None):
    client = get_client(session, user, client_id)
    if client is None:
        return None
    client.status = STATUS_COMPLETED
    client.closed_date = closed_date or date.today()
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


def get_client_transactions(session, user, client):
    """Every transaction belonging to one client, as finance records.

    Matched by client_id OR by name: rows written before client_id existed (and
    any backfill that could not find a match) still carry only the name, and
    omitting them would under-report the client's totals.
    """
    target = _key(client.name)
    rows = session.exec(
        select(Transaction)
        .where(Transaction.user_id == user.id)
        .order_by(Transaction.id)
    ).all()
    return [t.to_record(user.username) for t in rows
            if t.client_id == client.id or _key(t.client) == target]


# --- Transactions ---------------------------------------------------------
def get_transactions(session, user, client=None):
    rows = session.exec(
        select(Transaction)
        .where(Transaction.user_id == user.id)
        .order_by(Transaction.id)
    ).all()
    if client is not None:
        target = _key(client)
        rows = [t for t in rows if _key(t.client) == target]
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
                       vat_amount=None, vat_rate=None, file_hash=None,
                       client_id=None):
    """Save a transaction.

    `client_id` selects an existing client explicitly (what the UI picker
    sends). Without it the client is resolved — and created if new — from the
    name, which keeps the older name-only callers working.
    """
    row = get_client(session, user, client_id) if client_id else None
    if row is None:
        row = ensure_client(session, user, client)
    txn = Transaction(
        user_id=user.id,
        client_id=row.id,
        # Denormalised from the client row, not from the caller's string, so
        # the two can never disagree.
        client=row.name,
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
