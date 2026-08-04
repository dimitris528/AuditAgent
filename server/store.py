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

# Pure-stdlib module, no I/O and no config: importing it here keeps the VAT
# arithmetic for a settlement in the one place that owns VAT arithmetic,
# instead of making every caller pre-compute cents it cannot know (the amount
# left owing is only readable once the row has been loaded).
import finance
from server.models import (
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    Client,
    DebtPayment,
    Invoice,
    Transaction,
    User,
)
from server.text import afm_key, doc_key, name_key

# "full" pays the last cent owed; "partial" leaves a balance behind.
SETTLEMENT_FULL = "full"
SETTLEMENT_PARTIAL = "partial"

# Half a cent — the tolerance euro amounts are compared with, so a payment of
# 499.999999 against a 500.00 debt settles it instead of leaving a phantom
# tenth of a cent outstanding forever.
_CENT = 0.005


class StoreError(RuntimeError):
    """Database failure the caller should surface as a 502."""


class SettlementError(ValueError):
    """A settlement that cannot be applied (wrong row type, already paid, or
    an amount larger than the balance). Carries a user-facing Greek message."""


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
    """Look a client up by name, case- AND accent-insensitively.

    Uses the strict duplicate key (server.text.name_key) rather than _key, so
    "ΝΗΣΙΔΑ CAFE" finds the existing "Νησίδα Café" instead of opening a second
    card for the same company. Comparison stays Python-side for the reason in
    _key: SQL lower() is collation-dependent on Greek.
    """
    target = name_key(name)
    if not target:
        return None
    for client in session.exec(
            select(Client).where(Client.user_id == user.id)).all():
        if name_key(client.name) == target:
            return client
    return None


def find_client_by_afm(session, user, afm):
    """Look a client up by ΑΦΜ, ignoring formatting and an EL/GR prefix."""
    target = afm_key(afm)
    if not target:
        return None
    for client in session.exec(
            select(Client).where(Client.user_id == user.id)).all():
        if afm_key(client.afm) == target:
            return client
    return None


def detect_client_duplicate(session, user, name=None, afm=None, exclude_id=None):
    """Return (client, reason) for the first existing client this one collides
    with, or (None, None).

    ΑΦΜ is checked FIRST and is the stronger signal: it is the legal identifier,
    so two rows carrying the same one are the same company however differently
    they are spelled. The name check is the fallback for the many clients
    entered without an ΑΦΜ at all.

    `exclude_id` is the client being edited — a rename must not collide with
    itself.
    """
    if afm:
        match = find_client_by_afm(session, user, afm)
        if match is not None and match.id != exclude_id:
            return match, "afm"
    if name:
        match = find_client(session, user, name)
        if match is not None and match.id != exclude_id:
            return match, "name"
    return None, None


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
                       client_id=None, doc_number=None, counterparty_afm=None,
                       debt_id=None):
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
        doc_number=(doc_number or "").strip() or None,
        counterparty_afm=(counterparty_afm or "").strip() or None,
        debt_id=debt_id,
    )
    session.add(txn)
    session.commit()
    session.refresh(txn)
    return txn


def find_duplicate_transaction(session, user, doc_number, counterparty_afm=None,
                               client_name=None, txn_date=None, exclude_id=None):
    """The invoice already on file, if this one is a re-entry of it.

    Keyed on (issuer, document number, document date) — the triple that
    identifies a Greek invoice. The issuer is matched by ΑΦΜ when one was given
    and by client name otherwise, and EITHER agreeing is enough, so the guard
    still fires for the many rows typed without an ΑΦΜ.

    With no document number there is nothing to match on and the check is
    skipped rather than guessed at: two same-day invoices from one supplier for
    the same amount are perfectly legitimate, and refusing the second would be
    worse than missing a duplicate.
    """
    number = doc_key(doc_number)
    if not number:
        return None
    issuer = afm_key(counterparty_afm)
    by_name = name_key(client_name)
    when = _coerce_date(txn_date)
    for txn in session.exec(
            select(Transaction).where(Transaction.user_id == user.id)).all():
        if txn.id == exclude_id or doc_key(txn.doc_number) != number:
            continue
        if txn.date != when:
            continue
        if issuer and afm_key(txn.counterparty_afm) == issuer:
            return txn
        if by_name and name_key(txn.client) == by_name:
            return txn
    return None


# --- Debt settlement ------------------------------------------------------
def settle_debt(session, user, record_id, amount=None, vat_rate=None,
                paid_date=None, note=None):
    """Εξόφληση: pay a Χρεωστούμενο row down by `amount` (default: all of it).

    Cash-basis, which is the convention the rest of the book already follows:
    money becomes revenue on the day it is received, and whatever is still owed
    stays a debt.

      * A FULL payment flips the debt row itself into "Έσοδο" and stamps its
        VAT — byte for byte what Εξόφληση did before partial payments existed,
        so one-click settlement is unchanged.
      * A PARTIAL payment shrinks the debt row by the amount paid and books a
        SEPARATE revenue row for the part that was, linked back by debt_id. A
        €500 debt paid €200 leaves a €300 debt plus €200 of realised revenue.

    finance.py needs no change for either: it reads the debt KPI off the
    amounts of the rows still typed Χρεωστούμενο, so shrinking one lowers the
    outstanding balance automatically, and every client figure recalculates
    from the same single pass.

    Returns (transaction, payment) — the debt row as it now stands and the log
    entry — or None when the id belongs to no row of this tenant. Raises
    SettlementError for a row that cannot be settled.
    """
    txn = get_transaction(session, user, record_id)
    if txn is None:
        return None
    if (txn.type or "").strip() != finance.DEBT_TYPE:
        raise SettlementError("Η κίνηση δεν είναι χρεωστούμενο.")

    remaining = round(float(txn.amount or 0), 2)
    if remaining <= 0:
        raise SettlementError("Το χρεωστούμενο έχει ήδη εξοφληθεί.")

    paid = remaining if amount is None else round(float(amount), 2)
    if paid <= 0:
        raise SettlementError("Το ποσό εξόφλησης πρέπει να είναι μεγαλύτερο από 0.")
    if paid > remaining + _CENT:
        raise SettlementError(
            f"Το ποσό υπερβαίνει το υπόλοιπο του χρέους ({remaining:.2f} €).")

    when = _coerce_date(paid_date) or date.today()
    # The rate the payment is taxed at: the one sent, else the rate the debt
    # was recorded with, else the standard rate.
    rate = vat_rate
    if rate is None:
        rate = txn.vat_rate if txn.vat_rate is not None else finance.DEFAULT_VAT_RATE
    # Revenue orientation, exactly as a manually entered Έσοδο would be.
    vat_amount = finance.vat_for_write(paid, True, rate)

    full = paid >= remaining - _CENT
    left = 0.0 if full else round(remaining - paid, 2)

    if full:
        # Absorb the sub-cent tolerance so the books balance to the last cent.
        paid = remaining
        vat_amount = finance.vat_for_write(paid, True, rate)
        txn.type = "Έσοδο"
        txn.amount = remaining
        txn.date = when
        txn.vat_amount = vat_amount
        txn.vat_rate = rate
        session.add(txn)
        payment_txn = txn
    else:
        txn.amount = left
        session.add(txn)
        payment_txn = Transaction(
            user_id=user.id,
            client_id=txn.client_id,
            client=txn.client,
            amount=paid,                 # positive: realised revenue
            type="Έσοδο",
            vat_amount=vat_amount,
            vat_rate=rate,
            date=when,
            description=note or f"Μερική εξόφληση χρέους #{txn.id}",
            source="Settlement",
            # The document number is deliberately NOT copied: this row is a
            # payment against the invoice, not a second copy of it, and
            # duplicating the number would make the invoice look re-entered.
            counterparty_afm=txn.counterparty_afm,
            debt_id=txn.id,
        )
        session.add(payment_txn)

    # Assign the new row its primary key without ending the transaction, so
    # the log entry below can reference it and all three writes still commit
    # together (or not at all).
    session.flush()

    payment = DebtPayment(
        user_id=user.id,
        debt_id=txn.id,
        payment_txn_id=payment_txn.id,
        client_id=txn.client_id,
        client=txn.client,
        amount=paid,
        remaining=left,
        vat_amount=vat_amount,
        vat_rate=rate,
        paid_date=when,
        kind=SETTLEMENT_FULL if full else SETTLEMENT_PARTIAL,
        note=(note or "").strip() or None,
    )
    session.add(payment)
    session.commit()
    session.refresh(txn)
    session.refresh(payment)
    return txn, payment


def get_debt_payments(session, user, client=None, debt_id=None):
    """The settlement log, newest first — for one debt, one client, or all."""
    stmt = select(DebtPayment).where(DebtPayment.user_id == user.id)
    if debt_id is not None:
        try:
            stmt = stmt.where(DebtPayment.debt_id == int(debt_id))
        except (TypeError, ValueError):
            return []
    rows = session.exec(
        stmt.order_by(DebtPayment.paid_date.desc(), DebtPayment.id.desc())).all()
    if client is not None:
        # Matched by FK or by name, for the same reason
        # get_client_transactions is: rows can predate the association.
        target = _key(client.name)
        rows = [r for r in rows
                if r.client_id == client.id or _key(r.client) == target]
    return rows


def paid_by_debt(payments):
    """{debt_id: total paid so far} — what the drawer shows next to a debt that
    has been partly settled."""
    totals = {}
    for row in payments:
        totals[row.debt_id] = round(totals.get(row.debt_id, 0.0) + row.amount, 2)
    return totals


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
