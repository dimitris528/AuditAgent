"""
The 14-day trial, the paywall, and the Stripe round trip.

Three things are worth stating about how these tests are built, because each
one was a choice:

* The webhook payloads are SIGNED the way Stripe signs them (hmac-sha256 over
  "<timestamp>.<body>") rather than stubbing stripe.Webhook.construct_event.
  The signature check is the only thing standing between a public endpoint and
  anyone who can POST JSON, so a suite that mocks it out tests everything except
  the part that matters.

* Trial expiry is simulated by moving `trial_ends_at` INTO THE PAST rather than
  by freezing the clock. It exercises the same code path a real deployment
  takes at 2am and needs no time-travel library.

* Writes are checked on more than one endpoint. The gate is per-endpoint (each
  handler passes write=True), so a single assertion would pass happily while
  three other write paths stayed wide open.
"""

import datetime as dt
import hashlib
import hmac
import json
import time

import pytest
import stripe

from server import billing, database, store, subscription
from server.models import User
from tests.conftest import WEBHOOK_SECRET

WEBHOOK_URL = "/api/v1/webhooks/stripe"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _user(username="tester"):
    """The tenant's row, re-read from the database."""
    with database.session_scope() as session:
        row = store.get_user_by_username(session, username)
        assert row is not None
        # Detached copy: the session closes on the way out, and touching a
        # lazily-loaded attribute afterwards would raise.
        return User(**row.model_dump())


def _patch_user(**fields):
    with database.session_scope() as session:
        row = store.get_user_by_username(session, "tester")
        for key, value in fields.items():
            setattr(row, key, value)
        session.add(row)
        session.commit()


def _expire_trial(days_ago=1):
    """Put the trial deadline in the past, leaving the stored status alone —
    exactly the state a real account is in the morning after it lapses."""
    _patch_user(trial_ends_at=subscription.utcnow() - dt.timedelta(days=days_ago))


def _envelope(kind, obj):
    """A Stripe event, with the envelope fields real deliveries carry.

    `object: "event"` is not decoration: stripe.Webhook.construct_event builds
    an Event out of the parsed body and raises AttributeError without it, which
    the handler (correctly) turns into a 400. A payload missing it would test a
    rejection path rather than the happy one.
    """
    return {
        "id": f"evt_{kind.replace('.', '_')}",
        "object": "event",
        "api_version": "2024-06-20",
        "created": int(time.time()),
        "livemode": False,
        "type": kind,
        "data": {"object": obj},
    }


def _signed(event, secret=WEBHOOK_SECRET, timestamp=None):
    """(body, headers) for a Stripe webhook delivery."""
    body = json.dumps(event).encode()
    stamp = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode(), f"{stamp}.".encode() + body,
                   hashlib.sha256).hexdigest()
    return body, {"Stripe-Signature": f"t={stamp},v1={mac}",
                  "Content-Type": "application/json"}


def _checkout_event(username="tester", email="tester@example.com",
                    customer="cus_test_123"):
    return _envelope("checkout.session.completed", {
        "id": "cs_test_1",
        "object": "checkout.session",
        "client_reference_id": username,
        "customer": customer,
        "customer_details": {"email": email},
        "payment_status": "paid",
        "status": "complete",
    })


def _cancellation_event(customer="cus_test_123"):
    return _envelope("customer.subscription.deleted", {
        "id": "sub_test_1",
        "object": "subscription",
        "customer": customer,
        "status": "canceled",
    })


def _pay(api, **kwargs):
    """Drive a full checkout.session.completed through the real endpoint."""
    body, headers = _signed(_checkout_event(**kwargs))
    res = api.post(WEBHOOK_URL, content=body, headers=headers)
    assert res.status_code == 200, res.text
    return res


# A minimal write on each of the gated surfaces. Parametrised so a newly gated
# endpoint can be added in one line.
WRITES = [
    ("POST", "/api/v1/clients", {"name": "Νέος Πελάτης"}),
    ("POST", "/api/transactions",
     {"client": "Πελάτης Α", "amount": 100, "type": "Έσοδο"}),
    ("DELETE", "/api/transactions/1", None),
]


def _send(api, method, url, payload):
    if method == "DELETE":
        return api.delete(url)
    return api.post(url, json=payload)


# --------------------------------------------------------------------------
# The policy, in isolation
# --------------------------------------------------------------------------
def test_resolve_reads_a_live_trial_as_trialing():
    ends = subscription.utcnow() + dt.timedelta(days=5)
    state = subscription.resolve(subscription.TRIALING, ends)
    assert state.status == subscription.TRIALING
    assert state.allows_writes is True
    assert state.days_left == 5


def test_resolve_expires_a_trial_that_has_passed():
    ends = subscription.utcnow() - dt.timedelta(minutes=1)
    state = subscription.resolve(subscription.TRIALING, ends)
    assert state.status == subscription.INACTIVE
    assert state.allows_writes is False
    assert state.days_left == 0
    # …and the change is worth writing back to the row.
    assert state.needs_persisting is True


def test_a_paid_account_has_no_trial_date_and_never_expires():
    state = subscription.resolve(subscription.ACTIVE, None)
    assert state.status == subscription.ACTIVE
    assert state.allows_writes is True
    assert state.needs_persisting is False


def test_legacy_title_case_active_with_a_trial_date_is_a_trial():
    """The rows written before this module existed carry "Active" TOGETHER with
    a trial date. Reading them as permanently paid would give every one of them
    a free account for life."""
    ends = subscription.utcnow() + dt.timedelta(days=3)
    state = subscription.resolve("Active", ends)
    assert state.status == subscription.TRIALING
    assert state.needs_persisting is True


def test_an_explicit_inactive_wins_over_a_leftover_trial_date():
    """A cancellation must not be undone by a date nobody cleared."""
    ends = subscription.utcnow() + dt.timedelta(days=30)
    assert subscription.resolve("inactive", ends).status == subscription.INACTIVE


@pytest.mark.parametrize("stored", ["", None, "bananas", "past_due", "canceled"])
def test_unknown_or_failed_statuses_fail_closed(stored):
    assert subscription.resolve(stored, None).allows_writes is False


def test_naive_datetimes_are_read_as_utc():
    """SQLite hands back a NAIVE datetime for the TIMESTAMPTZ column; comparing
    that against an aware `now` would raise TypeError rather than expire
    anyone."""
    naive = (subscription.utcnow() + dt.timedelta(days=2)).replace(tzinfo=None)
    assert subscription.resolve(subscription.TRIALING, naive).status == \
        subscription.TRIALING


def test_remaining_days_rounds_up():
    """An account registered a minute ago reads "14 ημέρες", not 13 — the user
    counts the day they are standing in."""
    now = subscription.utcnow()
    assert subscription.remaining_days(now + dt.timedelta(days=14) - dt.timedelta(minutes=1), now) == 14
    assert subscription.remaining_days(now + dt.timedelta(hours=1), now) == 1
    assert subscription.remaining_days(now - dt.timedelta(days=3), now) == 0


# --------------------------------------------------------------------------
# Registration grants the trial
# --------------------------------------------------------------------------
def test_registration_starts_a_14_day_trial(api):
    """The `api` fixture registers the tenant, so its state IS the assertion."""
    row = _user()
    assert row.subscription_status == subscription.TRIALING
    assert row.trial_ends_at is not None

    left = subscription.as_utc(row.trial_ends_at) - subscription.utcnow()
    # A whole day of slack: the test only cares that it is 14 days and not 15
    # or none at all.
    assert dt.timedelta(days=13) < left <= dt.timedelta(days=14)


def test_registration_response_reports_the_trial(api):
    res = api.post("/api/v1/auth/register", json={
        "username": "second",
        "email": "second@example.com",
        "password": "correct-horse-battery",
    })
    assert res.status_code == 201, res.text
    sub = res.json()["subscription"]
    assert sub["status"] == "trialing"
    assert sub["allows_writes"] is True
    assert sub["days_left"] == 14
    assert sub["trial_days"] == 14
    assert sub["trial_ends_at"].endswith("Z")


def test_create_user_grants_a_trial_by_default(api):
    """The trial is the default, not something each caller has to remember —
    and asking for "trialing" without a date still gets one, rather than a row
    that is inactive from its first request."""
    with database.session_scope() as session:
        # Read inside the session: the rows detach on the way out.
        for username in ("bare", "asked"):
            kwargs = ({} if username == "bare"
                      else {"subscription_status": "trialing"})
            row = store.create_user(session, username, f"{username}@example.com",
                                    "x", **kwargs)
            assert row.subscription_status == subscription.TRIALING
            assert row.trial_ends_at is not None

        # The Airtable backfill's shape: a status, no trial date => paid.
        legacy = store.create_user(session, "legacy", "legacy@example.com", "x",
                                   subscription_status="Active")
        assert legacy.subscription_status == subscription.ACTIVE
        assert legacy.trial_ends_at is None


def test_a_trialing_tenant_can_write(api):
    assert api.post("/api/v1/clients", json={"name": "Πελάτης Α"}).status_code == 201
    assert api.post("/api/transactions", json={
        "client": "Πελάτης Α", "amount": 100, "type": "Έσοδο",
    }).status_code == 201


# --------------------------------------------------------------------------
# Expiry and the paywall
# --------------------------------------------------------------------------
def test_an_expired_trial_flips_itself_to_inactive_on_the_next_request(api):
    _expire_trial()
    assert _user().subscription_status == subscription.TRIALING  # not yet seen

    res = api.get("/api/dashboard")
    assert res.status_code == 200
    assert res.json()["subscription"]["status"] == "inactive"

    # Persisted, not merely computed — that is what "automatically update
    # subscription_status" means.
    row = _user()
    assert row.subscription_status == subscription.INACTIVE
    # The date is KEPT so the billing page can say when the trial ended.
    assert row.trial_ends_at is not None


@pytest.mark.parametrize("method,url,payload", WRITES)
def test_writes_are_blocked_once_the_trial_expires(api, method, url, payload):
    _expire_trial()
    res = _send(api, method, url, payload)
    assert res.status_code == 402, f"{method} {url} was allowed: {res.text}"
    detail = res.json()["detail"]
    assert detail["code"] == "subscription_inactive"
    # The UI redirects on this rather than pattern-matching a Greek sentence.
    assert detail["billing_url"] == "/billing"
    assert detail["subscription"]["allows_writes"] is False


def test_settlement_is_blocked_once_the_trial_expires(api):
    """Settling a debt writes revenue, so it is a write however it reads."""
    created = api.post("/api/transactions", json={
        "client": "Πελάτης Α", "amount": 500, "type": "Χρεωστούμενο",
    })
    assert created.status_code == 201
    debt_id = created.json()["id"]

    _expire_trial()
    assert api.post(f"/api/v1/transactions/{debt_id}/settle", json={}).status_code == 402


def test_reads_still_work_for_an_expired_tenant(api):
    """An expired tenant still owns their books. The paywall takes away the
    ability to ADD, not the ability to look."""
    api.post("/api/v1/clients", json={"name": "Πελάτης Α"})
    _expire_trial()

    assert api.get("/api/dashboard").status_code == 200
    assert api.get("/api/transactions").status_code == 200
    assert api.get("/api/v1/clients").status_code == 200
    assert api.get("/api/v1/billing/status").status_code == 200


def test_scanning_is_refused_before_it_costs_an_api_call(api):
    """OCR writes nothing but bills a real request, so it is gated too — and
    the 402 has to arrive INSTEAD of the extraction, not after it."""
    _expire_trial()
    res = api.post("/api/v1/documents/scan",
                   files={"file": ("invoice.pdf", b"%PDF-1.4", "application/pdf")})
    # 503 (no ANTHROPIC_API_KEY in the suite) is checked first and is the
    # cheaper refusal; either way nothing was scanned.
    assert res.status_code in (402, 503)


def test_billing_status_reports_a_lapsed_trial(api):
    _expire_trial(days_ago=2)
    body = api.get("/api/v1/billing/status").json()
    assert body["status"] == "inactive"
    assert body["allows_writes"] is False
    assert body["days_left"] == 0
    assert body["trial_ends_at"] is not None
    assert body["stripe_configured"] is False  # not set in the suite


def test_billing_status_reports_a_live_trial(api):
    body = api.get("/api/v1/billing/status").json()
    assert body["status"] == "trialing"
    assert body["is_trialing"] is True
    assert body["days_left"] == 14
    assert body["ending_soon"] is False
    assert body["username"] == "tester"


def test_a_trial_in_its_last_days_is_flagged_ending_soon(api):
    _patch_user(trial_ends_at=subscription.utcnow() + dt.timedelta(days=2))
    assert api.get("/api/v1/billing/status").json()["ending_soon"] is True


# --------------------------------------------------------------------------
# Stripe webhook
# --------------------------------------------------------------------------
def test_checkout_completed_activates_the_account(api):
    _expire_trial()
    assert api.post("/api/v1/clients", json={"name": "Α"}).status_code == 402

    _pay(api)

    row = _user()
    assert row.subscription_status == subscription.ACTIVE
    # Cleared, and that is load-bearing: a leftover date would expire the
    # paying account again the moment it passed.
    assert row.trial_ends_at is None
    assert row.stripe_customer_id == "cus_test_123"

    # …and the writes that were refused a moment ago now succeed.
    assert api.post("/api/v1/clients", json={"name": "Α"}).status_code == 201


def test_activation_is_idempotent(api):
    """Stripe delivers at least once; a redelivery must be a no-op."""
    _pay(api)
    _pay(api)
    assert _user().subscription_status == subscription.ACTIVE


def test_a_forged_signature_is_rejected(api):
    body, headers = _signed(_checkout_event(), secret="whsec_not_the_real_one")
    assert api.post(WEBHOOK_URL, content=body, headers=headers).status_code == 400
    assert _user().subscription_status == subscription.TRIALING


def test_a_replayed_delivery_outside_the_tolerance_is_rejected(api):
    """Stripe's own 5-minute window — an old capture must not be replayable."""
    old = int(time.time()) - 3600
    body, headers = _signed(_checkout_event(), timestamp=old)
    assert api.post(WEBHOOK_URL, content=body, headers=headers).status_code == 400
    assert _user().subscription_status == subscription.TRIALING


def test_an_unsigned_body_is_rejected(api):
    body = json.dumps(_checkout_event()).encode()
    res = api.post(WEBHOOK_URL, content=body,
                   headers={"Content-Type": "application/json"})
    assert res.status_code == 400


def test_an_unknown_payer_is_accepted_but_changes_nothing(api):
    """200 on purpose: retrying cannot make the payer identifiable, and a 5xx
    would have Stripe redeliver for three days. The event stays in the
    dashboard for manual reconciliation."""
    body, headers = _signed(_checkout_event(username="nobody",
                                            email="nobody@example.com",
                                            customer="cus_unknown"))
    assert api.post(WEBHOOK_URL, content=body, headers=headers).status_code == 200
    assert _user().subscription_status == subscription.TRIALING


def test_an_unrelated_event_is_ignored(api):
    """Renewals are deliberately unhandled: they would only re-set a status that
    is already active."""
    body, headers = _signed(_envelope("invoice.payment_succeeded", {
        "id": "in_1", "object": "invoice", "customer": "cus_test_123"}))
    assert api.post(WEBHOOK_URL, content=body, headers=headers).status_code == 200
    assert _user().subscription_status == subscription.TRIALING


def test_a_cancelled_subscription_closes_the_paywall_again(api):
    _pay(api)
    assert _user().subscription_status == subscription.ACTIVE

    body, headers = _signed(_cancellation_event())
    assert api.post(WEBHOOK_URL, content=body, headers=headers).status_code == 200

    assert _user().subscription_status == subscription.INACTIVE
    assert api.post("/api/v1/clients", json={"name": "Α"}).status_code == 402


def test_a_cancellation_for_an_unknown_customer_touches_nobody(api):
    """Deactivation matches on the Stripe customer id ONLY. Guessing wrong here
    locks a paying customer out of their own books."""
    _pay(api)
    body, headers = _signed(_cancellation_event(customer="cus_someone_else"))
    assert api.post(WEBHOOK_URL, content=body, headers=headers).status_code == 200
    assert _user().subscription_status == subscription.ACTIVE


# --------------------------------------------------------------------------
# Stripe Checkout
# --------------------------------------------------------------------------
def test_checkout_is_503_when_stripe_is_not_configured(api):
    """The suite runs with no keys, which is also how a fresh deployment starts.
    503 (and a billing page that says so) beats a button that only ever errors."""
    assert billing.is_configured() is False
    assert api.post("/api/v1/billing/checkout").status_code == 503


@pytest.fixture()
def stripe_configured(monkeypatch):
    """Pretend keys, plus a stand-in for Stripe's network call that records the
    parameters it was given."""
    monkeypatch.setattr(billing, "STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setattr(billing, "STRIPE_PRICE_ID", "price_fake")
    sent = {}

    def fake_create(**params):
        sent.update(params)
        return {"id": "cs_test_new", "url": "https://checkout.stripe.com/c/pay/cs_test_new",
                "customer": "cus_from_checkout"}

    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(fake_create))
    return sent


def test_checkout_returns_a_stripe_url(api, stripe_configured):
    res = api.post("/api/v1/billing/checkout")
    assert res.status_code == 200, res.text
    assert res.json()["url"].startswith("https://checkout.stripe.com/")


def test_checkout_stamps_the_tenant_on_the_session(api, stripe_configured):
    """client_reference_id is the only EXACT key the webhook can match the payer
    on. Losing it would leave every payment to the email fallback."""
    api.post("/api/v1/billing/checkout")
    assert stripe_configured["client_reference_id"] == "tester"
    assert stripe_configured["mode"] == "subscription"
    assert stripe_configured["line_items"] == [{"price": "price_fake", "quantity": 1}]
    assert stripe_configured["customer_email"] == "tester@example.com"
    # Mutually exclusive in the Stripe API — sending both is a 400 from Stripe.
    assert "customer" not in stripe_configured
    assert "/billing?checkout=success" in stripe_configured["success_url"]
    assert "/billing?checkout=cancelled" in stripe_configured["cancel_url"]


def test_checkout_remembers_the_stripe_customer_for_the_next_attempt(api,
                                                                    stripe_configured):
    """An abandoned checkout must not leave a fresh Stripe customer behind on
    every retry."""
    api.post("/api/v1/billing/checkout")
    assert _user().stripe_customer_id == "cus_from_checkout"

    stripe_configured.clear()
    api.post("/api/v1/billing/checkout")
    assert stripe_configured["customer"] == "cus_from_checkout"
    assert "customer_email" not in stripe_configured


def test_checkout_is_refused_for_an_already_paid_account(api, stripe_configured):
    _pay(api)
    res = api.post("/api/v1/billing/checkout")
    assert res.status_code == 409


def test_an_expired_tenant_can_still_open_a_checkout(api, stripe_configured):
    """The one thing a lapsed account must always be able to do."""
    _expire_trial()
    assert api.post("/api/v1/billing/checkout").status_code == 200


# --------------------------------------------------------------------------
# Stripe customer portal
# --------------------------------------------------------------------------
@pytest.fixture()
def portal_configured(monkeypatch):
    """A secret key and a stand-in for billing_portal.Session.create.

    NOTE it sets no STRIPE_PRICE_ID: the portal must work without one, which is
    the whole reason portal_is_configured() exists separately.
    """
    monkeypatch.setattr(billing, "STRIPE_SECRET_KEY", "sk_test_fake")
    sent = {}

    def fake_create(**params):
        sent.update(params)
        return {"id": "bps_test_1",
                "url": "https://billing.stripe.com/p/session/test_1"}

    monkeypatch.setattr(stripe.billing_portal.Session, "create",
                        staticmethod(fake_create))
    return sent


def test_portal_is_503_when_stripe_is_not_configured(api):
    assert billing.portal_is_configured() is False
    assert api.post("/api/v1/billing/portal").status_code == 503


def test_portal_is_409_without_a_stripe_customer(api, portal_configured):
    """A tenant who has never checked out has nothing for the portal to show.
    409 rather than opening a session for a customer id that does not exist."""
    res = api.post("/api/v1/billing/portal")
    assert res.status_code == 409
    assert portal_configured == {}  # Stripe was never called


def test_portal_returns_a_stripe_url_for_the_signed_in_customer(api,
                                                                portal_configured):
    _pay(api)  # the webhook stores stripe_customer_id
    res = api.post("/api/v1/billing/portal")
    assert res.status_code == 200, res.text
    assert res.json()["url"].startswith("https://billing.stripe.com/")
    # The customer comes from the TENANT'S ROW, never from the request — this
    # assertion is what keeps one tenant out of another's invoices.
    assert portal_configured["customer"] == "cus_test_123"
    assert portal_configured["return_url"].endswith("/billing")


def test_portal_needs_no_price_id(api, portal_configured):
    """Checkout is unconfigured here (no STRIPE_PRICE_ID), and the portal still
    opens — a deployment mid-setup can still let subscribers manage a card."""
    _pay(api)
    assert billing.is_configured() is False
    assert api.post("/api/v1/billing/portal").status_code == 200


def test_a_lapsed_subscriber_can_still_open_the_portal(api, portal_configured):
    """The person with a declined card is the one who most needs this."""
    _pay(api)
    _patch_user(subscription_status=subscription.INACTIVE, trial_ends_at=None)
    assert api.post("/api/v1/billing/portal").status_code == 200


def test_a_portal_failure_is_reported_as_502(api, monkeypatch):
    _pay(api)
    monkeypatch.setattr(billing, "STRIPE_SECRET_KEY", "sk_test_fake")

    def boom(**_params):
        raise stripe.InvalidRequestError("No such customer: cus_test_123", "customer")

    monkeypatch.setattr(stripe.billing_portal.Session, "create", staticmethod(boom))
    res = api.post("/api/v1/billing/portal")
    assert res.status_code == 502
    # Stripe's own English message is logged, not shown.
    assert "No such customer" not in res.text


def test_billing_status_reports_whether_the_portal_is_available(api, monkeypatch):
    # No key, no customer.
    assert api.get("/api/v1/billing/status").json()["portal_enabled"] is False

    # A customer but still no key.
    _pay(api)
    assert api.get("/api/v1/billing/status").json()["portal_enabled"] is False

    monkeypatch.setattr(billing, "STRIPE_SECRET_KEY", "sk_test_fake")
    assert api.get("/api/v1/billing/status").json()["portal_enabled"] is True


def test_a_stripe_failure_is_reported_as_502(api, monkeypatch):
    monkeypatch.setattr(billing, "STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setattr(billing, "STRIPE_PRICE_ID", "price_fake")

    def boom(**_params):
        raise stripe.APIConnectionError("network is down")

    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(boom))
    res = api.post("/api/v1/billing/checkout")
    assert res.status_code == 502
    # Stripe's own English message is logged, not shown.
    assert "network is down" not in res.text


# --------------------------------------------------------------------------
# Auth surfaces
# --------------------------------------------------------------------------
def test_login_reports_the_resolved_subscription(api):
    res = api.post("/api/auth/login",
                   json={"username": "tester", "password": "correct-horse-battery"})
    assert res.status_code == 200, res.text
    assert res.json()["subscription"]["status"] == "trialing"


def test_logging_in_resolves_a_trial_that_lapsed_while_nobody_looked(api):
    _expire_trial()
    res = api.post("/api/auth/login",
                   json={"username": "tester", "password": "correct-horse-battery"})
    assert res.json()["subscription"]["status"] == "inactive"
    assert _user().subscription_status == subscription.INACTIVE


def test_billing_endpoints_require_a_session(api):
    bare = api.__class__(api.app)  # no Authorization header
    assert bare.get("/api/v1/billing/status").status_code == 401
    assert bare.post("/api/v1/billing/checkout").status_code == 401
    assert bare.post("/api/v1/billing/portal").status_code == 401


def test_status_endpoint_reports_the_billing_configuration(api):
    body = api.get("/api/status").json()
    assert body["stripe_webhook_configured"] is True
    assert body["stripe_checkout_configured"] is False
    assert body["stripe_portal_configured"] is False
    assert body["trial_days"] == 14
