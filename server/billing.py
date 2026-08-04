"""
Billing API — subscription status and Stripe Checkout, mounted by
server/main.py:

    GET  /api/v1/billing/status     what the /billing page renders
    POST /api/v1/billing/checkout   -> { url } to redirect the browser to

The other half of the loop is server/webhooks.py, which receives Stripe's
callback and is what actually flips the account to `active`. Nothing here marks
anyone as paid: this endpoint only opens a checkout, and a browser that reaches
the success URL has not necessarily paid (it can be typed by hand). The webhook
is the single writer of `active`.

Checkout session, field by field
--------------------------------
    mode="subscription"     the Price is recurring; "payment" would take one
                            payment and never renew.
    client_reference_id     the tenant's USERNAME. This is the primary key the
                            webhook matches the payer on, and the only one that
                            is exact — email matching is a fallback that can go
                            wrong. Never omit it.
    customer / customer_email
                            mutually exclusive in the Stripe API — passing both
                            is a 400 from Stripe. A returning payer is
                            identified by their stored customer id; a first-time
                            one gets their email pre-filled.

Without STRIPE_SECRET_KEY / STRIPE_PRICE_ID the endpoint answers 503 and
/status reports `stripe_configured: false`, so the UI can explain that billing
is not set up rather than offering a button that only ever errors.
"""

import stripe
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from config import APP_BASE_URL, STRIPE_PRICE_ID, STRIPE_SECRET_KEY
from server import database, deps, store, subscription

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])

# Where Stripe returns the browser. The session id is stamped on the success URL
# purely so the page can show a confirmation; it is NOT treated as proof of
# payment (see the module docstring).
_SUCCESS_URL = f"{APP_BASE_URL}/billing?checkout=success&session_id={{CHECKOUT_SESSION_ID}}"
_CANCEL_URL = f"{APP_BASE_URL}/billing?checkout=cancelled"


def is_configured():
    return bool(STRIPE_SECRET_KEY and STRIPE_PRICE_ID)


def _stripe_status():
    """The configuration flags the billing page needs to decide what to show."""
    return {
        "stripe_configured": is_configured(),
        "checkout_enabled": is_configured() and database.is_configured(),
    }


@router.get("/status")
def billing_status(user: str = Depends(deps.get_current_user)):
    """Current subscription for the signed-in tenant.

    Resolving it here also PERSISTS an expiry that has just passed (via
    store.refresh_subscription), so merely opening the billing page brings the
    stored status up to date.
    """
    deps.require_db()
    try:
        with database.session_scope() as session:
            tenant, state = deps.subscription_state(session, user)
            return {
                "username": tenant.username,
                "email": tenant.email,
                "has_stripe_customer": bool(tenant.stripe_customer_id),
                **state.to_dict(),
                **_stripe_status(),
            }
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


@router.post("/checkout")
def create_checkout_session(user: str = Depends(deps.get_current_user)):
    """Open a Stripe Checkout session and hand back the URL to redirect to.

    Idempotent enough for a button: Stripe sessions are cheap and expire on
    their own, so a double click costs an abandoned session, not a double
    charge. An account that is ALREADY paid is refused (409) rather than sold a
    second subscription.
    """
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail="Οι πληρωμές δεν έχουν ρυθμιστεί (ορίστε STRIPE_SECRET_KEY "
                   "και STRIPE_PRICE_ID).")
    deps.require_db()

    try:
        with database.session_scope() as session:
            tenant, state = deps.subscription_state(session, user)
            if state.status == subscription.ACTIVE:
                raise HTTPException(
                    status_code=409, detail="Η συνδρομή είναι ήδη ενεργή.")
            username = tenant.username
            email = tenant.email
            customer_id = tenant.stripe_customer_id
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")

    params = {
        "mode": "subscription",
        "line_items": [{"price": STRIPE_PRICE_ID, "quantity": 1}],
        # The exact key the webhook matches the payer on.
        "client_reference_id": username,
        "success_url": _SUCCESS_URL,
        "cancel_url": _CANCEL_URL,
        "allow_promotion_codes": True,
    }
    # Mutually exclusive — see the module docstring.
    if customer_id:
        params["customer"] = customer_id
    elif email:
        params["customer_email"] = email

    try:
        # Set per call rather than at import: the key is read from config at
        # import time, and a module-level assignment would bake in whatever was
        # (or was not) configured then.
        stripe.api_key = STRIPE_SECRET_KEY
        checkout = stripe.checkout.Session.create(**params)
    except stripe.StripeError as exc:
        # Stripe's own message is not shown to the user: it is English, and can
        # quote ids. Logged in full, summarised in the response.
        print(f"[ERROR] Stripe checkout failed for username={username!r}: {exc}")
        raise HTTPException(
            status_code=502,
            detail="Δεν ήταν δυνατή η έναρξη της πληρωμής. Δοκιμάστε ξανά.")

    url = checkout.get("url") if isinstance(checkout, dict) else getattr(checkout, "url", None)
    if not url:
        raise HTTPException(
            status_code=502, detail="Το Stripe δεν επέστρεψε διεύθυνση πληρωμής.")

    # The customer id exists from this moment even though nothing has been paid
    # yet. Storing it now means a checkout the user abandons and retries reuses
    # the same Stripe customer instead of creating a duplicate every attempt.
    stripe_customer = (checkout.get("customer") if isinstance(checkout, dict)
                       else getattr(checkout, "customer", None))
    if stripe_customer and not customer_id:
        try:
            with database.session_scope() as session:
                tenant = store.get_user_by_username(session, username)
                if tenant is not None and not tenant.stripe_customer_id:
                    store.set_stripe_customer(session, tenant, stripe_customer)
        except SQLAlchemyError as exc:
            # Not fatal: the webhook can still match on client_reference_id.
            print(f"[WARN] Could not store Stripe customer id: {exc}")

    return {"url": url, "id": checkout.get("id") if isinstance(checkout, dict)
            else getattr(checkout, "id", None)}
