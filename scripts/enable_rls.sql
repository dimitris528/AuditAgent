-- ---------------------------------------------------------------------------
-- Row Level Security: tenant isolation enforced by the database itself.
--
-- Apply with scripts/enable_rls.py (idempotent, safe to re-run). Read
-- server/tenancy.py first — it explains why these policies key on a session
-- variable rather than on auth.uid(), which cannot work for a backend that
-- connects with its own role instead of through PostgREST.
--
-- ORDER OF DEPLOYMENT MATTERS. Ship the application change that sets
-- app.tenant_id FIRST, confirm it is live, and only then run this. The other
-- way round locks out every code path that has not been taught to set it.
--
-- The guarantee: a query that forgets its tenant filter returns NOTHING. It
-- does not return another tenant's rows. The application still filters by
-- user_id on every query — this is the second lock, not the only one.
-- ---------------------------------------------------------------------------

-- --- The authentication tables, which cannot be tenant-scoped --------------
-- `users` and `password_reset_tokens` are read BEFORE any tenant exists:
-- login looks a user up by username, and a reset link is resolved by its token
-- hash with nobody signed in. A tenant policy on either makes it impossible to
-- authenticate at all.
--
-- They still need an EXPLICIT policy, and this is the part that bites. RLS is
-- already ENABLED on both in this database (Supabase turns it on for tables
-- created through its dashboard) with no policy attached — which denies every
-- row to any role that does not bypass RLS. Today nothing notices, because the
-- application connects as the owner. Point it at a properly restricted role
-- without adding these two policies first and SELECT ... FROM users returns
-- nothing: not a leak, a total authentication outage.
--
-- So the openness is written down rather than left implied. What protects
-- these tables is the password check and the token hash, not RLS, and a
-- policy saying so is more honest than an empty one that reads as protection.
DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['users', 'password_reset_tokens']
    LOOP
        IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name = target) THEN
            CONTINUE;
        END IF;
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', target);
        EXECUTE format('DROP POLICY IF EXISTS auth_table_open_policy ON %I', target);
        EXECUTE format($p$
            CREATE POLICY auth_table_open_policy ON %I
                FOR ALL USING (true) WITH CHECK (true)
        $p$, target);
        RAISE NOTICE 'open policy on % (pre-authentication table)', target;
    END LOOP;
END $$;

-- --- Tenant data ------------------------------------------------------------
-- Everything below is partitioned by user_id and gets the full treatment.

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'clients', 'transactions', 'debt_payments', 'invoices', 'trusted_devices'
    ]
    LOOP
        -- Skip tables this deployment does not have yet, so the script can be
        -- run against a database mid-migration without failing halfway.
        IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name = target) THEN
            RAISE NOTICE 'skipping %, table not present', target;
            CONTINUE;
        END IF;

        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', target);

        -- FORCE is the line that makes this real. Without it PostgreSQL
        -- exempts the table OWNER from its own policies — and the owner is
        -- very often exactly the role in DATABASE_URL, which would leave
        -- every policy below inert while looking perfectly configured.
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', target);

        -- Dropped and recreated rather than CREATE POLICY IF NOT EXISTS
        -- (which does not exist): re-running must converge on the definition
        -- in this file rather than leave an older policy in place.
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_policy ON %I', target);

        -- USING governs what can be READ, UPDATEd or DELETEd; WITH CHECK
        -- governs what can be WRITTEN. Both are required: USING alone would
        -- let a tenant INSERT a row stamped with somebody else's user_id,
        -- which they could then never see — a write-only cross-tenant leak.
        --
        -- current_setting(..., true) returns NULL rather than raising when the
        -- variable is unset, and NULL = user_id is never true. So a connection
        -- that has not declared a tenant sees NO ROWS. Failing closed is the
        -- entire point.
        EXECUTE format($p$
            CREATE POLICY tenant_isolation_policy ON %I
                FOR ALL
                USING (user_id = NULLIF(current_setting('app.tenant_id', true), '')::bigint)
                WITH CHECK (user_id = NULLIF(current_setting('app.tenant_id', true), '')::bigint)
        $p$, target);

        RAISE NOTICE 'RLS enabled on %', target;
    END LOOP;
END $$;

-- --- Indexes ---------------------------------------------------------------
-- Every policy above filters on user_id, so every query now carries that
-- predicate whether it asked to or not. These indexes already exist from the
-- model definitions; asserted here because a policy on an unindexed column
-- turns each read into a sequential scan.
CREATE INDEX IF NOT EXISTS ix_clients_user_id ON clients (user_id);
CREATE INDEX IF NOT EXISTS ix_transactions_user_id ON transactions (user_id);
CREATE INDEX IF NOT EXISTS ix_debt_payments_user_id ON debt_payments (user_id);
CREATE INDEX IF NOT EXISTS ix_invoices_user_id ON invoices (user_id);
