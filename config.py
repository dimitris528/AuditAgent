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


# --- OpenAI ---------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_API_KEY")


# --- Airtable -------------------------------------------------------------
AIRTABLE_PAT = os.getenv("AIRTABLE_PAT", "YOUR_PERSONAL_ACCESS_TOKEN")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID", "YOUR_BASE_ID")
AIRTABLE_TABLE_NAME = os.getenv("AIRTABLE_TABLE_NAME", "Invoices")

# Tables backing the conversational Micro-SaaS (see airtable_client.py for schema).
AIRTABLE_PROJECTS_TABLE = os.getenv("AIRTABLE_PROJECTS_TABLE", "Projects")
AIRTABLE_TRANSACTIONS_TABLE = os.getenv("AIRTABLE_TRANSACTIONS_TABLE", "Transactions")
AIRTABLE_USERS_TABLE = os.getenv("AIRTABLE_USERS_TABLE", "Users")


# --- Password-reset email (app.py "Ξέχασα τον κωδικό μου") ------------------
# Sent with stdlib smtplib through a Gmail account. SMTP_PASSWORD is a Gmail
# App Password (Google Account -> Security -> 2-Step Verification -> App
# passwords), NOT the account's normal password. Leave both empty to disable
# the reset flow (requests then fail with a "temporarily unavailable" error).
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))  # implicit TLS (SMTP_SSL)


# Note: no Stripe settings live here. The webhook service that verified
# Stripe signatures was retired with the migration to Next.js + FastAPI;
# nothing in this repo talks to Stripe server-side any more (the legacy
# Πληρωμές tab only links out to the Payment Link / Customer Portal).


# --- WhatsApp Business Cloud API ------------------------------------------
# Create an app at https://developers.facebook.com/ -> add the "WhatsApp"
# product. WHATSAPP_TOKEN is the (permanent) access token; WHATSAPP_PHONE_NUMBER_ID
# is the "Phone number ID" shown on the WhatsApp > API Setup page. The verify
# token is any string you invent — you type the same value into the webhook
# configuration screen so Meta can confirm it's really your endpoint.
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "YOUR_WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "YOUR_PHONE_NUMBER_ID")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "changeme-verify-token")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v21.0")
WHATSAPP_BOT_NAME = os.getenv("WHATSAPP_BOT_NAME", "AI Document Auditor")
