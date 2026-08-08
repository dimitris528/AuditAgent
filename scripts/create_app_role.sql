-- ---------------------------------------------------------------------------
-- The database role the application should connect as.
--
-- NOT run by any script here. Run it yourself, as an administrator, against
-- the target database — creating a login role is a persistent, privileged
-- change and it needs a password only you should ever see.
--
-- Why this file exists
-- --------------------
-- `scripts/enable_rls.py --verify` reported:
--
--     connected as: postgres  (BYPASSRLS=True, SUPERUSER=False)
--
-- A role with BYPASSRLS ignores every policy on every table, FORCE included.
-- Applying Row Level Security while the application connects as that role
-- produces a database that reports itself as protected in every dashboard and
-- enforces precisely nothing. The policies are not the hard part; connecting
-- as a role they apply to is.
--
-- Order of operations (all of it matters)
-- ---------------------------------------
--   1. run this file as an admin, with a real password
--   2. run scripts/enable_rls.py --apply  (still as `postgres`)
--   3. point DATABASE_URL at auditagent_app
--   4. set DB_MIGRATE_ON_BOOT=0          (see the note at the bottom)
--   5. run scripts/enable_rls.py --verify again, now as the app role
--   6. log in. Then log in as a SECOND account and confirm it sees its own
--      books and not the first one's.
--
-- Step 6 is the only one that proves anything. The rest is configuration.
-- ---------------------------------------------------------------------------

-- --- The role --------------------------------------------------------------
-- NOBYPASSRLS is the entire point of this file; NOSUPERUSER because a
-- superuser bypasses RLS regardless of the flag. NOCREATEDB / NOCREATEROLE
-- because an application that can create databases can do a great deal else
-- besides.
--
-- Replace the password before running. Do not commit it anywhere.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'auditagent_app') THEN
        CREATE ROLE auditagent_app LOGIN PASSWORD 'REPLACE_ME';
    END IF;
END $$;

ALTER ROLE auditagent_app NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE;

-- --- Privileges ------------------------------------------------------------
-- DML only. No DDL: the application must not be able to ALTER its way out of
-- the policies that constrain it, which is also why migrations keep running
-- as the owner (step 4 below).
GRANT USAGE ON SCHEMA public TO auditagent_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    users, clients, transactions, debt_payments, invoices,
    password_reset_tokens
TO auditagent_app;

-- trusted_devices may not exist yet on a database that predates 2FA.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name = 'trusted_devices') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON trusted_devices TO auditagent_app;
    END IF;
END $$;

-- Every primary key here is a serial/identity column, and INSERT needs the
-- sequence as well as the table. Missing this is a confusing failure: the
-- grant looks complete and every insert fails on "permission denied for
-- sequence".
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO auditagent_app;

-- Tables and sequences added by a later migration, so a future create_all()
-- does not silently leave the application locked out of its own new table.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO auditagent_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO auditagent_app;

-- --- One consequence, stated plainly ---------------------------------------
-- auditagent_app does not own the tables, and ALTER TABLE requires ownership.
-- server/database.init_db() runs ADD COLUMN IF NOT EXISTS on every boot, so
-- under this role those statements raise — caught and logged by the lifespan
-- hook, non-fatal, but the schema then stops being migrated by the app.
--
-- Set DB_MIGRATE_ON_BOOT=0 in the application environment and run migrations
-- deliberately, as the owner, when you deploy. That is the right shape for a
-- production database anyway: a web process that can rewrite its own schema on
-- restart is a much larger blast radius than one that cannot.
