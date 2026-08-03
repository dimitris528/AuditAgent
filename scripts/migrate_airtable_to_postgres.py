"""
One-time backfill: copy the live Airtable base into PostgreSQL (Supabase).

WHY THIS EXISTS
---------------
The API now reads and writes PostgreSQL exclusively. Deploying that change
against an empty database does not lose anything — the Airtable base is
untouched — but every existing tenant logs in to a dashboard showing zero
clients and zero transactions, because their rows are still in Airtable. Run
this once, BEFORE or immediately after the cutover deploy, to move them.

This script only ever READS from Airtable. It never deletes or modifies the
base, so it is safe to run, inspect the result, and run again.

USAGE
-----
    # See exactly what would be written, touching nothing:
    python scripts/migrate_airtable_to_postgres.py --dry-run

    # Do it:
    python scripts/migrate_airtable_to_postgres.py

Requires AIRTABLE_PAT, AIRTABLE_BASE_ID and DATABASE_URL in the environment
(.env is loaded automatically via config.py).

IDEMPOTENCE
-----------
Re-running is safe. Users are matched on username, clients on (user, name),
and transactions on a natural key of (user, client, amount, date, type,
description). Anything already present is skipped rather than duplicated, so
an interrupted run can simply be repeated.

PASSWORDS
---------
Password cells are copied VERBATIM. Rows already holding a pbkdf2_sha256$…
hash keep working immediately. Rows still holding legacy plaintext also keep
working, because auth.password_matches accepts both — but they stay plaintext
until that user next changes their password, so consider forcing a reset.

EMAIL
-----
users.email is NOT NULL and UNIQUE, while Airtable's Email column was optional.
Accounts without one get a deterministic placeholder
(<username>@no-email.invalid, a reserved TLD that can never receive mail) so
the row can exist at all. Those users must set a real address before anything
email-based will work for them; the summary lists them explicitly.
"""

import argparse
import sys
from pathlib import Path

# Allow running as `python scripts/migrate_airtable_to_postgres.py` from the
# repo root without installing the project.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402  (loads .env)
import airtable_client as air  # noqa: E402
from server import database, store  # noqa: E402
from server.models import STATUS_ACTIVE, STATUS_COMPLETED, Client, Transaction  # noqa: E402

PLACEHOLDER_EMAIL_DOMAIN = "no-email.invalid"


def _txn_key(client, amount, date_, type_, description):
    """Natural key used to detect an already-migrated transaction. Airtable row
    ids are not carried over, so identity has to come from the content."""
    return (
        (client or "").strip().lower(),
        round(float(amount or 0), 2),
        date_.isoformat() if date_ else "",
        (type_ or "").strip(),
        (description or "").strip(),
    )


def migrate(dry_run=False):
    if not air.is_configured():
        raise SystemExit("AIRTABLE_PAT / AIRTABLE_BASE_ID are not set — nothing to read.")
    if not database.is_configured():
        raise SystemExit("DATABASE_URL is not set — nowhere to write.")

    print(f"{'DRY RUN — no writes' if dry_run else 'LIVE RUN'}\n")
    database.init_db()

    users = air.find_all_users() if hasattr(air, "find_all_users") else None
    if users is None:
        # airtable_client has no list-all helper; read the Users table directly.
        users = air._select(config.AIRTABLE_USERS_TABLE, "NOT({Username}='')")
    print(f"Airtable users: {len(users)}")

    summary = {"users": 0, "users_skipped": 0, "clients": 0, "txns": 0,
               "placeholder_emails": []}

    with database.session_scope() as session:
        for row in users:
            f = row.get("fields", {})
            username = (f.get("Username") or "").strip()
            if not username:
                continue

            existing = store.get_user_by_username(session, username)
            if existing:
                print(f"  = user {username!r} already present")
                summary["users_skipped"] += 1
                user = existing
            else:
                email = (f.get("Email") or "").strip().lower()
                if not email:
                    email = f"{username.lower()}@{PLACEHOLDER_EMAIL_DOMAIN}"
                    summary["placeholder_emails"].append(username)
                print(f"  + user {username!r} <{email}>")
                summary["users"] += 1
                if dry_run:
                    # Deliberately NOT `continue`: skipping to the next user
                    # here would stop the dry run from ever counting this
                    # account's clients and transactions, and it would report a
                    # reassuring "0 rows to migrate" for exactly the accounts
                    # that have the most to move. Every write below is already
                    # guarded by `if dry_run`, so falling through is safe with
                    # user left unset.
                    user = None
                else:
                    user = store.create_user(
                        session,
                        username=username,
                        email=email,
                        # Verbatim: already-hashed values keep verifying, and
                        # legacy plaintext still matches via password_matches.
                        password_hash=str(f.get("Password") or ""),
                        subscription_status=(f.get("SubscriptionStatus") or "Active"),
                    )

            # --- that user's clients (Airtable "Projects") ---
            projects = (air.get_active_projects(username)
                        + air.get_completed_projects(username))
            for proj in projects:
                pf = proj.get("fields", {})
                name = (pf.get("Name") or "").strip()
                if not name:
                    continue
                if not dry_run and store.find_client(session, user, name):
                    continue
                status = (STATUS_COMPLETED
                          if (pf.get("Status") or "") == STATUS_COMPLETED
                          else STATUS_ACTIVE)
                print(f"      + client {name!r} ({status})")
                summary["clients"] += 1
                if dry_run:
                    continue
                session.add(Client(
                    user_id=user.id,
                    name=name,
                    status=status,
                    closed_date=store._coerce_date(pf.get("ClosedDate")),
                ))
            if not dry_run:
                session.commit()

            # --- that user's transactions ---
            existing_keys = set()
            if not dry_run:
                for rec in store.get_transactions(session, user):
                    tf = rec["fields"]
                    existing_keys.add(_txn_key(
                        tf.get("Category"), tf.get("Amount"),
                        store._coerce_date(tf.get("Date")),
                        tf.get("Type"), tf.get("Description")))

            moved = 0
            for rec in air.get_transactions(username):
                tf = rec.get("fields", {})
                date_ = store._coerce_date(tf.get("Date"))
                key = _txn_key(tf.get("Category"), tf.get("Amount"), date_,
                               tf.get("Type"), tf.get("Description"))
                if key in existing_keys:
                    continue
                existing_keys.add(key)
                moved += 1
                if dry_run:
                    continue
                session.add(Transaction(
                    user_id=user.id,
                    client=(tf.get("Category") or "").strip(),
                    amount=float(tf.get("Amount") or 0),
                    type=tf.get("Type"),
                    vat_amount=tf.get("VAT_Amount"),
                    vat_rate=tf.get("VAT_Rate"),
                    date=date_,
                    description=tf.get("Description"),
                    source=tf.get("Source"),
                    file_hash=tf.get("FileHash"),
                ))
            if moved:
                print(f"      + {moved} transactions")
            summary["txns"] += moved
            if not dry_run:
                session.commit()

    print("\n--- summary ---")
    print(f"  users created   : {summary['users']}")
    print(f"  users skipped   : {summary['users_skipped']} (already present)")
    print(f"  clients created : {summary['clients']}")
    print(f"  transactions    : {summary['txns']}")
    if summary["placeholder_emails"]:
        print("\n  ACTION REQUIRED — these accounts had no Airtable Email and got a")
        print("  placeholder address; they cannot use any email-based flow until it")
        print(f"  is corrected: {', '.join(summary['placeholder_emails'])}")
    if dry_run:
        print("\n  (dry run — nothing was written)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be migrated without writing")
    migrate(dry_run=ap.parse_args().dry_run)
