"""
Provision (or refresh) the public demo account and fill it with sample books.

WHAT IT CREATES
---------------
One tenant, `demo@auditagent.io` / `DemoPass2026!`, with a handful of clients
and a few months of transactions: sales invoices, service receipts, expenses,
a credit note and two open Χρεωστούμενα (one of them overdue, so the debt
alerts have something to show). Dates are relative to TODAY, so the dashboard's
current period is never empty however long after seeding someone looks.

The subscription is `active` with no trial date — the same state
create_admin.py writes, for the same reason: a trial would lock the demo to
read-only fourteen days after it was seeded.

RE-RUNNING IS A RESET, AND THAT IS THE POINT
--------------------------------------------
The credentials are public (they are on the login page), so anyone can change
this account's data, turn on 2FA and lock everybody else out, or archive every
client. Running the script again puts the account back exactly as described
above: password reset, 2FA off, trusted devices forgotten, and the demo
tenant's clients, transactions and settlement log replaced.

Only rows belonging to the demo user are touched. Every delete below is keyed
on its user_id, and an existing account under the demo email that is NOT the
demo username is refused rather than wiped.

HOW THE ROWS ARE WRITTEN
------------------------
Through finance.book_amounts and store.create_transaction — the same path the
transaction form uses — so the sign convention, the net→gross conversion and
the VAT rounding are the real ones, and the demo dashboard reconciles exactly
like a real tenant's (total VAT == Σ per-client VAT).

USAGE
-----
    python scripts/seed_demo.py          # asks before writing
    python scripts/seed_demo.py --yes    # no prompt (cron / deploy hook)

Requires DATABASE_URL in the environment (.env is loaded via config.py).
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

# Allow running as `python scripts/seed_demo.py` from the repo root without
# installing the project.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, update  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

import config  # noqa: F401,E402  (loads .env)
import finance  # noqa: E402
import passwords  # noqa: E402
from server import database, store, subscription, tenancy  # noqa: E402
from server.demo_account import DEMO_EMAIL, DEMO_USERNAME  # noqa: E402,F401
from server.models import (Client, DebtPayment, Transaction,  # noqa: E402
                           TrustedDevice)

# Public by design: the login page's demo button sends exactly this. It is not
# a secret and must never be reused for anything that is.
DEMO_PASSWORD = "DemoPass2026!"
DEMO_COMPANY = "Demo Λογιστικό Γραφείο"
DEMO_FULL_NAME = "Demo Χρήστης"

REVENUE = "Έσοδο"
EXPENSE = "Έξοδο"

#: (name, ΑΦΜ, contact) — ΑΦΜ values are fictitious nine-digit placeholders.
CLIENTS = (
    ("Αλφα Κατασκευαστική Α.Ε.", "800000001", "info@alpha.example.gr"),
    ("Βήτα Εμπορική Ο.Ε.", "800000002", "logistirio@beta.example.gr"),
    ("Γάμμα Τεχνολογίες Ι.Κ.Ε.", "800000003", "accounts@gamma.example.gr"),
    ("Δέλτα Εστίαση", "800000004", None),
)

#: (days ago, client index, type, doc type, NET amount, VAT rate, doc number,
#:  description, due date as days from today — Χρεωστούμενα only; negative
#:  means already overdue)
TRANSACTIONS = (
    (95, 0, REVENUE, finance.DOC_SALES_INVOICE, 4200.00, 0.24, "ΤΠ-101",
     "Μηνιαία λογιστική υποστήριξη", None),
    (80, 1, REVENUE, finance.DOC_SERVICE_RECEIPT, 650.00, 0.24, "ΑΠΥ-58",
     "Υποβολή περιοδικής ΦΠΑ", None),
    (72, 0, EXPENSE, finance.DOC_EXPENSE, 380.00, 0.24, "Δ-2231",
     "Άδειες λογισμικού", None),
    (60, 2, REVENUE, finance.DOC_SALES_INVOICE, 2750.00, 0.24, "ΤΠ-102",
     "Σύσταση εταιρείας & μισθοδοσία", None),
    (45, 1, REVENUE, finance.DOC_CREDIT_NOTE, 150.00, 0.24, "ΠΣ-7",
     "Πίστωση — έκπτωση όγκου", None),
    (40, 3, EXPENSE, finance.DOC_OPERATING_EXPENSE, 120.00, 0.13, "Λ-904",
     "Γεύμα εργασίας", None),
    (30, 2, finance.DEBT_TYPE, finance.DOC_SALES_INVOICE, 1800.00, 0.24,
     "ΤΠ-103", "Ετήσιες οικονομικές καταστάσεις", -5),
    (14, 0, REVENUE, finance.DOC_SALES_INVOICE, 4200.00, 0.24, "ΤΠ-104",
     "Μηνιαία λογιστική υποστήριξη", None),
    (9, 3, finance.DEBT_TYPE, finance.DOC_SERVICE_RECEIPT, 480.00, 0.24,
     "ΑΠΥ-59", "Τήρηση βιβλίων τριμήνου", 21),
    (5, 1, EXPENSE, finance.DOC_OPERATING_EXPENSE, 95.00, 0.24, "Λ-915",
     "Αναλώσιμα γραφείου", None),
    (2, 2, REVENUE, finance.DOC_SERVICE_RECEIPT, 900.00, 0.24, "ΑΠΥ-60",
     "Συμβουλευτική myDATA", None),
)


def _target_description():
    """The database being written to, with the password removed."""
    return database._require_url().render_as_string(hide_password=True)


def _ensure_user(session):
    """The demo user, created or reset to its published state."""
    user = store.get_user_by_email(session, DEMO_EMAIL) \
        or store.get_user_by_username(session, DEMO_USERNAME)

    if user is None:
        return store.create_user(
            session,
            username=DEMO_USERNAME,
            email=DEMO_EMAIL,
            password_hash=passwords.hash_password(DEMO_PASSWORD),
            subscription_status=subscription.ACTIVE,
            # `active` + a trial date is a self-expiring account; see
            # scripts/create_admin.py.
            trial_ends_at=None,
            company_name=DEMO_COMPANY,
            full_name=DEMO_FULL_NAME,
        ), True

    # The email and the username must BOTH be the demo's. Anything else means
    # one of them belongs to a real tenant, and this script wipes books.
    if user.email != DEMO_EMAIL or user.username != DEMO_USERNAME:
        raise SystemExit(
            f"Refusing to reset {user.email!r} / {user.username!r}: it is not "
            f"the demo account ({DEMO_EMAIL!r} / {DEMO_USERNAME!r}), and "
            f"re-seeding would delete its data.")

    user.password_hash = passwords.hash_password(DEMO_PASSWORD)
    user.company_name = DEMO_COMPANY
    user.full_name = DEMO_FULL_NAME
    # A visitor can enrol 2FA on a shared account; that would lock everyone
    # else out, so the reset always switches it back off.
    user.mfa_enabled = False
    user.mfa_secret = None
    user.subscription_cancel_at = None
    session.add(user)
    # Commits, and clears trial_ends_at on the way to `active`.
    store.set_subscription_status(session, user, subscription.ACTIVE)
    return user, False


def _clear_books(session, user):
    """Delete the demo tenant's own rows, children first."""
    uid = user.id
    session.execute(delete(DebtPayment).where(DebtPayment.user_id == uid))
    # Partial settlements point back at their debt row; release that
    # self-reference so the rows can go in any order.
    session.execute(update(Transaction).where(Transaction.user_id == uid)
                    .values(debt_id=None))
    session.execute(delete(Transaction).where(Transaction.user_id == uid))
    session.execute(delete(Client).where(Client.user_id == uid))
    session.execute(delete(TrustedDevice).where(TrustedDevice.user_id == uid))
    session.commit()


def _seed_books(session, user, today):
    clients = [store.create_client(session, user, name, afm=afm, contact=contact)
               for name, afm, contact in CLIENTS]

    for (days_ago, idx, type_, doc_type, net, rate, number,
         description, due_in) in TRANSACTIONS:
        when = today - dt.timedelta(days=days_ago)
        signed, vat = finance.book_amounts(net, type_, doc_type=doc_type,
                                           vat_rate=rate, basis="net")
        client = clients[idx]
        store.create_transaction(
            session, user, client.name, signed,
            description=description,
            txn_date=when,
            type_=type_,
            source="Demo",
            vat_amount=vat,
            vat_rate=rate,
            client_id=client.id,
            doc_number=number,
            counterparty_afm=client.afm,
            doc_type=doc_type,
            due_date=(today + dt.timedelta(days=due_in)
                      if due_in is not None else None),
        )
    return len(clients), len(TRANSACTIONS)


def seed(session, today=None):
    """Create or reset the demo tenant. Returns (user, created, n_clients, n_txns)."""
    today = today or dt.date.today()
    user, created = _ensure_user(session)
    # Clients, transactions and trusted devices are tenant data under RLS
    # (scripts/enable_rls.sql): without a declared tenant the deletes match
    # nothing and the inserts are refused.
    with tenancy.tenant_scope(session, user.id):
        _clear_books(session, user)
        n_clients, n_txns = _seed_books(session, user, today)
    return user, created, n_clients, n_txns


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create or reset the public demo account with sample data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--yes", action="store_true",
                        help="do not ask for confirmation before writing")
    args = parser.parse_args(argv)

    if not database.is_configured():
        raise SystemExit("DATABASE_URL is not set — there is no database to seed.")

    print(f"Target : {_target_description()}")
    print(f"Account: {DEMO_EMAIL} (its existing data will be replaced)\n")
    if not args.yes and input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
        raise SystemExit("Nothing was written.")

    try:
        with database.session_scope() as session:
            user, created, n_clients, n_txns = seed(session)
            print("Demo account created:" if created else "Demo account reset:")
            print(f"    id            {user.id}")
            print(f"    email         {DEMO_EMAIL}")
            print(f"    password      {DEMO_PASSWORD}")
            print(f"    clients       {n_clients}")
            print(f"    transactions  {n_txns}")
    except SQLAlchemyError as exc:
        raise SystemExit(f"Database error — seeding did not complete: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
