"""
Stripe webhook receiver — a SEPARATE web service from the Streamlit app.

Streamlit has no request routing and cannot accept webhook POSTs, so this
tiny Flask app runs as its own Render web service (see render.yaml:
auditagent-webhook, started via gunicorn) and listens on POST /webhook.

Flow:
    Stripe -> POST /webhook
        -> signature verified against STRIPE_WEBHOOK_SECRET (reject 400)
        -> on checkout.session.completed, match the payer to a Users row
           and flip SubscriptionStatus to "Active".

Payer matching, in order:
    1. client_reference_id — app.py stamps the logged-in Username onto the
       Payment Link URL, so this is an exact tenant match.
    2. customer email — fallback; requires the OPTIONAL Email column in the
       Users table (see airtable_client.py schema notes).

A matched-but-failed Airtable write returns 502 so Stripe re-delivers the
event later (it retries for up to ~3 days); an unknown payer returns 200 —
retrying can't fix a payer we can't identify, and the event stays visible
in the Stripe Dashboard for manual reconciliation.

Stripe endpoint config: point the endpoint at
    https://<auditagent-webhook host>/webhook
and subscribe it to the checkout.session.completed event.

Local test:
    flask --app stripe_webhook run --port 5000
    stripe listen --forward-to localhost:5000/webhook
    stripe trigger checkout.session.completed
"""

import json

import stripe
from flask import Flask, abort, request

import airtable_client as db
from config import STRIPE_WEBHOOK_SECRET

app = Flask(__name__)


@app.get("/")
def health():
    """Render's health check (and a quick manual smoke test)."""
    return {"status": "ok"}


@app.post("/webhook")
def webhook():
    if not STRIPE_WEBHOOK_SECRET:
        # Fail closed: without the signing secret NOTHING can be trusted.
        print("[ERROR] STRIPE_WEBHOOK_SECRET is not set — rejecting event.")
        abort(500)
    payload = request.get_data()  # raw bytes — signature covers the exact body
    try:
        stripe.Webhook.construct_event(
            payload,
            request.headers.get("Stripe-Signature", ""),
            STRIPE_WEBHOOK_SECRET,
        )
    except (ValueError, stripe.SignatureVerificationError):
        abort(400)  # malformed payload or not signed by Stripe

    # Re-read the VERIFIED body as plain dicts: StripeObject dropped its
    # dict interface (no .get) in newer stripe-python majors, so relying on
    # it couples the handler to the SDK's accessor API.
    event = json.loads(payload)
    if event.get("type") == "checkout.session.completed":
        _activate_payer(event["data"]["object"])
    return {"received": True}


def _activate_payer(session):
    username = session.get("client_reference_id")
    email = ((session.get("customer_details") or {}).get("email")
             or session.get("customer_email"))
    try:
        user = db.find_user(username=username, email=email)
    except db.AirtableError as exc:
        print(f"[ERROR] Users lookup failed "
              f"(client_reference_id={username!r}): {exc}")
        abort(502)  # transient — let Stripe redeliver
    if user is None:
        print(f"[WARN] checkout.session.completed from unknown payer "
              f"(client_reference_id={username!r}, email={email!r}) — "
              f"no Users row updated; reconcile manually in Stripe.")
        return
    try:
        db.set_subscription_status(user["id"], "Active")
    except db.AirtableError as exc:
        print(f"[ERROR] Activation write failed for "
              f"Username={user['fields'].get('Username')!r}: {exc}")
        abort(502)  # transient — let Stripe redeliver
    print(f"[INFO] Subscription activated for "
          f"Username={user['fields'].get('Username')!r}.")
