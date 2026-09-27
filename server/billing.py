"""
Billing API — subscription status and Stripe Checkout, mounted by
server/main.py:

    GET  /api/v1/billing/status     what the /billing page renders
    POST /api/v1/billing/checkout   -> { url } to redirect the browser to
    POST /api/v1/billing/portal     -> { url } for Stripe's own billing portal
    POST /api/v1/billing/cancel     schedule the subscription to end

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

The customer portal
-------------------
/portal is the after-sale half: changing the card, downloading invoices,
cancelling. Stripe hosts all of it, which is the point — none of those screens
exist here, and a card number must never reach this server.

It needs only STRIPE_SECRET_KEY (there is no Price involved), so it is
configured separately from checkout.

It opens for EVERY signed-in tenant, whatever their status — active, on trial,
or lapsed. Stripe's portal is addressed by customer, so an account that has
never been through checkout has one created for it on the spot (_ensure_customer
below). That costs nothing: a Stripe customer with no subscription is a free
record, and it is the same customer checkout would have created later. The
alternative — refusing the button until the first payment — hides the card and
invoice screens from precisely the people trying to start paying.

Which customer is opened is read from the signed-in tenant's row and NEVER from
the request. That id is the only thing identifying whose billing this is, and a
portal session created for the wrong customer would hand one tenant another
tenant's invoices and card.

Cancellation
------------
/cancel sets `cancel_at_period_end` on the tenant's Stripe subscription rather
than deleting it. The tenant has already paid to the end of the current period,
so ending access the instant they click Cancel would be charging for days they
cannot use; it also leaves the decision reversible, which Stripe's portal
supports and the webhook syncs back.

The users row is stamped with the effective date here as well as by the webhook.
The webhook stays authoritative — it is what eventually flips the account
inactive — but it can land seconds after the click, and the page the user
returns to has to already show what they just did.
"""

import stripe
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from config import APP_BASE_URL, STRIPE_PRICE_ID, STRIPE_SECRET_KEY
from server import database, demo_account as demo, deps, errors, store, subscription

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])

# Where Stripe returns the browser. The session id is stamped on the success URL
# purely so the page can show a confirmation; it is NOT treated as proof of
# payment (see the module docstring).
_SUCCESS_URL = f"{APP_BASE_URL}/billing?checkout=success&session_id={{CHECKOUT_SESSION_ID}}"
_CANCEL_URL = f"{APP_BASE_URL}/billing?checkout=cancelled"

# Where Stripe's "← Return to …" link sends the browser back to.
_PORTAL_RETURN_URL = f"{APP_BASE_URL}/billing"


def _get(obj, key, default=None):
    """Read one field off whatever the Stripe SDK handed back.

    StripeObject dropped its dict interface (no .get) in newer majors, while the
    webhook path and the test doubles deal in plain dicts — so neither accessor
    works on its own, and every call site was otherwise repeating the same
    isinstance dance inline.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def is_configured():
    return bool(STRIPE_SECRET_KEY and STRIPE_PRICE_ID)


def portal_is_configured():
    """The portal sells nothing, so it needs no Price — only the API key.

    Kept separate from is_configured() so a deployment that has a key but no
    Price can still let existing subscribers manage their card, instead of
    being told billing is switched off.
    """
    return bool(STRIPE_SECRET_KEY)


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
            cancel_at = tenant.subscription_cancel_at
            # Pending only while the subscription it belongs to is still
            # running. Once the account is inactive the cancellation has
            # happened and is no longer something to warn about.
            pending = bool(cancel_at) and state.status == subscription.ACTIVE
            return {
                "username": tenant.username,
                "email": tenant.email,
                "has_stripe_customer": bool(tenant.stripe_customer_id),
                # The portal opens for anyone once the server has a key: a
                # tenant without a Stripe customer gets one created on the way
                # in (see /portal), so this no longer depends on having paid.
                "portal_enabled": portal_is_configured(),
                # There is a live Stripe subscription to cancel only if the
                # account is ACTIVE — our own free trial is not one — and it has
                # not been cancelled already.
                "can_cancel": (portal_is_configured()
                               and state.status == subscription.ACTIVE
                               and not pending),
                "pending_cancellation": pending,
                "cancel_at": subscription.iso_utc(cancel_at) if pending else None,
                **state.to_dict(),
                **_stripe_status(),
            }
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise errors.db_error(exc)


@router.post("/checkout")
def create_checkout_session(user: str = Depends(demo.forbid_demo_user)):
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
        raise errors.db_error(exc)

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

    url = _get(checkout, "url")
    if not url:
        raise HTTPException(
            status_code=502, detail="Το Stripe δεν επέστρεψε διεύθυνση πληρωμής.")

    # The customer id exists from this moment even though nothing has been paid
    # yet. Storing it now means a checkout the user abandons and retries reuses
    # the same Stripe customer instead of creating a duplicate every attempt.
    stripe_customer = _get(checkout, "customer")
    if stripe_customer and not customer_id:
        try:
            with database.session_scope() as session:
                tenant = store.get_user_by_username(session, username)
                if tenant is not None and not tenant.stripe_customer_id:
                    store.set_stripe_customer(session, tenant, stripe_customer)
        except SQLAlchemyError as exc:
            # Not fatal: the webhook can still match on client_reference_id.
            print(f"[WARN] Could not store Stripe customer id: {exc}")

    return {"url": url, "id": _get(checkout, "id")}


def _ensure_customer(username, email, customer_id):
    """The tenant's Stripe customer id, creating the customer if there is none.

    This is what lets the portal open for an account that has never paid. The
    id is persisted immediately, so the customer is reused by a later checkout
    instead of being duplicated — and the metadata carries the username, which
    is what makes an orphaned customer traceable back to an account when
    reconciling by hand in the Stripe dashboard.

    A failure to persist is logged, not raised: the portal session about to be
    created is still perfectly valid, and refusing to open it because a write
    failed would turn a bookkeeping problem into a broken button.
    """
    if customer_id:
        return customer_id

    customer = stripe.Customer.create(
        email=email or None,
        metadata={"username": username},
    )
    customer_id = _get(customer, "id")
    if not customer_id:
        raise HTTPException(
            status_code=502, detail="Το Stripe δεν επέστρεψε πελάτη χρέωσης.")

    try:
        with database.session_scope() as session:
            tenant = store.get_user_by_username(session, username)
            if tenant is not None and not tenant.stripe_customer_id:
                store.set_stripe_customer(session, tenant, customer_id)
    except SQLAlchemyError as exc:
        print(f"[WARN] Could not store Stripe customer id for "
              f"username={username!r}: {exc}")
    return customer_id


@router.post("/portal")
def create_portal_session(user: str = Depends(demo.forbid_demo_user)):
    """Open Stripe's hosted customer portal and hand back the URL.

    Open to EVERY signed-in tenant, whatever their subscription says. Someone
    whose card was declined, or whose subscription Stripe has already
    cancelled, is precisely the person who needs to update a card or read an
    invoice — and they are the one the paywall is blocking. Someone still on
    the free trial is the person about to become a customer. Neither is made to
    earn access to their own billing screens first: a tenant with no Stripe
    customer has one created here (see _ensure_customer).

    Sessions are single-use and short-lived, so the URL is generated per click
    and never stored.
    """
    if not portal_is_configured():
        raise HTTPException(
            status_code=503,
            detail="Οι πληρωμές δεν έχουν ρυθμιστεί (ορίστε STRIPE_SECRET_KEY).")
    deps.require_db()

    try:
        with database.session_scope() as session:
            tenant, _state = deps.subscription_state(session, user)
            username = tenant.username
            email = tenant.email
            customer_id = tenant.stripe_customer_id
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise errors.db_error(exc)

    try:
        # Per call, for the same reason as in the checkout above: the key is
        # read from config at import time.
        stripe.api_key = STRIPE_SECRET_KEY
        customer_id = _ensure_customer(username, email, customer_id)
        portal = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=_PORTAL_RETURN_URL,
        )
    except stripe.StripeError as exc:
        # The commonest failure here is a live-mode key against a test-mode
        # customer id (or the reverse), which Stripe reports as "No such
        # customer". Logged in full; the user gets something actionable.
        print(f"[ERROR] Stripe portal failed for username={username!r} "
              f"customer={customer_id!r}: {exc}")
        raise HTTPException(
            status_code=502,
            detail="Δεν ήταν δυνατό το άνοιγμα της διαχείρισης συνδρομής. "
                   "Δοκιμάστε ξανά.")

    url = _get(portal, "url")
    if not url:
        raise HTTPException(
            status_code=502,
            detail="Το Stripe δεν επέστρεψε διεύθυνση διαχείρισης.")
    return {"url": url}


# Stripe subscription states that still have something left to cancel. One that
# is already "canceled" or "incomplete_expired" is finished, and asking Stripe
# to cancel it again is an error rather than a no-op.
_CANCELABLE = ("active", "trialing", "past_due", "unpaid", "incomplete")


def _find_subscription(customer_id):
    """The tenant's cancelable Stripe subscription, or None.

    Looked up by customer rather than stored on the users row. The row would
    have to be kept in step with every plan change, upgrade and resubscribe
    Stripe performs on its own — and a stale id here would cancel nothing, or
    cancel the wrong thing. Asking Stripe costs one request on a button nobody
    presses twice.
    """
    listing = stripe.Subscription.list(customer=customer_id, status="all", limit=20)
    for sub in _get(listing, "data") or []:
        if _get(sub, "status") in _CANCELABLE:
            return sub
    return None


@router.post("/cancel")
def cancel_subscription(user: str = Depends(demo.forbid_demo_user)):
    """Schedule the tenant's subscription to end when the paid period does.

    `cancel_at_period_end`, never an immediate delete — see the module
    docstring. The account therefore stays ACTIVE and keeps write access until
    the date this returns; nothing about the paywall changes today.

    Idempotent: cancelling something already cancelled returns the existing date
    instead of erroring, so a double-click, a retry, or a cancellation made in
    Stripe's portal a moment earlier all land in the same place.
    """
    if not portal_is_configured():
        raise HTTPException(
            status_code=503,
            detail="Οι πληρωμές δεν έχουν ρυθμιστεί (ορίστε STRIPE_SECRET_KEY).")
    deps.require_db()

    try:
        with database.session_scope() as session:
            tenant, _state = deps.subscription_state(session, user)
            username = tenant.username
            customer_id = tenant.stripe_customer_id
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise errors.db_error(exc)

    # No customer means no subscription was ever bought. Unlike the portal,
    # there is nothing useful to create here — cancelling nothing is a mistake
    # worth reporting, not a request to fulfil.
    if not customer_id:
        raise HTTPException(
            status_code=409, detail="Δεν υπάρχει ενεργή συνδρομή προς ακύρωση.")

    try:
        stripe.api_key = STRIPE_SECRET_KEY
        sub = _find_subscription(customer_id)
        if sub is None:
            raise HTTPException(
                status_code=409, detail="Δεν υπάρχει ενεργή συνδρομή προς ακύρωση.")
        if not _get(sub, "cancel_at_period_end"):
            sub = stripe.Subscription.modify(
                _get(sub, "id"), cancel_at_period_end=True)
    except HTTPException:
        raise
    except stripe.StripeError as exc:
        # English, and can quote ids — logged in full, summarised in the reply.
        print(f"[ERROR] Stripe cancellation failed for username={username!r} "
              f"customer={customer_id!r}: {exc}")
        raise HTTPException(
            status_code=502,
            detail="Δεν ήταν δυνατή η ακύρωση της συνδρομής. Δοκιμάστε ξανά.")

    # `cancel_at` is what Stripe fills in once the cancellation is scheduled;
    # current_period_end is the same instant and is the fallback for an API
    # version that does not set it.
    cancel_at = subscription.from_unix(
        _get(sub, "cancel_at") or _get(sub, "current_period_end"))

    try:
        with database.session_scope() as session:
            tenant = store.get_user_by_username(session, username)
            if tenant is not None:
                store.set_subscription_cancel_at(session, tenant, cancel_at)
    except SQLAlchemyError as exc:
        # Stripe has accepted the cancellation, which is the part that matters.
        # The webhook will stamp the row on its own.
        print(f"[WARN] Cancellation stored in Stripe but not locally for "
              f"username={username!r}: {exc}")

    print(f"[INFO] Subscription set to cancel at period end for "
          f"username={username!r} (cancel_at={cancel_at!r}).")
    return {
        "pending_cancellation": True,
        "cancel_at": subscription.iso_utc(cancel_at),
    }
