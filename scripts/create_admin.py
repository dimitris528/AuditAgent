"""
Create the owner's account on a freshly wiped production database.

WHAT "ADMIN" MEANS HERE
-----------------------
There is no admin ROLE in this application. `users` has no is_admin, no role
column, and no endpoint checks for one — every account is a tenant that sees
its own books and nothing else (server/store.py scopes every query by
user_id). So this script does not grant elevated privileges, because there are
none to grant. What it creates is the OWNER'S OWN tenant: an ordinary account
that happens to be the first one in the database, with its subscription set to
`active` so it is not on a 14-day clock.

If you want a genuine admin role — one account able to read other tenants'
data — that is a feature, not a setup step, and it does not exist yet.

WHY NOT JUST REGISTER THROUGH THE UI
------------------------------------
Signing up at /register works and is the normal path, but it always creates a
`trialing` account with `trial_ends_at` 14 days out. The owner's account would
then lock itself to read-only two weeks after launch, mid-business. This script
writes `active` with NO trial date, which is the state a paid Stripe checkout
produces (server/webhooks.py) and the only state that never expires.

That pairing is load-bearing, not tidiness: subscription.resolve() reads a
PRESENT trial date as "this is a trial" regardless of what the status column
says, so an `active` row that still carried a trial date would flip itself back
to `inactive` the moment the date passed. Active means active AND no date.

THE PASSWORD IS NEVER AN ARGUMENT
---------------------------------
It is prompted for, hidden, and confirmed. A --password flag would put the
owner's production credential into shell history, into `ps` output, and into
any CI log that echoed the command. There is deliberately no way to pass it
non-interactively.

USAGE
-----
    # Prompts for everything:
    python scripts/create_admin.py

    # Identity on the command line, password still prompted:
    python scripts/create_admin.py --email owner@example.com --username owner

    # Reset the password of an account that already exists (and make it
    # active). Refused without this flag, so a re-run cannot silently
    # overwrite a live credential:
    python scripts/create_admin.py --email owner@example.com --reset-password

Requires DATABASE_URL in the environment (.env is loaded via config.py).
"""

import argparse
import getpass
import sys
from pathlib import Path

# Allow running as `python scripts/create_admin.py` from the repo root without
# installing the project.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.exc import IntegrityError, SQLAlchemyError  # noqa: E402

import config  # noqa: F401,E402  (loads .env)
import passwords  # noqa: E402
from server import database, store, subscription  # noqa: E402

#: Mirrors server.main.MIN_PASSWORD_LENGTH. Duplicated rather than imported
#: because importing server.main pulls in the whole FastAPI app — Stripe,
#: OpenAI, the routers — and prints an alarming "JWT_SECRET is unset" warning
#: at a moment when the operator is watching for exactly that kind of message.
#: tests/test_create_admin.py asserts the two values stay equal, so the
#: duplication cannot drift silently.
MIN_PASSWORD_LENGTH = 8


def _target_description():
    """The database being written to, with the password removed.

    Printed before anything is created for the same reason the wipe script
    prints it: "which database am I actually pointed at" is the question worth
    making impossible to get wrong, and DATABASE_URL carries a password that
    must not reach a terminal scrollback.
    """
    return database._require_url().render_as_string(hide_password=True)


def prompt_password(confirm=True):
    """Read a password twice from the terminal, hidden.

    Raises SystemExit rather than looping forever: this runs in a launch
    checklist, and a script that cannot be exhausted by a typo is worth more
    than one that keeps asking.
    """
    first = getpass.getpass("Password: ")
    if len(first) < MIN_PASSWORD_LENGTH:
        raise SystemExit(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters "
            f"— the API enforces the same minimum and would reject this "
            f"account's own password reset.")
    if confirm and first != getpass.getpass("Password (again): "):
        raise SystemExit("The two passwords did not match — nothing was created.")
    return first


def create_admin(session, email, username, password, reset_password=False):
    """Create (or re-activate) the owner's account. Returns (user, created).

    Does the availability check that the API's /register does, for the same
    friendly-error reason — but the UNIQUE constraints on email and username
    remain the real guard.
    """
    email = str(email).strip().lower()
    username = str(username).strip()

    existing = store.get_user_by_email(session, email) \
        or store.get_user_by_username(session, username)

    if existing is not None:
        if not reset_password:
            raise SystemExit(
                f"An account already exists for {existing.email!r} / "
                f"{existing.username!r}.\n"
                f"Re-run with --reset-password to set a new password and make "
                f"it active, or pick a different email/username. Refusing to "
                f"overwrite a live credential by default.")
        existing.password_hash = passwords.hash_password(password)
        session.add(existing)
        # set_subscription_status commits, and clears trial_ends_at on its way
        # to `active` — which is precisely the invariant described in the module
        # docstring, so it is reused rather than re-implemented here.
        store.set_subscription_status(session, existing, subscription.ACTIVE)
        return existing, False

    user = store.create_user(
        session,
        username=username,
        email=email,
        # Hashed here — the plaintext never reaches the database.
        password_hash=passwords.hash_password(password),
        subscription_status=subscription.ACTIVE,
        # Explicitly None. See the module docstring: `active` + a trial date is
        # a self-expiring account.
        trial_ends_at=None,
    )
    return user, True


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create the owner's production account (subscription: active).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--email", default=None,
                        help="login email (prompted if omitted)")
    parser.add_argument("--username", default=None,
                        help="tenant username (prompted if omitted; defaults "
                             "to the part of the email before @)")
    parser.add_argument("--reset-password", action="store_true",
                        help="if the account already exists, set a new password "
                             "and make it active instead of refusing")
    args = parser.parse_args(argv)

    if not database.is_configured():
        raise SystemExit(
            "DATABASE_URL is not set — there is no database to create the "
            "account in. Set it to the PRODUCTION connection string first.")

    print(f"Target : {_target_description()}\n")

    email = args.email or input("Admin email: ").strip()
    if "@" not in email:
        raise SystemExit(f"{email!r} is not an email address.")
    # Defaulting the username to the local part keeps the common case to one
    # question. It is a separate column because the JWT subject and every
    # pre-existing row key tenancy on username, not email (server/models.py).
    default_username = email.split("@", 1)[0]
    username = args.username or input(f"Username [{default_username}]: ").strip() \
        or default_username

    password = prompt_password()

    try:
        with database.session_scope() as session:
            user, created = create_admin(session, email, username, password,
                                         args.reset_password)
            state = subscription.resolve(user.subscription_status,
                                         user.trial_ends_at)
            print()
            print("Account created:" if created else "Account updated:")
            print(f"    id                  {user.id}")
            print(f"    email               {user.email}")
            print(f"    username            {user.username}")
            print(f"    subscription        {state.status}")
            print(f"    trial_ends_at       {user.trial_ends_at or '(none — does not expire)'}")
            print(f"    writes allowed      {state.allows_writes}")
            if state.status != subscription.ACTIVE or not state.allows_writes:
                # Belt and braces: if this ever prints, the account would hit
                # the paywall on its first write, and the operator should know
                # before the customer does.
                raise SystemExit(
                    "\nWARNING: the account did NOT come out active. Do not "
                    "launch on it — investigate server/subscription.py.")
            print("\nLog in at the dashboard with the email above.")
    except IntegrityError:
        raise SystemExit(
            "That email or username was taken between the check and the "
            "insert — nothing was created. Re-run.")
    except SQLAlchemyError as exc:
        raise SystemExit(f"Database error — nothing was created: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
