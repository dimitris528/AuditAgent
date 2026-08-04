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
        -> on checkout.session.completed, match the payer to a users row and
           flip subscription_status to "active" (which also clears
           trial_ends_at — see store.set_subscription_status).
        -> on customer.subscription.deleted, flip that customer back to
           "inactive" so a cancellation actually re-closes the paywall.

Payer matching, in order:
    1. client_reference_id — the checkout URL stamps the tenant's username, so
       this is an exact match.
    2. customer email — every account now has one (registration requires it).
    3. stripe_customer_id — for repeat checkouts that carry neither.

DEACTIVATION matches on stripe_customer_id ONLY. The asymmetry is deliberate:
a cancellation event carries no client_reference_id, and the email fallback is a
guess — guessing wrong when granting access hands a stranger a paid account,
and guessing wrong when revoking it locks a paying customer out of their own
books. Neither is acceptable, so an unmatched cancellation is logged for manual
reconciliation instead.

Status codes are chosen for Stripe's retry behaviour, which is the whole point
of getting them right — Stripe re-delivers on 5xx for ~3 days and gives up on
2xx/4xx:
    400  bad signature or unparseable body   -> never retry, it can't improve
    503  endpoint or database not configured -> retry once configuration lands
    502  database lookup/write failed        -> retry, it is transient
    200  accepted, including unknown payer   -> retry cannot identify them; the
                                                event stays in the Stripe
                                                Dashboard for manual reconcile

Idempotency: Stripe delivers at-least-once, so the same event can arrive twice.
The only mutation here is "set this row to Active", which is naturally
idempotent, so a duplicate delivery is a no-op rather than something needing an
event-id ledger.

Stripe endpoint config: point the endpoint at
    https://<accounting-api host>/api/v1/webhooks/stripe
and subscribe it to checkout.session.completed and
customer.subscription.deleted.

Local test:
    uvicorn server.main:app --reload --port 8000
    stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe
    stripe trigger checkout.session.completed
"""

import json

import stripe
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from config import STRIPE_WEBHOOK_SECRET
from server import database, store, subscription

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])

# Event that means "this tenant just paid".
CHECKOUT_COMPLETED = "checkout.session.completed"
# ...and the one that means they stopped. Renewals (invoice.payment_succeeded)
# stay UNHANDLED: they would only ever re-set a status that is already active,
# and the account is already active for as long as Stripe has not cancelled it.
SUBSCRIPTION_DELETED = "customer.subscription.deleted"


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
    kind = event.get("type")
    body = event.get("data", {}).get("object", {})
    if kind == CHECKOUT_COMPLETED:
        _activate_payer(body)
    elif kind == SUBSCRIPTION_DELETED:
        _deactivate_customer(body)
    return {"received": True}


def _require_db(action):
    if not database.is_configured():
        print(f"[ERROR] DATABASE_URL is not configured — cannot {action}.")
        raise HTTPException(status_code=503, detail="Database not configured.")


def _activate_payer(checkout):
    """Flip the paying tenant's users row to `active`. Raises HTTPException(502)
    on transient database failures so Stripe re-delivers."""
    username = checkout.get("client_reference_id")
    email = ((checkout.get("customer_details") or {}).get("email")
             or checkout.get("customer_email"))
    customer_id = checkout.get("customer")

    _require_db("activate payer")

    try:
        with database.session_scope() as session:
            user = store.find_user(session, username=username, email=email)
            if user is None and customer_id:
                # Repeat checkouts by an existing customer may carry neither a
                # client_reference_id nor a matching email.
                user = store.get_user_by_stripe_customer(session, customer_id)

            if user is None:
                print(f"[WARN] {CHECKOUT_COMPLETED} from unknown payer "
                      f"(client_reference_id={username!r}, email={email!r}) — "
                      f"no row updated; reconcile manually in Stripe.")
                return

            # Remember the Stripe customer FIRST: set_subscription_status
            # commits and refreshes, and a failure between the two writes must
            # not leave an active account that no future event can match.
            if customer_id and user.stripe_customer_id != customer_id:
                store.set_stripe_customer(session, user, customer_id)
            store.set_subscription_status(session, user, subscription.ACTIVE)
            print(f"[INFO] Subscription activated for username={user.username!r}.")
    except SQLAlchemyError as exc:
        print(f"[ERROR] Activation failed (client_reference_id={username!r}): {exc}")
        raise HTTPException(status_code=502, detail="Database write failed.")


def _deactivate_customer(sub):
    """Close the paywall again when a subscription ends.

    Matched STRICTLY by stripe_customer_id — see the module docstring for why
    the email fallback is not available on this side.
    """
    customer_id = sub.get("customer")
    if not customer_id:
        print(f"[WARN] {SUBSCRIPTION_DELETED} carried no customer id — ignored.")
        return

    _require_db("deactivate customer")

    try:
        with database.session_scope() as session:
            user = store.get_user_by_stripe_customer(session, customer_id)
            if user is None:
                print(f"[WARN] {SUBSCRIPTION_DELETED} for unknown customer "
                      f"{customer_id!r} — no row updated; reconcile manually.")
                return
            store.set_subscription_status(session, user, subscription.INACTIVE)
            print(f"[INFO] Subscription cancelled for username={user.username!r}.")
    except SQLAlchemyError as exc:
        print(f"[ERROR] Deactivation failed (customer={customer_id!r}): {exc}")
        raise HTTPException(status_code=502, detail="Database write failed.")
