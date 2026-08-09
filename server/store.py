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

import hashlib
import secrets
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlmodel import select

# Pure-stdlib module, no I/O and no config: importing it here keeps the VAT
# arithmetic for a settlement in the one place that owns VAT arithmetic,
# instead of making every caller pre-compute cents it cannot know (the amount
# left owing is only readable once the row has been loaded).
import finance
from server import mfa, subscription
from server.models import (
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    Client,
    DebtPayment,
    Invoice,
    PasswordResetToken,
    Transaction,
    TrustedDevice,
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


class AmountError(ValueError):
    """A figure that cannot be stored as money. Carries a Greek message."""


def money(value, allow_none=True):
    """A euro figure as a plain float, or None.

    Every financial column goes through this on its way into the database, and
    it exists because the values arriving here come from three places that each
    have their own idea of a number: a JSON body (int, float, or a string that
    looks like one), a spreadsheet cell (openpyxl returns int, float, Decimal
    or datetime), and finance.py's own arithmetic. Postgres takes none of those
    interchangeably — a Decimal mixed into float arithmetic raises, a str binds
    as text and fails the numeric cast, and both surface as an opaque insert
    error naming a column rather than a row.

    NaN and infinity are rejected explicitly. Both are ordinary floats to
    Python, both pass a `float()` call without complaint, and Postgres refuses
    them at the column — so without this check they travel all the way to the
    insert and take the whole batch down with them.
    """
    if value is None:
        if allow_none:
            return None
        raise AmountError("Λείπει το ποσό.")
    if isinstance(value, bool):
        # bool is an int subclass, so True would quietly become 1.00 €.
        raise AmountError("Μη έγκυρο ποσό.")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise AmountError(f"Μη έγκυρο ποσό «{value}».")
    if result != result or result in (float("inf"), float("-inf")):
        raise AmountError("Μη έγκυρο ποσό.")
    return round(result, 2)


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
                subscription_status=None, trial_ends_at=None):
    """Insert a new tenant, on a free trial unless told otherwise.

    The trial is the DEFAULT rather than something the caller has to remember:
    a signup that silently skipped it would create an account that is inactive
    from its first request. Callers must check availability first; the unique
    constraints on username/email are the real guard against a race.
    """
    status = subscription.normalize(subscription_status or subscription.TRIALING)
    # A trial with no deadline is not a trial — subscription.resolve reads the
    # missing date as a lapsed one and fails closed. Fill it in rather than
    # write a row that is inactive from its very first request.
    if status == subscription.TRIALING and trial_ends_at is None:
        trial_ends_at = subscription.trial_end()
    user = User(
        username=str(username).strip(),
        email=str(email).strip().lower(),
        password_hash=password_hash,
        subscription_status=status,
        trial_ends_at=trial_ends_at,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def set_subscription_status(session, user, status):
    """Flip a tenant's subscription.

    Going ACTIVE also clears trial_ends_at, and that is load-bearing rather than
    tidying: subscription.resolve() reads a trial date as "this is a trial", so
    leaving one on a paying account would expire them again the moment it passed
    — a paid customer locked out by their own dead trial.
    """
    status = subscription.normalize(status)
    user.subscription_status = status
    if status == subscription.ACTIVE:
        user.trial_ends_at = None
    # A pending cancellation belongs to the subscription that was running when
    # it was requested, so any change of status retires it: going ACTIVE means a
    # new (uncancelled) subscription, and going INACTIVE means the cancellation
    # already happened. Leaving the date behind would show "λήγει στις …" on an
    # account with nothing left to expire.
    user.subscription_cancel_at = None
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def refresh_subscription(session, user, now=None):
    """Resolve the tenant's subscription and write back any drift.

    Called on every database-backed request (server/deps.py), which is what
    makes an expired trial flip itself to `inactive` without a scheduled job:
    the first request after the deadline does it. The write is skipped unless
    the verdict actually differs from the stored column, so the overwhelmingly
    common case — a valid trial, a paid account — costs one SELECT and no
    transaction at all.

    The trial date is deliberately KEPT when a trial lapses: the billing page
    says when it ended, and re-resolving an `inactive` row is stable because an
    explicit inactive wins over any date (subscription.resolve).
    """
    state = subscription.resolve(user.subscription_status, user.trial_ends_at,
                                 now=now)
    if state.needs_persisting:
        user.subscription_status = state.status
        session.add(user)
        session.commit()
        session.refresh(user)
    return state


def set_stripe_customer(session, user, customer_id):
    user.stripe_customer_id = customer_id
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def set_subscription_cancel_at(session, user, cancel_at):
    """Record (or clear, with None) the date a cancellation takes effect.

    Deliberately does NOT touch subscription_status: a subscription cancelling
    at the end of the period is still paid for and still active until then, and
    closing the paywall early would be taking money for days the tenant cannot
    use. The status flips when Stripe says so (customer.subscription.deleted).
    """
    user.subscription_cancel_at = cancel_at
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


# --- Two-factor authentication --------------------------------------------
def set_mfa_secret(session, user, secret):
    """Stage a TOTP secret WITHOUT turning the second factor on.

    Two steps on purpose: enrolment shows a QR code before the user has proved
    they can read it, and treating "has a secret" as "2FA is on" would lock out
    anyone who closed the tab halfway through.
    """
    user.mfa_secret = secret
    user.mfa_enabled = False
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def set_mfa_enabled(session, user, enabled):
    """Turn the second factor on (after a code has verified) or off.

    Switching OFF clears the secret and every trusted device with it. A device
    trusted under the old secret must not silently keep its bypass if 2FA is
    turned back on later — that would be a live bypass nobody remembers
    granting.
    """
    user.mfa_enabled = bool(enabled)
    if not enabled:
        user.mfa_secret = None
        _forget_devices(session, user)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _forget_devices(session, user):
    for row in session.exec(
        select(TrustedDevice).where(TrustedDevice.user_id == user.id)
    ).all():
        session.delete(row)


# --- Trusted devices ------------------------------------------------------
# Everything below reads or writes `trusted_devices`, which is TENANT DATA:
# scripts/enable_rls.sql gives it the same policy as clients and transactions,
# keyed on the app.tenant_id set by server/tenancy.py. So every one of these
# needs a declared tenant, and two of them are reached from the login flow
# BEFORE there is a session to declare one from — server/main.py wraps those in
# tenancy.tenant_scope. Called with no tenant against a database with the
# policies on, the reads find nothing and the writes are refused.


def trust_device(session, user, raw_token, expires_at, label=None):
    """Record a device allowed to skip the 2FA prompt until `expires_at`."""
    session.add(TrustedDevice(
        user_id=user.id,
        token_hash=mfa.hash_device_token(raw_token),
        label=label,
        expires_at=expires_at,
    ))
    session.commit()


def find_trusted_device(session, user, raw_token, now=None):
    """The live trust row for this token AND this user, or None.

    Scoped to the user, which is the check that matters: a token is only ever
    evidence about the account it was issued for. Presenting one account's
    device cookie while logging into another proves nothing, and is treated as
    no cookie at all.

    Expired rows are deleted as they are met rather than merely ignored —
    housekeeping that costs nothing here and keeps a list of dead bypasses
    from accumulating.
    """
    if not raw_token:
        return None
    now = now or utcnow()
    row = session.exec(
        select(TrustedDevice).where(
            TrustedDevice.token_hash == mfa.hash_device_token(raw_token)
        )
    ).first()
    if row is None:
        return None
    if _as_utc(row.expires_at) <= now:
        session.delete(row)
        session.commit()
        return None
    if row.user_id != user.id:
        return None
    row.last_used_at = now
    session.add(row)
    session.commit()
    return row


def list_trusted_devices(session, user):
    return session.exec(
        select(TrustedDevice)
        .where(TrustedDevice.user_id == user.id)
        .order_by(TrustedDevice.created_at.desc())
    ).all()


def revoke_trusted_devices(session, user, device_id=None):
    """Drop one device, or every one. Returns how many went.

    The "all" form is what a user reaches for after losing a laptop, and it is
    the reason these are database rows rather than self-contained signed
    tokens: a signed token stays valid until it expires no matter what its
    owner does about it.
    """
    rows = list_trusted_devices(session, user)
    removed = 0
    for row in rows:
        if device_id is not None and row.id != int(device_id):
            continue
        session.delete(row)
        removed += 1
    if removed:
        session.commit()
    return removed


# --- Password reset -------------------------------------------------------
# How long a reset link stays usable. Short on purpose: the link is a bearer
# credential sitting in an inbox, and the person who asked for it is, by
# definition, at their keyboard right now.
RESET_TOKEN_TTL_MINUTES = 30

# The token is 32 random bytes, urlsafe-encoded. Long enough that guessing is
# not a strategy, short enough to survive an email client wrapping the line.
_RESET_TOKEN_BYTES = 32


def hash_reset_token(token):
    """sha256 of the raw token — what the table actually stores.

    Plain sha256 rather than a password KDF on purpose: this input is 256 bits
    of CSPRNG output, not a human-chosen secret, so there is no dictionary to
    slow an attacker down against and the cost of pbkdf2 would buy nothing.
    """
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def create_reset_token(session, user, now=None):
    """Issue a reset token for `user` and return the RAW value.

    The raw token is returned once, here, and never stored — the caller puts it
    in the link and then it is unrecoverable. Any of the user's earlier
    outstanding tokens are spent first, so asking twice invalidates the first
    email rather than leaving two working keys in an inbox.
    """
    now = now or utcnow()
    for stale in session.exec(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
    ).all():
        stale.used_at = now
        session.add(stale)

    raw = secrets.token_urlsafe(_RESET_TOKEN_BYTES)
    session.add(PasswordResetToken(
        user_id=user.id,
        token_hash=hash_reset_token(raw),
        expires_at=now + timedelta(minutes=RESET_TOKEN_TTL_MINUTES),
    ))
    session.commit()
    return raw


def consume_reset_token(session, raw_token, now=None):
    """Return the User this token belongs to and spend it, or None.

    None covers every failure indistinguishably — unknown, expired, already
    used, or belonging to a deleted account — because the caller must not be
    able to tell those apart. "Already used" would confirm the token was real.

    The token is marked used BEFORE the caller changes the password. If the
    password write then fails the user has to request a new link, which is the
    safe direction to fail: the alternative leaves a spent token usable.
    """
    now = now or utcnow()
    if not raw_token:
        return None
    row = session.exec(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == hash_reset_token(raw_token)
        )
    ).first()
    if row is None or row.used_at is not None:
        return None
    if _as_utc(row.expires_at) <= now:
        return None

    user = session.get(User, row.user_id)
    if user is None:
        return None

    row.used_at = now
    session.add(row)
    session.commit()
    return user


def purge_expired_reset_tokens(session, now=None):
    """Delete spent and expired rows. Housekeeping, not security — the checks
    above already refuse them; this just stops the table growing forever."""
    now = now or utcnow()
    rows = session.exec(select(PasswordResetToken)).all()
    removed = 0
    for row in rows:
        if row.used_at is not None or _as_utc(row.expires_at) <= now:
            session.delete(row)
            removed += 1
    if removed:
        session.commit()
    return removed


def _as_utc(stamp):
    """SQLite hands back a naive datetime for a TIMESTAMPTZ column; comparing
    it against an aware `now` raises rather than expiring anything."""
    if stamp is not None and stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp


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
                       debt_id=None, doc_type=None, due_date=None):
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
        # Coerced here, at the one door every write goes through. `amount` is
        # NOT NULL and non-optional in the model, so a None reaching this line
        # is an insert failure naming a column — see money().
        amount=money(amount, allow_none=False),
        type=type_,
        vat_amount=money(vat_amount),
        vat_rate=None if vat_rate is None else float(vat_rate),
        date=_coerce_date(txn_date),
        description=description,
        source=source,
        file_hash=file_hash,
        doc_number=(doc_number or "").strip() or None,
        counterparty_afm=(counterparty_afm or "").strip() or None,
        doc_type=(doc_type or "").strip() or None,
        due_date=_coerce_date(due_date),
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


# --- Bulk import ----------------------------------------------------------
# The write half of server/imports.py: rows that module has already parsed and
# validated, matched against what the tenant already has and inserted.
#
# Two things separate these from a loop over create_client / create_transaction:
#
#   * ONE transaction for the whole file. Committing per row would leave a
#     failed import half-applied, and a partial book is worse than none — the
#     user cannot tell which half landed without reconciling every row by hand.
#     Everything below therefore adds and flushes, and the caller's
#     session_scope commits once.
#   * ONE pass over the existing rows. The per-row duplicate guards each scan
#     the tenant's whole table, so a 500-row import would be 500 full scans;
#     the maps built here are the same comparisons done once, and they are
#     UPDATED as rows are inserted, which is also what makes a file containing
#     the same client (or the same invoice) twice collapse correctly.
#
# The source column is stamped "Import" so a migrated book stays
# distinguishable from what was typed into the app afterwards.
IMPORT_SOURCE = "Import"


def _client_lookups(session, user):
    """({afm key: Client}, {name key: Client}) for one tenant.

    Both keys are the STRICT duplicate-detection forms (server.text), not
    store._key: an import is exactly the moment "ΝΗΣΙΔΑ CAFE" must land on the
    existing "Νησίδα Café" rather than opening a second card beside it.
    """
    by_afm, by_name = {}, {}
    for client in session.exec(
            select(Client).where(Client.user_id == user.id)).all():
        key = afm_key(client.afm)
        if key:
            by_afm.setdefault(key, client)
        key = name_key(client.name)
        if key:
            by_name.setdefault(key, client)
    return by_afm, by_name


def import_clients(session, user, rows):
    """Insert the clients from a parsed file. Returns (created, skipped).

    A row matching an existing client is SKIPPED, not merged and not refused.
    Merging would silently overwrite details the user has already corrected in
    the app, and refusing the whole upload over a client they happen to have
    already is the opposite of what a migration tool is for — re-uploading the
    same file must be safe, and after this it is a no-op.
    """
    by_afm, by_name = _client_lookups(session, user)
    created, skipped = [], []

    for row in rows:
        afm = afm_key(row.get("afm"))
        name = name_key(row.get("name"))
        clash = (by_afm.get(afm) if afm else None) or (by_name.get(name) if name else None)
        if clash is not None:
            skipped.append({
                "row": row.get("row"),
                "message": f"Υπάρχει ήδη ο πελάτης «{clash.name}».",
            })
            continue

        client = Client(user_id=user.id, name=row["name"], status=STATUS_ACTIVE,
                        afm=row.get("afm"), contact=row.get("contact"),
                        notes=row.get("notes"))
        session.add(client)
        created.append(client)
        # Registered immediately so the SAME file listing a client twice — under
        # two spellings, or once with an ΑΦΜ and once without — inserts it once.
        if afm:
            by_afm.setdefault(afm, client)
        if name:
            by_name.setdefault(name, client)

    if created:
        session.commit()
    return created, skipped


def _invoice_index(session, user):
    """{(doc key, date): [(afm key, name key), ...]} for the tenant's invoices.

    The precomputed form of find_duplicate_transaction's rule: same document
    number, same document date, and EITHER the issuer's ΑΦΜ or the client name
    agreeing. Rows with no document number are omitted — there is nothing to
    match them on, and two same-day invoices from one supplier for the same
    amount are perfectly legitimate.
    """
    index = {}
    for txn in session.exec(
            select(Transaction).where(Transaction.user_id == user.id)).all():
        number = doc_key(txn.doc_number)
        if not number:
            continue
        index.setdefault((number, txn.date), []).append(
            (afm_key(txn.counterparty_afm), name_key(txn.client)))
    return index


def _is_duplicate(index, number, when, afm, name):
    for issuer, client in index.get((number, when), ()):
        if afm and issuer and afm == issuer:
            return True
        if name and client and name == client:
            return True
    return False


def import_transactions(session, user, rows):
    """Insert historic transactions from a parsed file.

    Returns (created, skipped, new_clients) — the transactions written, the
    rows passed over as already on file, and any clients that had to be created
    to hold them.

    Clients are matched by ΑΦΜ first and by name second, which is
    detect_client_duplicate's precedence: the ΑΦΜ is the legal identity, so it
    wins over a name typed three different ways. A row naming a client the book
    does not have CREATES it rather than failing — the common case is importing
    a year of history into an empty account, where every client is new, and
    demanding the client list be imported first would make the feature useless
    on its own.

    Amounts and VAT go through finance.book_amounts, the same function the
    transaction form uses, so an imported row and a typed one storing the same
    document store the same cents.
    """
    by_afm, by_name = _client_lookups(session, user)
    index = _invoice_index(session, user)
    created, skipped, new_clients = [], [], []

    for row in rows:
        afm = afm_key(row.get("afm"))
        name = name_key(row.get("client"))
        number = doc_key(row.get("doc_number"))
        if number and _is_duplicate(index, number, row["date"], afm, name):
            skipped.append({
                "row": row.get("row"),
                "message": f"Το παραστατικό «{row['doc_number']}» υπάρχει ήδη "
                           "καταχωρημένο.",
            })
            continue

        client = (by_afm.get(afm) if afm else None) or (by_name.get(name) if name else None)
        if client is None:
            client = Client(user_id=user.id,
                            # A row identified only by ΑΦΜ still needs a name to
                            # show on its card; the number is the one thing that
                            # certainly identifies it.
                            name=(row.get("client") or f"Α.Φ.Μ. {row['afm']}").strip(),
                            status=STATUS_ACTIVE, afm=row.get("afm"))
            session.add(client)
            # The transaction below needs the foreign key, and the maps need the
            # row to be findable by the time the next line of the file asks.
            session.flush()
            new_clients.append(client)
            if afm:
                by_afm.setdefault(afm, client)
            key = name_key(client.name)
            if key:
                by_name.setdefault(key, client)

        signed, vat_amount = finance.book_amounts(
            row["amount"], row["type"], doc_type=row.get("doc_type"),
            vat_rate=row["vat_rate"], vat_amount=row.get("vat_amount"),
            basis=row.get("basis", "gross"))

        # Checked per row, BEFORE the insert. A figure the database would
        # refuse fails the whole batch at commit — one bad row taking four
        # hundred good ones with it, and reporting itself as a column name.
        # Here it costs that row alone and says which line to look at.
        try:
            signed = money(signed, allow_none=False)
            vat_amount = money(vat_amount)
        except AmountError:
            skipped.append({
                "row": row.get("row"),
                "message": "Δεν ήταν δυνατή η ανάγνωση του ποσού σε αυτή τη "
                           "γραμμή — η κίνηση δεν καταχωρήθηκε.",
            })
            continue
        if signed == 0:
            # A zero-value transaction is not a transaction. Reaching here
            # means the amount column was misread rather than genuinely nil,
            # and importing it would put a row worth nothing in the books.
            skipped.append({
                "row": row.get("row"),
                "message": "Το ποσό της γραμμής είναι μηδενικό — η κίνηση δεν "
                           "καταχωρήθηκε.",
            })
            continue

        session.add(Transaction(
            user_id=user.id,
            client_id=client.id,
            # Denormalised from the client row, not from the file, so the two
            # can never disagree — see create_transaction.
            client=client.name,
            amount=signed,
            type=row["type"],
            vat_amount=vat_amount,
            vat_rate=float(row["vat_rate"]),
            date=row["date"],
            description=row.get("description"),
            source=IMPORT_SOURCE,
            doc_number=row.get("doc_number"),
            counterparty_afm=row.get("afm"),
            doc_type=row.get("doc_type"),
            due_date=row.get("due_date"),
        ))
        created.append(row)
        if number:
            index.setdefault((number, row["date"]), []).append((afm, name_key(client.name)))

    if created or new_clients:
        session.commit()
    return created, skipped, new_clients


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
    _release_transaction_links(session, user, {txn.id})
    session.delete(txn)
    session.commit()
    return True


# --- Bulk selection actions -----------------------------------------------
# Delete and archive over a set of ids, for the checkbox selection in the UI.
#
# Same two properties as the importer above: ONE transaction for the whole
# batch, and one pass over the tenant's rows instead of a full scan per id.
# Each function returns what it could not do alongside what it did — a bulk
# action that silently drops half its input is worse than one that refuses,
# because the user has no way to tell which half.


def _release_transaction_links(session, user, ids):
    """Detach everything pointing AT the transactions about to be deleted.

    Three tables reference a transaction row, and Postgres will refuse the
    DELETE for any one of them left dangling (SQLite quietly would not, which
    is exactly how this would have passed the suite and failed in production):

      * debt_payments.debt_id — the settlement log for a debt being deleted.
        DELETED with it: the log records payments against a debt that is about
        to stop existing, and an entry pointing at nothing is not history, it
        is a broken row that every client balance would still sum.
      * debt_payments.payment_txn_id — the revenue row a partial settlement
        created. NULLED, not deleted: the payment still happened and the debt
        it paid down may well survive, so the log keeps the entry and loses
        only the link.
      * transactions.debt_id — a settlement row pointing back at its debt.
        Nulled for the same reason.
    """
    if not ids:
        return
    for payment in session.exec(
        select(DebtPayment).where(DebtPayment.user_id == user.id)
    ).all():
        if payment.debt_id in ids:
            session.delete(payment)
        elif payment.payment_txn_id in ids:
            payment.payment_txn_id = None
            session.add(payment)
    for txn in session.exec(
        select(Transaction).where(Transaction.user_id == user.id)
    ).all():
        if txn.id not in ids and txn.debt_id in ids:
            txn.debt_id = None
            session.add(txn)
    # Ordered before the rows themselves go, so the database never sees an
    # instant where a foreign key points at a deleted primary key.
    session.flush()


def _as_ids(values):
    """(usable int ids, unusable inputs) — ids arrive from JSON and a caller
    may send anything."""
    good, bad = [], []
    for value in values or ():
        try:
            good.append(int(value))
        except (TypeError, ValueError):
            bad.append(value)
    return good, bad


def delete_transactions(session, user, ids):
    """Delete many transactions. Returns (deleted ids, missing ids).

    `missing` covers an unknown id and ANOTHER TENANT'S id indistinguishably —
    the rows are loaded scoped to this user, so a guessed id simply is not
    found.
    """
    wanted, missing = _as_ids(ids)
    rows = {t.id: t for t in session.exec(
        select(Transaction).where(Transaction.user_id == user.id)).all()}

    targets = []
    for pk in wanted:
        row = rows.get(pk)
        if row is None:
            missing.append(pk)
        else:
            targets.append(row)
    if not targets:
        return [], missing

    doomed = {t.id for t in targets}
    _release_transaction_links(session, user, doomed)
    for row in targets:
        session.delete(row)
    session.commit()
    return sorted(doomed), missing


def delete_clients(session, user, ids):
    """Delete many clients. Returns (deleted, blocked, missing).

    A client that still has transactions is BLOCKED rather than deleted, and
    that is the central decision here. The alternatives are both worse: taking
    the transactions with it destroys booked financial history on a checkbox
    click, and leaving them behind orphans rows that finance.py still counts by
    name — the totals would stay put while the card they belong to vanished,
    which is the kind of discrepancy nobody finds until an audit.

    So deletion is for clients created by mistake, and everything else gets
    archived. `blocked` carries the name and the row count so the UI can say
    which clients need archiving instead.
    """
    wanted, missing = _as_ids(ids)
    clients = {c.id: c for c in session.exec(
        select(Client).where(Client.user_id == user.id)).all()}

    # Rows are attributed by FK when they have one and by name otherwise, the
    # same rule get_client_transactions uses — a transaction predating
    # client_id still belongs to its client, and still blocks the delete.
    by_name = {}
    for client in clients.values():
        key = _key(client.name)
        if key:
            by_name.setdefault(key, client.id)

    counts = {}
    for txn in session.exec(
        select(Transaction).where(Transaction.user_id == user.id)
    ).all():
        owner = (txn.client_id if txn.client_id in clients
                 else by_name.get(_key(txn.client)))
        if owner is not None:
            counts[owner] = counts.get(owner, 0) + 1
    # A settlement log entry references clients.id directly, so it blocks the
    # delete on its own even in the odd case where its transaction has gone.
    for payment in session.exec(
        select(DebtPayment).where(DebtPayment.user_id == user.id)
    ).all():
        if payment.client_id in clients and payment.client_id not in counts:
            counts[payment.client_id] = counts.get(payment.client_id, 0) + 1

    deleted, blocked = [], []
    for pk in wanted:
        client = clients.get(pk)
        if client is None:
            missing.append(pk)
            continue
        used = counts.get(pk, 0)
        if used:
            blocked.append({"id": pk, "name": client.name, "transactions": used})
            continue
        deleted.append({"id": pk, "name": client.name})
        session.delete(client)
    if deleted:
        session.commit()
    return deleted, blocked, missing


def set_clients_archived(session, user, ids, archived=True):
    """Archive or restore many clients. Returns (changed, unchanged, missing).

    `unchanged` is a client already in the requested state — reported rather
    than counted as done, so selecting five clients of which two were already
    archived says "3 archived, 2 already were" instead of a bare 5 that hides
    what happened.

    Archiving is the reversible counterpart to delete: nothing is destroyed,
    the client leaves the active lists (and every total that sums them), and
    the grid's own Αρχειοθετημένοι filter is where it reappears.
    """
    wanted, missing = _as_ids(ids)
    status = STATUS_COMPLETED if archived else STATUS_ACTIVE
    changed, unchanged = [], []

    for pk in wanted:
        client = session.get(Client, pk)
        if client is None or client.user_id != user.id:
            missing.append(pk)
            continue
        if client.status == status:
            unchanged.append({"id": pk, "name": client.name})
            continue
        client.status = status
        # Stamped on archive and cleared on restore, so a row never looks
        # closed while being active — same rule update_client follows.
        client.closed_date = date.today() if archived else None
        session.add(client)
        changed.append({"id": pk, "name": client.name})

    if changed:
        session.commit()
    return changed, unchanged, missing


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
