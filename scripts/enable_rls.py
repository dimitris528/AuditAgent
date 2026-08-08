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

_STATUS = """
SELECT c.relname                             AS table_name,
       c.relrowsecurity                      AS rls_enabled,
       c.relforcerowsecurity                 AS rls_forced,
       (SELECT count(*) FROM pg_policies p
         WHERE p.schemaname = current_schema()
           AND p.tablename = c.relname)      AS policies
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = current_schema()
   AND c.relname = ANY(:tables)
 ORDER BY c.relname
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
        rows = conn.execute(text(_STATUS), {"tables": list(TABLES)}).all()
        present = {row.table_name for row in rows}

        print(f"{'table':<20}{'RLS':<8}{'FORCED':<9}{'policies'}")
        print("-" * 46)
        for row in rows:
            print(f"{row.table_name:<20}{str(row.rls_enabled):<8}"
                  f"{str(row.rls_forced):<9}{row.policies}")
            if not (row.rls_enabled and row.rls_forced and row.policies):
                ok = False
        for missing in set(TABLES) - present:
            print(f"{missing:<20}(table not present)")

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
        conn.execute(text(statements))
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
