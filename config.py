"""
Configuration for the AI Document Auditor.

All secrets now live in a local `.env` file (never committed — see .gitignore)
and are loaded here via python-dotenv. Copy `.env.example` to `.env` and fill in
your real values.
"""

import os

from dotenv import load_dotenv

# Load variables from a local .env file into the environment (no-op if absent).
load_dotenv()


def _env(name, default=""):
    """Read an env var and strip surrounding whitespace.

    Secrets pasted into a hosting dashboard (Render) or a .env file routinely
    pick up a trailing newline or a stray space. An Airtable PAT with a
    trailing "\\n" produces a 401 on every call, and a Base ID with one
    produces a 404 — both of which look like "wrong credentials" rather than
    "credentials with whitespace", so they are miserable to diagnose. Strip
    once, here, and every consumer is covered. Falls back to `default` when the
    variable is unset OR set to nothing but whitespace.
    """
    value = (os.getenv(name) or "").strip()
    return value or default


def _flag(name, default=False):
    """Read a boolean env var.

    Accepts the spellings people actually type into a hosting dashboard rather
    than only Python's idea of truth: "1", "true", "yes", "on" (any case).
    ANYTHING else — including a typo like "ture" or an empty string — reads as
    False, so a flag that guards an exposure fails CLOSED rather than being
    switched on by a misspelling.
    """
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# --- PostgreSQL (Supabase) — the live data layer --------------------------
# Full connection string, e.g.
#   postgresql://postgres.<ref>:<password>@<host>:6543/postgres
# IMPORTANT: percent-encode special characters in the password (/ -> %2F,
# @ -> %40, : -> %3A, ? -> %3F). An un-escaped "/" silently reparses the URL so
# the password fragment becomes the port. See server/database.py.
# Read by server/database.py directly from the environment; listed here so the
# full configuration surface is documented in one place.
DATABASE_URL = _env("DATABASE_URL")


# --- Interactive API documentation ----------------------------------------
# Serves /docs (Swagger UI), /redoc and /openapi.json. Every route behind them
# is auth-gated, so exposing the schema is not itself a breach — but it hands a
# stranger a complete, accurate map of the API's shape, parameters and error
# codes, which is free reconnaissance for anyone probing the deployment. It is
# also the only publicly reachable surface here that serves no customer.
#
# DEFAULT OFF, deliberately: a new deployment that forgets to set anything is
# closed rather than open. Turn it on locally (DOCS_ENABLED=true in .env) while
# working on the API.
DOCS_ENABLED = _flag("DOCS_ENABLED", default=False)


# --- Airtable (MIGRATION ONLY) --------------------------------------------
# No serving code reads these any more — they exist for the one-time backfill
# in scripts/migrate_airtable_to_postgres.py.
AIRTABLE_PAT = _env("AIRTABLE_PAT", "YOUR_PERSONAL_ACCESS_TOKEN")
AIRTABLE_BASE_ID = _env("AIRTABLE_BASE_ID", "YOUR_BASE_ID")

# Tables backing the SaaS (see airtable_client.py for the required schema).
AIRTABLE_PROJECTS_TABLE = _env("AIRTABLE_PROJECTS_TABLE", "Projects")
AIRTABLE_TRANSACTIONS_TABLE = _env("AIRTABLE_TRANSACTIONS_TABLE", "Transactions")
AIRTABLE_USERS_TABLE = _env("AIRTABLE_USERS_TABLE", "Users")


# --- Stripe ----------------------------------------------------------------
# Signing secret for the billing webhook (server/webhooks.py, mounted at
# POST /api/v1/webhooks/stripe). Take it from Stripe Dashboard -> Developers
# -> Webhooks -> your endpoint ("whsec_..."). Stripped like the Airtable
# secrets: a trailing newline here makes EVERY signature check fail, which
# reads as "Stripe is sending bad signatures" rather than as a config typo.
# Leave empty to disable the endpoint (it then rejects with 503 rather than
# accepting unverified payloads).
STRIPE_WEBHOOK_SECRET = _env("STRIPE_WEBHOOK_SECRET")

# Secret API key ("sk_test_..." / "sk_live_...") and the recurring Price the
# checkout subscribes to ("price_..."). Both are required by
# POST /api/v1/billing/checkout; with either missing the endpoint reports 503
# and the billing page says the subscription is not configured yet, rather than
# opening a checkout that cannot complete.
#
# NOTE the price is a Price id, not a Product id. Stripe rejects a "prod_..."
# here, and the error it returns names neither field.
STRIPE_SECRET_KEY = _env("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = _env("STRIPE_PRICE_ID")

# Public origin of the Next.js dashboard, used to build the Stripe success and
# cancel URLs. Stripe redirects the BROWSER there after checkout, so it must be
# the address the user's browser can reach — not the API's own host.
APP_BASE_URL = _env("APP_BASE_URL", "http://localhost:3000").rstrip("/")


# --- Free trial ------------------------------------------------------------
# Days of full access granted automatically at registration, with no card. When
# it elapses the account flips itself to "inactive" on its next request: reads
# still work, writes return 402. server/subscription.py owns what that means
# and reads the value from here.
TRIAL_DAYS = int(_env("TRIAL_DAYS", "14"))


# --- OpenAI Vision (invoice OCR) -------------------------------------------
# Powers POST /api/v1/documents/scan — reading a PDF/photo of an invoice into
# the transaction form (server/ocr.py). Leave empty to disable the endpoint:
# it then returns 503 rather than silently handing back a blank extraction,
# which would be filed as a blank invoice.
OPENAI_API_KEY = _env("OPENAI_API_KEY")
# Must be a VISION-capable model that also supports Structured Outputs — the
# scan sends an image (or a PDF) and requires a JSON-schema-shaped reply, and a
# text-only model fails on the first request rather than at startup. Pinned to
# a long-lived default; override to move to a newer one without a code change.
OPENAI_MODEL = _env("OPENAI_MODEL", "gpt-4o")


# --- Sentry (error monitoring) ---------------------------------------------
# server/monitoring.py initialises the SDK from these. Leave SENTRY_DSN empty
# and nothing is initialised at all: no integrations are patched in, no network
# calls are made, and the process behaves exactly as it did before monitoring
# existed. That is the intended state for local development and for the test
# suite — an error reporter that phones home from a developer's laptop is a
# privacy problem, not a feature.
#
# The DSN is not a secret in the way an API key is (it is write-only ingest and
# ships in the browser bundle of every site using Sentry), but it is still set
# per environment rather than committed, so a staging deploy cannot pollute the
# production issue stream.
SENTRY_DSN = _env("SENTRY_DSN")

# The tag every event is filed under. Explicit when set; otherwise derived, and
# the derivation matters: an event tagged "development" in production is an
# alert nobody routes. RENDER is set by Render's own runtime on every service,
# so a deployed process identifies itself without anyone remembering to.
SENTRY_ENVIRONMENT = (_env("SENTRY_ENVIRONMENT")
                      or _env("APP_ENV")
                      or ("production" if _env("RENDER") else "development"))

# Fraction of requests traced for performance (0.0–1.0). Traces are the
# expensive half of a Sentry quota and the dashboard makes several calls per
# page, so production samples rather than recording everything; development
# records all of it, where the volume is one person.
#
# Parsed defensively: a typo here must not take the process down at import.
def _sample_rate(name, default):
    raw = _env(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        print(f"[WARN] {name}={raw!r} is not a number — using {default}.")
        return default
    if not 0.0 <= value <= 1.0:
        print(f"[WARN] {name}={raw!r} is outside 0.0–1.0 — using {default}.")
        return default
    return value


SENTRY_TRACES_SAMPLE_RATE = _sample_rate(
    "SENTRY_TRACES_SAMPLE_RATE",
    0.1 if SENTRY_ENVIRONMENT == "production" else 1.0)

# Which deploy an error came from, so a spike can be tied to the change that
# caused it. Render exposes the commit SHA as RENDER_GIT_COMMIT.
SENTRY_RELEASE = _env("SENTRY_RELEASE") or _env("RENDER_GIT_COMMIT")


# Removed with the Streamlit retirement, because nothing read them any more:
# AIRTABLE_TABLE_NAME (the publisher), SMTP_* (the Streamlit password-reset
# mail), and the WHATSAPP_* block (which already had no consumer anywhere in
# the repo). Re-add them next to the code that needs them rather than keeping
# settings nothing reads.
#
# ANTHROPIC_API_KEY / ANTHROPIC_MODEL went the same way when the invoice OCR
# moved from the Claude Messages API to OpenAI Vision; nothing reads them now.
