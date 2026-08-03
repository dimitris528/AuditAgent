"""
Stripe billing webhook, mounted by server/main.py at

    POST /api/v1/webhooks/stripe

This replaces the retired standalone Flask service (stripe_webhook.py). That
service existed only because Streamlit cannot route requests; FastAPI can, so
the receiver now lives inside the API and shares its Airtable client, logging
and deploy.

Flow:
    Stripe -> POST /api/v1/webhooks/stripe
        -> signature verified against STRIPE_WEBHOOK_SECRET (reject 400)
        -> on checkout.session.completed, match the payer to a Users row and
           flip SubscriptionStatus to "Active" (which also clears TrialExpiry).

Payer matching, in order:
    1. client_reference_id — the checkout URL stamps the tenant's Username, so
       this is an exact match.
    2. customer email — fallback; needs the OPTIONAL Email column in the Users
       table (see airtable_client.py schema notes).

Status codes are chosen for Stripe's retry behaviour, which is the whole point
of getting them right — Stripe re-delivers on 5xx for ~3 days and gives up on
2xx/4xx:
    400  bad signature or unparseable body   -> never retry, it can't improve
    503  endpoint not configured             -> retry once the secret is set
    502  Airtable lookup/write failed        -> retry, it is transient
    200  accepted, including unknown payer   -> retry cannot identify them; the
                                                event stays in the Stripe
                                                Dashboard for manual reconcile

Idempotency: Stripe delivers at-least-once, so the same event can arrive twice.
The only mutation here is "set this row to Active", which is naturally
idempotent, so a duplicate delivery is a no-op rather than something needing an
event-id ledger.

Stripe endpoint config: point the endpoint at
    https://<accounting-api host>/api/v1/webhooks/stripe
and subscribe it to checkout.session.completed.

Local test:
    uvicorn server.main:app --reload --port 8000
    stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe
    stripe trigger checkout.session.completed
"""

import json

import stripe
from fastapi import APIRouter, HTTPException, Request

import airtable_client as db
from config import STRIPE_WEBHOOK_SECRET

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])

# Event that means "this tenant just paid". Renewals
# (invoice.payment_succeeded) and cancellations are deliberately NOT handled:
# those events carry no client_reference_id, so the only way to match a payer
# would be the email fallback, and a wrong match there would hand a stranger a
# paid account. Add them only alongside a stored Stripe customer id.
CHECKOUT_COMPLETED = "checkout.session.completed"


@router.post("/stripe")
async def stripe_webhook(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        # Fail closed: without the signing secret NOTHING can be trusted.
        print("[ERROR] STRIPE_WEBHOOK_SECRET is not set — rejecting event.")
        raise HTTPException(status_code=503, detail="Webhook not configured.")

    # Raw bytes: the signature covers the exact body, so anything that
    # re-serializes the JSON (even key reordering) would invalidate it.
    payload = await request.body()
    signature = request.headers.get("Stripe-Signature", "")
    try:
        stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
    except (ValueError, AttributeError, stripe.SignatureVerificationError) as exc:
        # ValueError: body is not JSON. SignatureVerificationError: not from
        # Stripe, or outside the 5-minute replay tolerance. AttributeError:
        # construct_event inspects event.object, which blows up on a payload
        # that parses but is not shaped like an event — a 400, not a 500.
        print(f"[WARN] Rejected Stripe webhook: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=400, detail="Invalid payload or signature.")

    # Re-read the VERIFIED body as plain dicts: StripeObject dropped its dict
    # interface (no .get) in newer stripe-python majors, so relying on it
    # couples this handler to the SDK's accessor API.
    event = json.loads(payload)
    if event.get("type") == CHECKOUT_COMPLETED:
        _activate_payer(event.get("data", {}).get("object", {}))
    return {"received": True}


def _activate_payer(session):
    """Flip the paying tenant's Users row to Active. Raises HTTPException(502)
    on transient Airtable failures so Stripe re-delivers."""
    username = session.get("client_reference_id")
    email = ((session.get("customer_details") or {}).get("email")
             or session.get("customer_email"))

    try:
        user = db.find_user(username=username, email=email)
    except db.AirtableError as exc:
        print(f"[ERROR] Users lookup failed (client_reference_id={username!r}): {exc}")
        raise HTTPException(status_code=502, detail="Airtable lookup failed.")

    if user is None:
        print(f"[WARN] {CHECKOUT_COMPLETED} from unknown payer "
              f"(client_reference_id={username!r}, email={email!r}) — "
              f"no Users row updated; reconcile manually in Stripe.")
        return

    try:
        db.set_subscription_status(user["id"], "Active")
    except db.AirtableError as exc:
        print(f"[ERROR] Activation write failed for "
              f"Username={user['fields'].get('Username')!r}: {exc}")
        raise HTTPException(status_code=502, detail="Airtable write failed.")

    print(f"[INFO] Subscription activated for "
          f"Username={user['fields'].get('Username')!r}.")
