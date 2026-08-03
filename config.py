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


# --- Airtable -------------------------------------------------------------
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


# Removed with the Streamlit retirement, because nothing read them any more:
# OPENAI_API_KEY and AIRTABLE_TABLE_NAME (invoice OCR / publisher), SMTP_* (the
# Streamlit password-reset mail), and the WHATSAPP_* block (which already had
# no consumer anywhere in the repo). Re-add them next to the code that needs
# them rather than keeping settings nothing reads.
