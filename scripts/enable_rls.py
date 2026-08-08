"""
Apply (and verify) the Row Level Security policies in enable_rls.sql.

    python -m scripts.enable_rls --verify    # report, change nothing
    python -m scripts.enable_rls --apply     # enable RLS on the tenant tables

Deliberately NOT part of init_db(). Every other migration in this project is
additive and safe on every boot; this one changes what the application is
allowed to READ, and a half-deployed version of it is an outage. It runs when
somebody decides it runs, having first confirmed with --verify that the
application is already setting app.tenant_id.

The order that matters, in one line: ship server/tenancy.py, run --verify to
see the tenant arriving, then --apply.
"""

import argparse
import pathlib
import sys

from sqlalchemy import text

# Importable both as a module and as a script run from the repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from server import database, tenancy  # noqa: E402

_SQL = pathlib.Path(__file__).with_name("enable_rls.sql")

#: Tables the policies cover. Kept in step with enable_rls.sql; --verify
#: reports on exactly these.
TABLES = ("clients", "transactions", "debt_payments", "invoices",
          "trusted_devices")

#: Read BEFORE any tenant exists, so they carry an open policy rather than a
#: tenant one — and they are checked separately because the failure they cause
#: is the opposite of a leak. RLS enabled here with no policy denies every row
#: to a restricted role, which means nobody can log in.
AUTH_TABLES = ("users", "password_reset_tokens")

_STATUS = """
SELECT c.relname                             AS table_name,
       c.relrowsecurity                      AS rls_enabled,
       c.relforcerowsecurity                 AS rls_forced,
       pg_get_userbyid(c.relowner)           AS owner,
       (SELECT count(*) FROM pg_policies p
         WHERE p.schemaname = current_schema()
           AND p.tablename = c.relname)      AS policies
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = current_schema()
   AND c.relname = ANY(:tables)
 ORDER BY c.relname
"""

# The question that decides whether ANY of this has an effect, and the one it
# is easiest to never think to ask.
#
# A role with BYPASSRLS ignores every policy on every table, FORCE included —
# and on a managed Postgres the role in the connection string is very often
# exactly such a role. Enabling RLS under one produces a database that reports
# itself as protected in every dashboard and enforces nothing whatsoever.
_ROLE = """
SELECT current_user            AS role_name,
       r.rolbypassrls          AS bypasses_rls,
       r.rolsuper              AS is_superuser
  FROM pg_roles r
 WHERE r.rolname = current_user
"""


def _require_postgres():
    if not database.is_configured():
        raise SystemExit("DATABASE_URL is not set.")
    if not database.is_postgres():
        raise SystemExit(
            "Row Level Security is a PostgreSQL feature and this DATABASE_URL "
            "points somewhere else. Nothing to do.")


def verify():
    """Report the state of play without changing anything.

    Two questions, and the second is the one people forget: are the policies
    on, and is the application actually SETTING the variable they read? A
    database with policies and an application that never sets app.tenant_id is
    not locked down, it is down.
    """
    _require_postgres()
    engine = database.get_engine()
    ok = True
    with engine.connect() as conn:
        role = conn.execute(text(_ROLE)).first()
        rows = conn.execute(text(_STATUS), {"tables": list(TABLES)}).all()
        present = {row.table_name for row in rows}

        print(f"{'table':<22}{'RLS':<8}{'FORCED':<9}{'policies':<10}{'owner'}")
        print("-" * 64)
        for row in rows:
            print(f"{row.table_name:<22}{str(row.rls_enabled):<8}"
                  f"{str(row.rls_forced):<9}{row.policies:<10}{row.owner}")
            if not (row.rls_enabled and row.rls_forced and row.policies):
                ok = False
            # The dangerous middle state, and the reason this is called out
            # rather than merely counted: a table with RLS on and NO policies
            # reports itself as protected in every dashboard while enforcing
            # nothing, because the owner is exempt until FORCE is set. It is
            # also one non-owner connection away from denying everything.
            if row.rls_enabled and not row.policies:
                print(f"{'':<20}^ RLS is on with NO policy — protects nothing "
                      "while the owner connects, denies everything otherwise.")
        for missing in set(TABLES) - present:
            print(f"{missing:<22}(table not present)")

        # The authentication tables, checked separately because the failure
        # they cause is the opposite of a leak: RLS on with no policy denies
        # every row to a restricted role, and the symptom is that nobody can
        # log in at all.
        auth = conn.execute(text(_STATUS), {"tables": list(AUTH_TABLES)}).all()
        print()
        for row in auth:
            print(f"{row.table_name:<22}{str(row.rls_enabled):<8}"
                  f"{str(row.rls_forced):<9}{row.policies:<10}{row.owner}")
            if row.rls_enabled and not row.policies:
                ok = False
                print(f"{'':<20}^ LOCKOUT RISK. Read before login, so a "
                      "restricted role would find no account")
                print(f"{'':<20}  and authentication would fail entirely. "
                      "enable_rls.sql adds an open policy.")

        print()
        if role is None:
            print("Could not identify the connecting role.")
            ok = False
        else:
            print(f"connected as: {role.role_name}"
                  f"  (BYPASSRLS={role.bypasses_rls}, "
                  f"SUPERUSER={role.is_superuser})")
            if role.bypasses_rls or role.is_superuser:
                ok = False
                print()
                print("  STOP. This role ignores every policy, FORCE included.")
                print("  Applying RLS while the application connects as it")
                print("  would produce a database that reports itself as")
                print("  protected and enforces nothing.")
                print("  Create a role WITHOUT BYPASSRLS for the application,")
                print("  grant it the table privileges it needs, and point")
                print("  DATABASE_URL at that role first.")
            owners = {row.owner for row in rows}
            if role.role_name in owners:
                print(f"  Owns the tables, so FORCE ROW LEVEL SECURITY is "
                      f"REQUIRED for the policies to apply — enable_rls.sql "
                      f"sets it.")

        # Is the session variable reaching the database? Asked by setting it
        # through the same code path the application uses and reading it back.
        token = tenancy.set_current_tenant(12345)
        try:
            with database.session_scope() as session:
                seen = session.exec(
                    text("SELECT current_setting('app.tenant_id', true)")
                ).first()
            seen = seen[0] if seen else None
        finally:
            tenancy.reset(token)

        print()
        if seen == "12345":
            print("app.tenant_id is being set correctly by the application.")
        else:
            ok = False
            print(f"app.tenant_id did NOT arrive (read back {seen!r}). Do NOT "
                  "apply the policies until this works — they would lock the "
                  "application out of every row.")

    print()
    print("RLS is fully enabled." if ok else "RLS is NOT fully enabled.")
    return 0 if ok else 1


def apply():
    _require_postgres()
    statements = _SQL.read_text(encoding="utf-8")
    engine = database.get_engine()
    with engine.begin() as conn:
        # Straight to the driver cursor with NO parameter argument, which is
        # load-bearing twice over.
        #
        # text() would parse the file for :bindparams a migration must never
        # contain. And psycopg2 only skips its own %-interpolation when no
        # parameters are passed AT ALL — an empty tuple is not the same thing,
        # and would make it choke on the %I placeholders inside the format()
        # calls below. (exec_driver_sql distils None into an empty dict, which
        # is why it is not used here either.)
        #
        # begin() keeps the whole file in one transaction, so a failure part
        # way through leaves no half-policied table behind — as this very
        # script proved when it first failed here and rolled back cleanly.
        cursor = conn.connection.cursor()
        try:
            cursor.execute(statements)
        finally:
            cursor.close()
    print(f"Applied {_SQL.name}.")
    print()
    return verify()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true",
                       help="enable RLS and (re)create the policies")
    group.add_argument("--verify", action="store_true",
                       help="report the current state, change nothing")
    args = parser.parse_args(argv)
    return apply() if args.apply else verify()


if __name__ == "__main__":
    raise SystemExit(main())
