"""
Empty the books for a real production launch, without touching the schema.

WHAT THIS IS FOR
----------------
The database that gets deployed has usually been typed into for weeks: demo
clients, test invoices, a settlement someone tried twice. This deletes that
content and nothing else, so the first real customer starts from a genuinely
empty book rather than from somebody's rehearsal.

WHAT IT DELETES              debt_payments, invoices, transactions, clients
WHAT IT KEEPS                every table (structure), every user account, every
                             password hash, subscription status and Stripe
                             customer id
WHAT IT NEVER DOES           DROP, TRUNCATE CASCADE, ALTER, or anything to a
                             table it was not told about. Deleting the rows and
                             leaving the schema alone is the entire point: a
                             DROP would take the columns added by the migrations
                             in server/database.py with it.

USAGE
-----
    # Default is a DRY RUN. Prints what is there and what would go:
    python scripts/reset_production_data.py

    # Do it:
    python scripts/reset_production_data.py --apply --confirm WIPE-DATA

    # One tenant only, leaving everyone else's books alone:
    python scripts/reset_production_data.py --user tester --apply --confirm WIPE-DATA

    # Also delete the ACCOUNTS (test signups) — not just their data:
    python scripts/reset_production_data.py --apply --confirm WIPE-DATA --include-users

Requires DATABASE_URL in the environment (.env is loaded via config.py).

WHY THREE THINGS ARE REQUIRED TO DELETE ANYTHING
------------------------------------------------
`--apply`, the exact `--confirm` phrase, and (on a terminal) a typed y/N against
the printed target. This script is one shell-history arrow-up away from being
run against the wrong database, and the cost of that is a customer's accounts.
The dry run is the default for the same reason.

DELETION ORDER
--------------
Children first — debt_payments references transactions, clients AND users, and
transactions reference clients. The whole wipe runs in ONE transaction, so an
error half way through rolls back to where it started rather than leaving
transactions whose client no longer exists.
"""

import argparse
import sys
from pathlib import Path

# Allow running as `python scripts/reset_production_data.py` from the repo root
# without installing the project.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

import config  # noqa: F401,E402  (loads .env)
from server import database, store  # noqa: E402
from server.models import (Client, DebtPayment, Invoice,  # noqa: E402
                           PasswordResetToken, Transaction, User)

#: The books. Children first — see the module docstring.
DATA_TABLES = (DebtPayment, Invoice, Transaction, Client)

#: Only with --include-users. PasswordResetToken first: it has a foreign key to
#: users, and a live reset link for a deleted account is a dangling key either
#: way.
AUTH_TABLES = (PasswordResetToken, User)

CONFIRM_PHRASE = "WIPE-DATA"


def _label(model):
    return model.__tablename__


def _scoped(statement, model, user_id):
    """Restrict a statement to one tenant when asked.

    Every table here carries user_id — that is what makes a per-tenant reset
    possible at all. `users` is the exception: it is keyed by its own id.
    """
    if user_id is None:
        return statement
    column = User.id if model is User else model.user_id
    return statement.where(column == user_id)


def counts(session, user_id=None, include_users=False):
    """Rows currently present, per table, in deletion order."""
    tables = DATA_TABLES + (AUTH_TABLES if include_users else ())
    result = {}
    for model in tables:
        statement = _scoped(select(func.count()).select_from(model), model, user_id)
        result[_label(model)] = session.execute(statement).scalar_one()
    return result


def reset_sequences(session, tables):
    """Restart the id sequences so the first real row is id 1.

    Cosmetic, and skipped for a per-tenant reset (other tenants' rows are still
    using those ids). PostgreSQL only: pg_get_serial_sequence has no equivalent
    on SQLite, where the ids restart on their own once the table is empty.
    """
    if not database.is_postgres():
        return []
    from sqlalchemy import text

    restarted = []
    for model in tables:
        name = _label(model)
        # Asked for by name rather than assembled as "<table>_id_seq": that
        # convention is a default, not a guarantee, and a table restored from a
        # dump can carry a differently-named sequence.
        sequence = session.execute(
            text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": name}
        ).scalar()
        if not sequence:
            # An identity column, or no sequence at all. Nothing to restart, and
            # nothing wrong either.
            continue
        # (…, 1, false) means "the NEXT value handed out is 1". RESTART WITH 1
        # would do the same, but only as literal DDL — it cannot take the
        # sequence name as a bound parameter, which is how it would have to
        # arrive here.
        session.execute(text("SELECT setval(CAST(:seq AS regclass), 1, false)"),
                        {"seq": sequence})
        restarted.append(name)
    return restarted


def wipe(session, user_id=None, include_users=False, keep_sequences=False):
    """Delete the rows and return {table: rows deleted}.

    Does NOT commit — the caller owns the transaction, which is what lets the
    dry run and the test suite drive the same code path as the real thing.
    """
    tables = DATA_TABLES + (AUTH_TABLES if include_users else ())
    deleted = {}
    for model in tables:
        statement = _scoped(delete(model), model, user_id)
        deleted[_label(model)] = session.execute(statement).rowcount or 0
    if not keep_sequences and user_id is None:
        reset_sequences(session, tables)
    return deleted


def _target_description():
    """The database being pointed at, with the password removed.

    Printed before anything is deleted because "which database am I actually
    connected to" is the question this script exists to make impossible to get
    wrong — and DATABASE_URL contains a password that must not land in a
    terminal scrollback or a CI log.
    """
    url = database._require_url()
    return url.render_as_string(hide_password=True)


def _print_counts(title, rows):
    print(title)
    if not rows:
        print("    (nothing)")
    for name, n in rows.items():
        print(f"    {name:<22} {n:>8}")
    print(f"    {'TOTAL':<22} {sum(rows.values()):>8}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Delete transactional data, keeping the schema and users.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--apply", action="store_true",
                        help="actually delete (default: dry run)")
    parser.add_argument("--confirm", default="",
                        help=f"required with --apply; must be {CONFIRM_PHRASE}")
    parser.add_argument("--user", default=None,
                        help="restrict to one tenant (username), instead of "
                             "every account in the database")
    parser.add_argument("--include-users", action="store_true",
                        help="ALSO delete the account rows and their password "
                             "reset tokens — test signups, not just their data")
    parser.add_argument("--keep-sequences", action="store_true",
                        help="leave the id sequences where they are instead of "
                             "restarting them at 1")
    parser.add_argument("--no-input", action="store_true",
                        help="skip the interactive y/N (for CI); --apply and "
                             "--confirm are still required")
    args = parser.parse_args(argv)

    if not database.is_configured():
        raise SystemExit("DATABASE_URL is not set — there is nothing to reset.")

    print(f"Target : {_target_description()}")
    print(f"Scope  : {args.user or 'ALL TENANTS'}"
          f"{' + user accounts' if args.include_users else ''}")
    print(f"Mode   : {'APPLY — rows will be deleted' if args.apply else 'DRY RUN'}\n")

    with database.session_scope() as session:
        user_id = None
        if args.user:
            user = store.get_user_by_username(session, args.user)
            if user is None:
                raise SystemExit(f"No such user: {args.user!r}")
            user_id = user.id

        before = counts(session, user_id, args.include_users)
        _print_counts("Rows found:", before)

        if not args.apply:
            print("\nDry run — nothing was deleted. Re-run with:")
            print(f"    --apply --confirm {CONFIRM_PHRASE}")
            return 0

        if args.confirm != CONFIRM_PHRASE:
            raise SystemExit(
                f"\nRefusing to delete: pass --confirm {CONFIRM_PHRASE} to "
                f"confirm you mean this database.")

        if not args.no_input and sys.stdin.isatty():
            answer = input(f"\nDelete {sum(before.values())} row(s) from the "
                           f"database above? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted — nothing was deleted.")
                return 1

        try:
            deleted = wipe(session, user_id, args.include_users,
                           args.keep_sequences)
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            raise SystemExit(f"\nFailed, rolled back — nothing was deleted: {exc}")

        print()
        _print_counts("Deleted:", deleted)

        remaining = counts(session, user_id, args.include_users)
        print()
        _print_counts("Remaining:", remaining)

        # The reassurance the operator actually wants: the accounts are still
        # there, and so is every table.
        if not args.include_users:
            total_users = session.execute(
                select(func.count()).select_from(User)).scalar_one()
            print(f"\nUser accounts kept: {total_users} "
                  f"(logins, password hashes and subscriptions untouched)")
        print("Schema untouched — no table was dropped or altered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
