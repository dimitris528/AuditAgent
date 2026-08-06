"""
SQLModel tables backing the accounting SaaS on PostgreSQL (Supabase).

Replaces the Airtable base described in airtable_client.py. The column names
are snake_case SQL rather than Airtable's Title Case, and tenancy is now a real
foreign key (transactions.user_id) instead of a repeated Username string.

to_record() adapters
--------------------
finance.py is the single source of truth for the VAT/tax math and consumes
Airtable-shaped dicts: {"id": ..., "createdTime": ..., "fields": {...}}. Rather
than rewrite that module — and risk the summation guarantee that total VAT ==
Σ per-client VATs — each model can emit exactly that shape. finance.py is
therefore untouched by the database migration, and its behaviour is unchanged
by construction rather than by re-testing.
"""

# Imported as a module rather than `from datetime import date`: the
# transactions table has a column literally named `date`, and a bare `date`
# annotation on a field of the same name resolves to that field instead of the
# type, which fails schema generation with a confusing NoneType annotation.
import datetime as dt
from typing import Optional

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from server import subscription


def _tstz(nullable=True):
    """A TIMESTAMPTZ column.

    SQLModel maps datetime to TIMESTAMP WITHOUT TIME ZONE by default, which
    would silently drop the offset from the tz-aware UTC values written here
    and make every stored instant ambiguous. Since create_all() never ALTERs an
    existing table, getting this wrong is only fixable by a manual migration —
    so it is pinned explicitly.
    """
    return Column(DateTime(timezone=True), nullable=nullable)

# Mirrors the Airtable single-select options the UI and finance.py expect.
STATUS_ACTIVE = "Active"
STATUS_COMPLETED = "Completed"


def _utcnow():
    return dt.datetime.now(dt.timezone.utc)


def _iso_z(stamp):
    """Render a datetime the way Airtable rendered createdTime, because
    finance.txn_date() falls back to parsing that string."""
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    # Login identity. Unique and stored lower-cased by the callers so lookups
    # are case-insensitive without needing a functional index.
    email: str = Field(index=True, unique=True, max_length=320)
    # Kept alongside email even though it is not in the original spec: the JWT
    # subject, every pre-existing token, and the Airtable rows being backfilled
    # all key tenancy on username. Dropping it would orphan existing data and
    # invalidate every live session.
    username: str = Field(index=True, unique=True, max_length=120)
    password_hash: str = Field(max_length=255)
    # "trialing" | "active" | "inactive" — see server/subscription.py, which
    # owns what each one is allowed to do. The default fails CLOSED: a row
    # inserted without a status has no trial date either, so it is not a trial,
    # and calling it Active would hand out a free account by omission.
    subscription_status: str = Field(default=subscription.INACTIVE, max_length=32)
    stripe_customer_id: Optional[str] = Field(default=None, index=True, max_length=128)
    # When the free trial runs out. Set at registration and CLEARED on payment:
    # its presence is what marks the account as a trial (subscription.resolve),
    # so a stale date on a paying account would flip them back to inactive.
    # Renamed from `trial_expiry` — see the guarded rename in database.py.
    trial_ends_at: Optional[dt.datetime] = Field(default=None, sa_column=_tstz())
    # When a subscription the tenant has asked to cancel will actually end.
    #
    # Set while the account is still ACTIVE: Stripe cancels at the close of the
    # period already paid for, so this is "pending cancellation", not
    # "cancelled", and the paywall stays open until the date passes. Cleared
    # whenever the account changes state — a fresh checkout, or the
    # cancellation finally landing — so it never outlives the decision it
    # records. Stripe remains the source of truth (server/webhooks.py syncs it
    # both ways, including cancellations made in Stripe's own portal).
    subscription_cancel_at: Optional[dt.datetime] = Field(default=None,
                                                          sa_column=_tstz())
    created_at: dt.datetime = Field(default_factory=_utcnow,
                                    sa_column=_tstz(nullable=False))


class PasswordResetToken(SQLModel, table=True):
    """One outstanding "forgot my password" request.

    The token is stored HASHED, exactly like a password, and for the same
    reason: this table is a list of live account-takeover keys, so anyone who
    reads the database — a backup, a log, an errant SELECT — must not be able to
    use what they find. The plaintext exists only in the reset link, and only
    the holder of that link can present it.

    Single-use and short-lived, both enforced in store.consume_reset_token:
    `used_at` is stamped the moment a token is spent so a leaked link cannot be
    replayed, and `expires_at` bounds the window in which a forgotten,
    unclicked email is dangerous.
    """

    __tablename__ = "password_reset_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    # sha256 of the token that went out in the link. Indexed because lookup is
    # BY this value — there is nothing else to find the row by.
    token_hash: str = Field(index=True, unique=True, max_length=64)
    expires_at: dt.datetime = Field(sa_column=_tstz(nullable=False))
    used_at: Optional[dt.datetime] = Field(default=None, sa_column=_tstz())
    created_at: dt.datetime = Field(default_factory=_utcnow,
                                    sa_column=_tstz(nullable=False))


class Client(SQLModel, table=True):
    """A billable client — the Airtable "Projects" table, renamed to match what
    the UI has called it since the VAT refactor (Πελάτης).

    Not in the requested model list, but build_dashboard() takes active and
    completed clients as its first two arguments: without this table the
    dashboard has no cards, no per-client VAT and no tax bars.
    """

    __tablename__ = "clients"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    name: str = Field(max_length=200)
    status: str = Field(default=STATUS_ACTIVE, index=True, max_length=32)
    closed_date: Optional[dt.date] = Field(default=None)
    # Α.Φ.Μ. — the Greek tax registration number. Text, not a number: it is an
    # identifier, and leading zeros are significant.
    afm: Optional[str] = Field(default=None, max_length=32)
    contact: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=2000)
    created_at: dt.datetime = Field(default_factory=_utcnow,
                                    sa_column=_tstz(nullable=False))

    def to_record(self, username):
        return {
            "id": str(self.id),
            "createdTime": _iso_z(self.created_at),
            "fields": {
                "Name": self.name,
                "Username": username,
                "Status": self.status,
                "ClosedDate": self.closed_date.isoformat() if self.closed_date else None,
                # Carried into the finance record shape so the dashboard's
                # client cards can be searched by Α.Φ.Μ. without a second
                # round trip per card.
                "AFM": self.afm,
            },
        }

    def to_detail(self):
        """Full client record for the detail drawer (not the finance record
        shape — this one is consumed by the UI directly)."""
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "archived": self.status == STATUS_COMPLETED,
            "afm": self.afm,
            "contact": self.contact,
            "notes": self.notes,
            "closed_date": self.closed_date.isoformat() if self.closed_date else None,
            "created_at": _iso_z(self.created_at),
        }


class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    # The authoritative link to a client. Nullable because rows backfilled from
    # Airtable may name a client that never had a Projects row, and losing those
    # transactions would be worse than leaving the link unset.
    client_id: Optional[int] = Field(default=None, foreign_key="clients.id", index=True)
    # The client NAME is kept alongside the FK, denormalised on purpose:
    # finance.py groups transactions by name (case-insensitively) and that is
    # the module whose VAT-summation guarantees we do not want to touch. Renames
    # therefore update both — see store.update_client.
    client: str = Field(index=True, max_length=200)
    # SIGNED, exactly as in Airtable: revenue/debt positive, expense negative.
    # finance.py reads the sign, so flipping this convention silently inverts
    # every figure on the dashboard.
    amount: float
    # "Έσοδο" | "Έξοδο" | "Χρεωστούμενο"
    type: Optional[str] = Field(default=None, max_length=32)
    vat_amount: Optional[float] = Field(default=None)
    vat_rate: Optional[float] = Field(default=None)
    date: Optional[dt.date] = Field(default=None, index=True)
    description: Optional[str] = Field(default=None, max_length=500)
    source: Optional[str] = Field(default=None, max_length=32)
    file_hash: Optional[str] = Field(default=None, index=True, max_length=64)
    # --- Invoice identity -------------------------------------------------
    # The document number and the issuer's ΑΦΜ, filled in by hand or by the OCR
    # scanner. Together with `date` these are what the duplicate guard keys on:
    # one invoice can only be entered once.
    doc_number: Optional[str] = Field(default=None, index=True, max_length=64)
    counterparty_afm: Optional[str] = Field(default=None, index=True, max_length=32)
    # Τύπος παραστατικού — "Τιμολόγιο Πώλησης" | "ΑΠΥ" | "Πιστωτικό" |
    # "Δαπάνη/Έξοδο" | "Λειτουργικό Έξοδο" (finance.DOC_TYPES).
    doc_type: Optional[str] = Field(default=None, index=True, max_length=64)
    # Χρεωστούμενα only: when payment falls due. Nullable, and NOT backfilled —
    # finance.debt_due_date infers a due date from the issue date plus the
    # standard terms, so rows written before this column existed still age.
    due_date: Optional[dt.date] = Field(default=None, index=True)
    # Set on the revenue rows a PARTIAL settlement creates, pointing back at the
    # Χρεωστούμενο row they paid down, so a debt's history can be reconstructed
    # from the transactions alone.
    debt_id: Optional[int] = Field(default=None, foreign_key="transactions.id",
                                   index=True)
    created_at: dt.datetime = Field(default_factory=_utcnow,
                                    sa_column=_tstz(nullable=False))

    def to_record(self, username):
        return {
            "id": str(self.id),
            "createdTime": _iso_z(self.created_at),
            "fields": {
                "Username": username,
                "Category": self.client,
                "Amount": self.amount,
                "Type": self.type,
                "VAT_Amount": self.vat_amount,
                "VAT_Rate": self.vat_rate,
                "Date": self.date.isoformat() if self.date else None,
                "Description": self.description,
                "Source": self.source,
                "FileHash": self.file_hash,
                "DocNumber": self.doc_number,
                "CounterpartyAFM": self.counterparty_afm,
                "DocType": self.doc_type,
                "DueDate": self.due_date.isoformat() if self.due_date else None,
                "DebtId": self.debt_id,
            },
        }


class DebtPayment(SQLModel, table=True):
    """One payment made against a Χρεωστούμενο row — the settlement log.

    Its own table rather than something inferred from the transactions,
    because a FULL settlement flips the debt row itself into "Έσοδο" (exactly
    what Εξόφληση has always done). Once that happens the row no longer says it
    was ever a debt, so without this log the history of a settled debt would be
    gone. Every payment — partial or final — writes one row here.

    `remaining` is stored rather than derived: it is what was still owed at the
    moment of that payment, and recomputing it later from a running total would
    silently change historical rows if an earlier payment were ever corrected.
    """

    __tablename__ = "debt_payments"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    # The Χρεωστούμενο row being paid down.
    debt_id: int = Field(foreign_key="transactions.id", index=True)
    # The revenue row this payment produced: a newly created one for a partial
    # payment, or the flipped debt row itself for the final one.
    payment_txn_id: Optional[int] = Field(default=None,
                                          foreign_key="transactions.id", index=True)
    client_id: Optional[int] = Field(default=None, foreign_key="clients.id", index=True)
    client: str = Field(max_length=200)
    # Positive magnitude paid, and what was left owing AFTER it.
    amount: float
    remaining: float
    vat_amount: Optional[float] = Field(default=None)
    vat_rate: Optional[float] = Field(default=None)
    paid_date: Optional[dt.date] = Field(default=None, index=True)
    # "full" | "partial"
    kind: str = Field(default="partial", max_length=16)
    note: Optional[str] = Field(default=None, max_length=500)
    created_at: dt.datetime = Field(default_factory=_utcnow,
                                    sa_column=_tstz(nullable=False))

    def to_detail(self):
        return {
            "id": self.id,
            "debt_id": self.debt_id,
            "payment_txn_id": self.payment_txn_id,
            "client": self.client,
            "amount": round(self.amount, 2),
            "remaining": round(self.remaining, 2),
            "vat_amount": self.vat_amount,
            "vat_rate": self.vat_rate,
            "paid_date": self.paid_date.isoformat() if self.paid_date else None,
            "kind": self.kind,
            "note": self.note,
            "created_at": _iso_z(self.created_at),
        }


class Invoice(SQLModel, table=True):
    """Issued invoices. New surface — Airtable had no equivalent table, so
    nothing is backfilled here and no endpoint writes to it yet; it is created
    so the schema is in place for the invoicing UI.
    """

    __tablename__ = "invoices"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    number: str = Field(index=True, max_length=64)
    client: str = Field(max_length=200)
    amount: float
    vat_amount: Optional[float] = Field(default=None)
    vat_rate: Optional[float] = Field(default=None)
    issue_date: Optional[dt.date] = Field(default=None)
    due_date: Optional[dt.date] = Field(default=None)
    # "draft" | "sent" | "paid" | "void"
    status: str = Field(default="draft", index=True, max_length=32)
    description: Optional[str] = Field(default=None, max_length=500)
    created_at: dt.datetime = Field(default_factory=_utcnow,
                                    sa_column=_tstz(nullable=False))
