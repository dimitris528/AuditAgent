"""
FastAPI web backend for the accounting SaaS dashboard.

Thin glue only: Airtable I/O lives in airtable_client.py, financial math in
finance.py, and auth in auth.py. The Next.js dashboard (web/) consumes these
JSON endpoints.

Run (from the repo root):
    uvicorn server.main:app --reload --port 8000

Auth & tenancy: every data endpoint requires a Bearer JWT (POST /api/auth/login
to obtain one, verified against the Airtable Users table). The tenant is taken
STRICTLY from the token's `sub`, never from a query param, so one user can only
ever read/write their own rows.

Data source: PostgreSQL (Supabase), always. DATABASE_URL is required — every
endpoint reports 503 without it and its errors surface (502) rather than being
masked. The in-memory demo dataset and the demo/demo login that reached it were
removed: once the shared credential went, nothing could authenticate without a
database, so the fallback was unreachable code pretending to be a feature.
"""

import datetime as _dt
import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text as _text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import auth
import finance
import passwords
from config import DOCS_ENABLED, STRIPE_WEBHOOK_SECRET
from server import (billing, database, deps, exports, imports, mailer, ocr,
                    store, subscription)
from server.billing import router as billing_router
from server.webhooks import router as webhooks_router

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create any missing tables on boot.

    Deliberately non-fatal: a database that is unreachable at boot must not
    prevent the process from starting, or Render's health check fails and the
    deploy rolls back with no way to reach /api/status and see why.
    """
    if not database.is_configured():
        print("[WARN] DATABASE_URL is not configured — every request will "
              "report 503 until it is set.")
    else:
        try:
            database.init_db()
            print("[INFO] Database schema verified.")
        except Exception as exc:
            print(f"[ERROR] Could not initialise the database schema: {exc}")
    yield


# The interactive docs are OPT-IN (config.DOCS_ENABLED, default off). Passing
# None for a URL is what actually removes the route — FastAPI never registers
# it — rather than registering a handler that 404s, so there is no endpoint left
# to probe and the schema is not merely hidden.
#
# openapi_url has to go too, and it is the one that matters: /docs and /redoc
# are only viewers, and leaving /openapi.json up would keep serving the entire
# machine-readable schema to anyone who asked for it directly.
app = FastAPI(
    title="Accounting SaaS API",
    version="3.0.0",
    lifespan=lifespan,
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
)

# Stripe billing webhook (POST /api/v1/webhooks/stripe). Public by design —
# it authenticates via Stripe's payload signature, not a bearer token.
app.include_router(webhooks_router)
# Subscription status + Stripe Checkout (GET/POST /api/v1/billing/*). Bearer
# auth, like every other data route.
app.include_router(billing_router)

# --------------------------------------------------------------------------
# CORS
# --------------------------------------------------------------------------
# Worth being precise about, because CORS is the first thing blamed for a
# "backend unavailable" and in this architecture it is almost never the cause.
#
# The browser NEVER calls this API. It calls same-origin Next.js route handlers
# (/api/...), which read the httpOnly session cookie and proxy here
# server-to-server with a Bearer token. Server-to-server requests send no Origin
# header and CORS does not apply to them at all — so in production these
# settings are inert. They matter for exactly two things:
#
#   * `next dev` on :3000 talking to uvicorn on :8000 during local development;
#   * anyone pointing a browser tool, or a future direct-from-browser client,
#     at the API.
#
# FRONTEND_ORIGINS is the explicit allowlist. FRONTEND_ORIGIN_REGEX covers
# generated hostnames — Render mints one per service and adds a suffix, so
# hard-coding the deployed URL means the allowlist silently goes stale on the
# next rename. Defaulted to the *.onrender.com pattern.
#
# allow_origins is NOT "*": with allow_credentials=True the CORS spec forbids
# the wildcard, and Starlette would send a header every browser then rejects —
# which looks exactly like a CORS misconfiguration while being caused by one.
_origins = os.getenv(
    "FRONTEND_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
).split(",")
_origin_regex = os.getenv(
    "FRONTEND_ORIGIN_REGEX",
    r"https://.*\.onrender\.com",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_origin_regex=_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Response headers a cross-origin caller may READ. Without this the CSV
    # export downloads with a browser-invented name, because the filename lives
    # in Content-Disposition and cross-origin JS cannot see an unexposed header.
    expose_headers=["Content-Disposition"],
    # Cache the preflight so a browser client is not re-asking on every call.
    max_age=3600,
)

TREND_MONTHS = int(os.getenv("DASHBOARD_TREND_MONTHS", "12"))
# Shortest password we will store, enforced server-side; the UI only mirrors
# it. (The free-trial length lives in server/subscription.py, which owns both
# granting the trial and expiring it — so the two cannot disagree about how
# long 14 days is.)
MIN_PASSWORD_LENGTH = 8
_TXN_TYPES = {"Έσοδο", "Έξοδο", finance.DEBT_TYPE}


# --------------------------------------------------------------------------
# Auth + subscription dependencies
# --------------------------------------------------------------------------
# Defined in server/deps.py so the billing router can share them without
# importing this module (which imports it). Re-bound here under the names the
# rest of the file has always used.
get_current_user = deps.get_current_user
_require_db = deps.require_db
_resolve_user = deps.resolve_user


# --------------------------------------------------------------------------
# Request models (tenant is NEVER in the body — it comes from the token)
# --------------------------------------------------------------------------
class LoginRequest(BaseModel):
    # Accepts a username OR an email — signup collects an email, so people
    # reasonably try to log in with it.
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=120)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=200)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=16, max_length=512)
    password: str = Field(..., min_length=8, max_length=200)


class TransactionCreate(BaseModel):
    client: str = Field(..., min_length=1, description="Client (Category) name")
    # Preferred over `client` when present: the UI picker sends the id of the
    # selected client so a rename or a near-duplicate name cannot mis-file the
    # row. `client` stays required as the fallback for name-only callers.
    client_id: int | None = None
    amount: float = Field(..., gt=0, description="Amount > 0, read per amount_basis")
    # Which side of the VAT line `amount` is on. An invoice states its net
    # value and a till receipt only its total, so the form lets either be
    # typed; converting HERE keeps both entry modes rounding identically
    # instead of putting a second rounding rule in the browser.
    amount_basis: Literal["gross", "net"] = "gross"
    type: str = Field(..., description="Έσοδο | Έξοδο | Χρεωστούμενο")
    # Τύπος παραστατικού (finance.DOC_TYPES). Optional: rows predating it, and
    # quick cash entries, legitimately have none.
    doc_type: str | None = Field(default=None, max_length=64)
    vat_rate: float = Field(default=finance.DEFAULT_VAT_RATE, ge=0, le=1)
    # The ΦΠΑ actually printed on the document, when there is one. Stored
    # verbatim in preference to the derived figure: an invoice with several
    # lines at different rates, or with its own rounding, must reconcile to the
    # cents on the paper. Omitted → derived from `amount` and `vat_rate`.
    vat_amount: float | None = Field(default=None, ge=0)
    date: str | None = Field(default=None, description="ISO date; defaults today")
    # Χρεωστούμενα: when payment falls due. Omitted → the alerts infer it from
    # the issue date plus the standard terms.
    due_date: str | None = None
    description: str | None = None
    # Invoice identity — what the duplicate guard keys on.
    doc_number: str | None = Field(default=None, max_length=64)
    counterparty_afm: str | None = Field(default=None, max_length=32)
    # Set by the UI after the user has been shown the duplicate and chosen to
    # save anyway. Never defaulted true: the whole point is that the second
    # entry of an invoice is a decision, not an accident.
    force: bool = False


class DuplicateCheck(BaseModel):
    """Ask about an invoice before saving it, so the form can warn while it is
    still being filled in rather than on submit."""
    doc_number: str | None = Field(default=None, max_length=64)
    counterparty_afm: str | None = Field(default=None, max_length=32)
    client: str | None = Field(default=None, max_length=200)
    date: str | None = None


class DebtResolve(BaseModel):
    amount: float = Field(..., gt=0)
    vat_rate: float = Field(default=finance.DEFAULT_VAT_RATE, ge=0, le=1)
    date: str | None = None


class DebtSettle(BaseModel):
    """Εξόφληση χρέους. `amount` omitted = pay the whole remaining balance,
    which is what the one-click "full settlement" button sends."""
    amount: float | None = Field(default=None, gt=0)
    vat_rate: float | None = Field(default=None, ge=0, le=1)
    date: str | None = None
    note: str | None = Field(default=None, max_length=500)


class ClientCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    afm: str | None = Field(default=None, max_length=32)
    contact: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class BulkIds(BaseModel):
    """The selection a bulk action applies to.

    `int` rather than `str` even though transaction ids are serialised as
    strings: pydantic coerces a numeric string, so both `[1, 2]` and
    `["1", "2"]` are accepted, while "abc" is rejected at the edge instead of
    becoming a silently-skipped row.

    Capped so one request cannot ask for unbounded work. The UI selects from a
    page of rows, so the ceiling is far above anything reachable by clicking.
    """
    ids: list[int] = Field(..., min_length=1, max_length=500)


class BulkArchive(BulkIds):
    # False restores. The endpoint is named for the common direction, but
    # un-archiving is the same operation and splitting it into a second route
    # would duplicate the whole body to flip one column.
    archived: bool = True


class ClientDuplicateCheck(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    afm: str | None = Field(default=None, max_length=32)
    exclude_id: int | None = None


class ClientUpdate(BaseModel):
    """Every field optional — this is a PATCH-style update sent as PUT, so the
    drawer can save one field without blanking the rest. Unset (None) means
    "leave alone"; the store only touches keys present in the payload."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    afm: str | None = Field(default=None, max_length=32)
    contact: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    archived: bool | None = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _load(username):
    """Return (active, completed, transactions, subscription) for one tenant.

    A database is required; its errors surface (502) rather than being masked.

    The subscription rides along because _resolve_user has already resolved it:
    returning it here is what lets the dashboard tell the UI to redirect an
    expired tenant to /billing without a second round trip."""
    _require_db()
    try:
        with database.session_scope() as session:
            user, state = deps.resolve_user_state(session, username)
            return (store.get_active_projects(session, user),
                    store.get_completed_projects(session, user),
                    store.get_transactions(session, user),
                    state)
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


def _assert_can_write(username):
    """Refuse a paid-feature request from a lapsed account BEFORE it costs
    anything.

    Only the scan endpoint needs this: every other write resolves the tenant
    inside its own session and gates there, but OCR spends an Anthropic call
    before it ever touches the database, so the check has to come first.
    """
    if not database.is_configured():
        return
    try:
        with database.session_scope() as session:
            _resolve_user(session, username, write=True)
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


def _serialize_txn(rec, paid_by_debt=None):
    f = rec.get("fields", {})
    d = finance.txn_date(rec)
    amount = finance.amount(rec)
    row = {
        "id": rec.get("id"),
        "client": f.get("Category"),
        "amount": amount,
        "type": f.get("Type"),
        "is_revenue": finance.is_revenue(rec),
        "is_debt": finance.is_debt(rec),
        "vat_amount": f.get("VAT_Amount"),
        "vat_rate": f.get("VAT_Rate"),
        "date": d.isoformat() if d else None,
        "description": f.get("Description"),
        "source": f.get("Source"),
        "doc_number": f.get("DocNumber"),
        "counterparty_afm": f.get("CounterpartyAFM"),
        "doc_type": f.get("DocType"),
        "debt_id": f.get("DebtId"),
        # Net of VAT, so the drawer shows both sides of the line without each
        # caller re-deriving it (and rounding it differently). None when the
        # row carries no VAT.
        "net_amount": finance.txn_net(rec),
    }
    if row["is_debt"]:
        due = finance.debt_due_date(rec)
        row["due_date"] = due.isoformat() if due else None
        row["status"] = finance.debt_status(rec)
        row["days_overdue"] = finance.days_overdue(rec)
        # A partly-settled debt row holds what is STILL owed — settlement
        # shrinks it — so the original is reconstructed from the payment log
        # rather than stored a second time and left to drift.
        try:
            paid = (paid_by_debt or {}).get(int(rec.get("id")), 0.0)
        except (TypeError, ValueError):
            paid = 0.0
        row["paid"] = round(paid, 2)
        row["remaining"] = round(amount, 2)
        row["original"] = round(amount + paid, 2)
    return row


def _duplicate_payload(txn):
    """The existing row a duplicate check matched, in the shape the warning
    banner renders."""
    if txn is None:
        return None
    return {
        "id": str(txn.id),
        "client": txn.client,
        "amount": round(abs(float(txn.amount or 0)), 2),
        "type": txn.type,
        "date": txn.date.isoformat() if txn.date else None,
        "doc_number": txn.doc_number,
        "counterparty_afm": txn.counterparty_afm,
        "description": txn.description,
    }


# --------------------------------------------------------------------------
# Public endpoints (no auth)
# --------------------------------------------------------------------------
@app.get("/")
def root():
    # "docs" is omitted rather than reported as null when the docs are off:
    # advertising a path that 404s is worse than saying nothing, and a null
    # would still tell a prober the feature exists and is merely disabled.
    body = {"service": "Accounting SaaS API", "status": "ok"}
    if DOCS_ENABLED:
        body["docs"] = "/docs"
    return body


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/status")
def status():
    """Setup diagnostics: is the database configured, does the connection work,
    and which auth mode is active. No secrets are echoed — in particular the
    error string is truncated, since a libpq failure can quote the DSN."""
    configured = database.is_configured()
    result = {
        "database_configured": configured,
        # "postgres" or "disabled" — there is no third mode. The demo path
        # that used to be reported here is gone.
        "auth_mode": "postgres" if configured else "disabled",
        "jwt_secret_set": not auth.JWT_SECRET_IS_DEFAULT,
        "stripe_webhook_configured": bool(STRIPE_WEBHOOK_SECRET),
        # Checkout needs the secret key AND a price; the webhook needs only the
        # signing secret. Reported separately because half-configured billing
        # (a webhook with no checkout, or the reverse) is the usual mistake and
        # each half fails in a completely different place.
        "stripe_checkout_configured": billing.is_configured(),
        # The customer portal needs only the secret key, so it can be live while
        # checkout is not (a missing STRIPE_PRICE_ID) — worth reporting on its
        # own rather than inferring from the line above.
        "stripe_portal_configured": billing.portal_is_configured(),
        "trial_days": subscription.TRIAL_DAYS,
    }
    if configured:
        try:
            with database.session_scope() as session:
                session.exec(_text("select 1"))
            result["database_connection"] = "ok"
        except Exception as exc:
            result["database_connection"] = "error"
            result["database_error"] = type(exc).__name__
    return result


@app.get("/api/meta")
def meta():
    """Static parameters the UI needs: ΦΠΑ rate options + income-tax scale."""
    return {
        "vat_rates": [
            {"value": r, "label": finance.VAT_RATE_LABELS.get(r, f"{r:.0%}")}
            for r in finance.VAT_RATES
        ],
        "default_vat_rate": finance.DEFAULT_VAT_RATE,
        "tax": {
            "bracket_limit": finance.TAX_BRACKET_LIMIT,
            "low_rate": finance.TAX_RATE_LOW,
            "high_rate": finance.TAX_RATE_HIGH,
        },
        "txn_types": sorted(_TXN_TYPES),
        "doc_types": [
            {"value": d, "label": d, **finance.DOC_TYPE_META[d]}
            for d in finance.DOC_TYPES
        ],
        "payment_terms": finance.DEFAULT_PAYMENT_TERMS,
        # Lets the UI hide the scan button on a deployment with no key rather
        # than offer a control that can only fail.
        "scan_enabled": ocr.is_configured(),
        "scan_accepts": list(ocr.SUPPORTED_TYPES),
        "scan_max_bytes": ocr.MAX_BYTES,
    }


# --------------------------------------------------------------------------
# Auth endpoints
# --------------------------------------------------------------------------
@app.post("/api/auth/login")
def login(body: LoginRequest):
    try:
        user = auth.authenticate(body.username, body.password)
    except auth.AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    if not user:
        raise HTTPException(status_code=401, detail="Λάθος όνομα χρήστη ή κωδικός.")
    token = auth.create_access_token(user["username"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user["username"],
        "subscription": user.get("subscription"),
        "expires_hours": auth.JWT_EXPIRE_HOURS,
    }


@app.get("/api/auth/me")
def me(user: str = Depends(get_current_user)):
    return {"username": user}


@app.post("/api/v1/auth/register", status_code=201)
def register(body: RegisterRequest):
    """Self-service signup, writing straight to PostgreSQL.

    Every new account starts on a free 14-day trial: `subscription_status`
    "trialing" and `trial_ends_at` = now + TRIAL_DAYS. No card, no extra step —
    the trial is granted by the act of registering.

    `trial_ends_at` is what the paywall reads (server/subscription.py): the
    first request after it passes flips the account to "inactive", and a paid
    Stripe checkout clears the date entirely (see server/webhooks.py).

    Availability is checked first for a friendly 409, but the UNIQUE
    constraints on username/email are the real guard — two simultaneous
    signups for the same name both pass the check, and the loser gets an
    IntegrityError rather than a duplicate account.
    """
    _require_db()

    username = body.username.strip()
    email = body.email.strip().lower()
    if len(body.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Ο κωδικός πρέπει να έχει τουλάχιστον {MIN_PASSWORD_LENGTH} χαρακτήρες.")

    try:
        with database.session_scope() as session:
            if store.get_user_by_username(session, username):
                raise HTTPException(
                    status_code=409, detail="Το όνομα χρήστη χρησιμοποιείται ήδη.")
            if store.get_user_by_email(session, email):
                raise HTTPException(
                    status_code=409, detail="Το email χρησιμοποιείται ήδη.")
            user = store.create_user(
                session,
                username=username,
                email=email,
                # Hashed here — a plaintext password never reaches the database.
                password_hash=passwords.hash_password(body.password),
                subscription_status=subscription.TRIALING,
                trial_ends_at=subscription.trial_end(),
            )
            token = auth.create_access_token(user.username)
            state = subscription.resolve(user.subscription_status,
                                         user.trial_ends_at)
            return {
                "ok": True,
                "access_token": token,
                "token_type": "bearer",
                "username": user.username,
                "email": user.email,
                "subscription": state.to_dict(),
                "expires_hours": auth.JWT_EXPIRE_HOURS,
            }
    except HTTPException:
        raise
    except IntegrityError:
        # Lost the race against a simultaneous signup.
        raise HTTPException(
            status_code=409, detail="Το όνομα χρήστη ή το email χρησιμοποιείται ήδη.")
    except auth.AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


@app.post("/api/v1/auth/forgot-password")
def forgot_password(body: ForgotPasswordRequest):
    """Start a password reset. ALWAYS answers the same way.

    The identical 200 for a known and an unknown address is the whole security
    property of this endpoint: anything that differed — a 404, a slower reply, a
    friendlier message — would turn it into a free tool for discovering which
    email addresses hold accounts here. So the response never says whether a
    mail was sent, and the caller cannot tell.

    Delivery: the link is handed to server/mailer.py, which LOGS it when no
    mail transport is configured. That is a deliberate, visible stub rather than
    a silent no-op — see that module. The token itself is only ever returned in
    the response when RESET_TOKEN_IN_RESPONSE is on, which is a local
    development switch and refuses to work in production.
    """
    _require_db()
    neutral = {
        "ok": True,
        "message": "Αν υπάρχει λογαριασμός με αυτό το email, στάλθηκε σύνδεσμος "
                   "επαναφοράς.",
    }
    try:
        with database.session_scope() as session:
            user = store.get_user_by_email(session, body.email)
            if user is None:
                # Same shape, same work, same answer.
                return neutral
            raw = store.create_reset_token(session, user)
            store.purge_expired_reset_tokens(session)
            link = mailer.reset_link(raw)
            mailer.send_password_reset(user.email, link)
            if mailer.EXPOSE_RESET_TOKEN:
                # Development only — mailer refuses to set this in production.
                return {**neutral, "reset_token": raw, "reset_url": link}
            return neutral
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


@app.post("/api/v1/auth/reset-password")
def reset_password(body: ResetPasswordRequest):
    """Spend a reset token and set a new password.

    400 covers unknown, expired and already-used tokens without distinguishing
    them: telling someone their token "has already been used" confirms it was
    real, which is exactly the hint not to give.

    The token is consumed before the password is written (see
    store.consume_reset_token) so a failure cannot leave a spent link working.
    No session is issued — the user logs in with the password they just chose,
    which proves it is the one they think they set.
    """
    _require_db()
    if len(body.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Ο κωδικός πρέπει να έχει τουλάχιστον {MIN_PASSWORD_LENGTH} χαρακτήρες.")
    try:
        with database.session_scope() as session:
            user = store.consume_reset_token(session, body.token)
            if user is None:
                raise HTTPException(
                    status_code=400,
                    detail="Ο σύνδεσμος επαναφοράς δεν είναι έγκυρος ή έχει λήξει. "
                           "Ζητήστε νέο.")
            store.update_password(session, user,
                                  passwords.hash_password(body.password))
            print(f"[INFO] Password reset completed for username={user.username!r}.")
            return {"ok": True, "username": user.username}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


# --------------------------------------------------------------------------
# Read endpoints (tenant-scoped by the JWT)
# --------------------------------------------------------------------------
@app.get("/api/dashboard")
def dashboard(user: str = Depends(get_current_user),
              year: int | None = None,
              quarter: int | None = None,
              month: int | None = None):
    """The full dashboard payload for the authenticated tenant: executive
    header, rich client cards, and the analytics datasets — all with the exact
    VAT/net-profit summation.

    Optional year/quarter/month narrow EVERY figure to that period. Only the
    transactions are filtered: the client list is not period-scoped, so a client
    with no activity in the window still gets a card (showing zeros) rather than
    vanishing from the grid.

    The one deliberate exception is `debt_alerts`, which is computed over the
    whole book — see finance.build_debt_alerts. Its totals will therefore
    exceed the header's period-scoped debt KPI whenever a period is selected,
    and the UI labels it as all-time so the two are not read as contradicting
    each other.
    """
    active, completed, transactions, state = _load(user)
    start, end = finance.period_bounds(year, quarter, month)
    scoped = finance.filter_period(transactions, start, end)
    # Settlement progress for the debt rows in the table below. One extra query
    # for the whole page; _serialize_txn needs it to show "paid X of Y".
    paid_by_debt = {}
    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user)
            paid_by_debt = store.paid_by_debt(
                store.get_debt_payments(session, tenant))
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")
    payload = finance.build_dashboard(active, completed, scoped,
                                      trend_months=TREND_MONTHS,
                                      # Alerts run over the UNFILTERED book:
                                      # an overdue debt must not disappear
                                      # because a past period is selected.
                                      all_transactions=transactions)
    payload["username"] = user
    payload["scan_enabled"] = ocr.is_configured()
    # The period's rows, newest first, for the dashboard's transactions table.
    # Carried on this payload rather than fetched separately: the table is on
    # screen from the first paint, and a second round trip would make the
    # busiest part of the page the last to arrive.
    payload["transactions"] = [
        _serialize_txn(t, paid_by_debt)
        for t in sorted(scoped, key=lambda r: finance.txn_date(r) or _dt.date.min,
                        reverse=True)
    ]
    # Carried on the dashboard payload rather than fetched separately: the page
    # has to know whether to redirect a lapsed tenant to /billing BEFORE it
    # renders, and a second request to find that out would show the dashboard
    # for a frame first.
    payload["subscription"] = state.to_dict()
    payload["period"] = {
        "year": year,
        "quarter": quarter,
        "month": month,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        # Years present across ALL history, so the selector can still offer a
        # year the current filter excludes.
        "available_years": finance.available_years(transactions),
        "transactions_in_period": len(scoped),
        "transactions_total": len(transactions),
    }
    return payload


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------
def _client_summary(name, records):
    """Balance + VAT summary for one client's transactions.

    Delegates to finance.client_metrics — the exact function behind the
    dashboard card — rather than recomputing, so the drawer and the card cannot
    drift apart. (Note finance.sum_by_type/vat_by_type return (expense, revenue)
    TUPLES in that order; recomputing here is an easy way to silently swap
    them.)
    """
    regular, debts = finance.split_debts(records)
    grouped = finance.transactions_by_category(regular)
    debt_total = round(sum(finance.amount(d) for d in debts), 2)
    metrics = finance.client_metrics(name, grouped, debt_total)
    net_vat = metrics["net_vat"]
    return {
        **metrics,
        "vat_status": ("refund" if net_vat < 0
                       else "payable" if net_vat > 0 else "zero"),
        "count": len(records),
        "open_debts": len(debts),
    }


@app.get("/api/v1/clients")
def list_clients(user: str = Depends(get_current_user),
                 include_archived: bool = True):
    """All of the tenant's clients — the picker uses this for its dropdown."""
    _require_db()
    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user)
            rows = store.list_clients(session, tenant,
                                      include_archived=include_archived)
            return {"clients": [c.to_detail() for c in rows]}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


_DUPLICATE_MESSAGES = {
    "afm": "Υπάρχει ήδη πελάτης με αυτό το Α.Φ.Μ.: «{name}».",
    "name": "Υπάρχει ήδη πελάτης με αυτό το όνομα: «{name}».",
}


@app.post("/api/v1/clients", status_code=201)
def create_client(body: ClientCreate, user: str = Depends(get_current_user)):
    """Create a client, e.g. inline from the transaction form.

    Refuses — it does not merely warn — when the client already exists. A
    duplicate client card silently splits one company's revenue, VAT and tax
    bar across two cards, and nothing on the dashboard makes that visible
    afterwards. The ΑΦΜ is the strict check; the name check is Greek
    case- and accent-insensitive, so "ΝΗΣΙΔΑ CAFE" collides with the existing
    "Νησίδα Café" rather than opening a second card for it.
    """
    _require_db()
    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user, write=True)
            clash, reason = store.detect_client_duplicate(
                session, tenant, name=body.name, afm=body.afm)
            if clash is not None:
                raise HTTPException(
                    status_code=409,
                    detail=_DUPLICATE_MESSAGES[reason].format(name=clash.name))
            client = store.create_client(
                session, tenant, body.name,
                afm=body.afm, contact=body.contact, notes=body.notes)
            return client.to_detail()
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


@app.post("/api/v1/clients/check-duplicate")
def check_client_duplicate(body: ClientDuplicateCheck,
                           user: str = Depends(get_current_user)):
    """Non-destructive lookup so the picker can warn WHILE a name is being
    typed, instead of only rejecting the save."""
    _require_db()
    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user)
            clash, reason = store.detect_client_duplicate(
                session, tenant, name=body.name, afm=body.afm,
                exclude_id=body.exclude_id)
            if clash is None:
                return {"duplicate": None}
            return {"duplicate": {**clash.to_detail(), "matched_by": reason}}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


@app.get("/api/v1/clients/{client_id}")
def get_client(client_id: int, user: str = Depends(get_current_user),
               year: int | None = None, quarter: int | None = None,
               month: int | None = None):
    """One client plus its own transactions and summary — the drawer payload.

    Accepts the same period parameters as the dashboard so the drawer can honour
    whatever period the page is showing.
    """
    _require_db()
    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user)
            client = store.get_client(session, tenant, client_id)
            if client is None:
                raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε.")
            records = store.get_client_transactions(session, tenant, client)
            start, end = finance.period_bounds(year, quarter, month)
            scoped = finance.filter_period(records, start, end)
            scoped = sorted(scoped,
                            key=lambda t: finance.txn_date(t) or _dt.date.min,
                            reverse=True)
            # The settlement log is NOT period-scoped: a debt raised last
            # quarter and paid this one must still show what has been paid
            # against it, and hiding earlier payments would make the remaining
            # balance look unexplained.
            payments = store.get_debt_payments(session, tenant, client=client)
            paid = store.paid_by_debt(payments)
            return {
                "client": client.to_detail(),
                # The tenant's OWN identity, for the statement letterhead. A
                # printed Καρτέλα Πελάτη that does not say who issued it is not
                # a document anyone can act on — it went out with a hard-coded
                # product name and nothing else.
                "issuer": {
                    "name": tenant.username,
                    "email": tenant.email,
                },
                "summary": _client_summary(client.name, scoped),
                "transactions": [_serialize_txn(t, paid) for t in scoped],
                "payments": [p.to_detail() for p in payments],
            }
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


@app.put("/api/v1/clients/{client_id}")
def update_client(client_id: int, body: ClientUpdate,
                  user: str = Depends(get_current_user)):
    """Update a client's details, or archive/restore it.

    Only the keys actually sent are applied — `exclude_unset` is what makes this
    a partial update, so saving the notes field cannot blank the ΑΦΜ.
    """
    _require_db()
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=422, detail="Κανένα πεδίο προς ενημέρωση.")
    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user, write=True)
            # Both the rename and the ΑΦΜ are checked, and against every OTHER
            # client only — editing a client without changing either must not
            # collide with itself.
            clash, reason = store.detect_client_duplicate(
                session, tenant,
                name=fields.get("name"), afm=fields.get("afm"),
                exclude_id=client_id)
            if clash is not None:
                raise HTTPException(
                    status_code=409,
                    detail=_DUPLICATE_MESSAGES[reason].format(name=clash.name))
            client = store.update_client(session, tenant, client_id, **fields)
            if client is None:
                raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε.")
            return client.to_detail()
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


@app.get("/api/v1/exports/transactions.csv")
def export_transactions(user: str = Depends(get_current_user),
                        year: int | None = None,
                        quarter: int | None = None,
                        month: int | None = None,
                        client_id: int | None = None,
                        dialect: Literal["excel", "iso"] = "excel"):
    """The period's transactions as a spreadsheet.

    Same period parameters as the dashboard, so the download matches what was
    on screen when the button was pressed rather than silently exporting the
    whole book. `client_id` narrows it to one client (the drawer's export).

    A READ, and therefore NOT behind the subscription paywall: an account whose
    trial lapsed must still be able to get its own books out. Locking a
    customer's data inside the product is how you turn a billing problem into a
    grievance.

    See server/exports.py for why the default dialect is semicolon-delimited
    UTF-8-with-BOM rather than RFC 4180 — in one word, Excel.
    """
    active, _completed, transactions, _state = _load(user)
    start, end = finance.period_bounds(year, quarter, month)
    scoped = finance.filter_period(transactions, start, end)

    afm_by_client = {}
    paid_by_debt = {}
    client_name = None
    if database.is_configured():
        try:
            with database.session_scope() as session:
                tenant = _resolve_user(session, user)
                if client_id is not None:
                    target = store.get_client(session, tenant, client_id)
                    if target is None:
                        raise HTTPException(status_code=404,
                                            detail="Ο πελάτης δεν βρέθηκε.")
                    client_name = target.name
                for row in store.list_clients(session, tenant):
                    if row.afm:
                        afm_by_client[exports.client_key(row.name)] = row.afm
                paid_by_debt = store.paid_by_debt(
                    store.get_debt_payments(session, tenant))
        except HTTPException:
            raise
        except SQLAlchemyError as exc:
            raise HTTPException(status_code=502,
                                detail=f"Σφάλμα βάσης δεδομένων: {exc}")

    if client_name is not None:
        key = exports.client_key(client_name)
        scoped = [t for t in scoped
                  if exports.client_key(t["fields"].get("Category")) == key]

    body = exports.transactions_csv(scoped, afm_by_client=afm_by_client,
                                    paid_by_debt=paid_by_debt, dialect=dialect)
    name = exports.filename(year=year, quarter=quarter, month=month,
                            client=client_id)
    return Response(
        # Encoded here rather than left to Starlette: the BOM is part of the
        # BYTES, and the charset has to be declared or Excel guesses again.
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# --------------------------------------------------------------------------
# Bulk selection actions (delete / archive)
# --------------------------------------------------------------------------
# What the checkbox selection in the UI posts to. Every one of these reports
# what it could NOT do alongside what it did: a bulk action that silently drops
# half its input is worse than one that refuses, because the user is left
# unable to tell which half landed.
def _plural(count, singular, plural):
    return f"{count} {singular if count == 1 else plural}"


@app.post("/api/transactions/bulk-delete")
def bulk_delete_transactions(body: BulkIds,
                             user: str = Depends(get_current_user)):
    """Delete the selected transactions.

    Irreversible, and deliberately not softened with a "trash": these are book
    entries, and a half-deleted one that still counts toward a total is worse
    than none. The confirmation lives in the UI, where the count can be shown.
    """
    _require_db()
    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user, write=True)
            deleted, missing = store.delete_transactions(session, tenant,
                                                         body.ids)
            return {
                "ok": True,
                "requested": len(body.ids),
                "deleted": len(deleted),
                "skipped": len(missing),
                "deleted_ids": [str(i) for i in deleted],
                "message": (f"Διαγράφηκαν {_plural(len(deleted), 'κίνηση', 'κινήσεις')}."
                            if deleted else "Δεν διαγράφηκε καμία κίνηση."),
            }
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


@app.post("/api/clients/bulk-delete")
def bulk_delete_clients(body: BulkIds, user: str = Depends(get_current_user)):
    """Delete the selected clients — only those with no transactions.

    A client that still has rows is refused and reported in `blocked`, with its
    name and row count. See store.delete_clients for why that is the behaviour
    rather than a cascade: taking booked history out with a checkbox click, or
    orphaning rows the totals still count, are both worse than saying no. The
    answer for those clients is Αρχειοθέτηση, which is what `blocked` tells the
    UI to offer.
    """
    _require_db()
    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user, write=True)
            deleted, blocked, missing = store.delete_clients(session, tenant,
                                                             body.ids)
            return {
                "ok": True,
                "requested": len(body.ids),
                "deleted": len(deleted),
                "skipped": len(blocked) + len(missing),
                "deleted_ids": [row["id"] for row in deleted],
                "blocked": [
                    {**row,
                     "message": f"Ο πελάτης «{row['name']}» έχει "
                                f"{_plural(row['transactions'], 'κίνηση', 'κινήσεις')} "
                                "και δεν μπορεί να διαγραφεί. Αρχειοθετήστε τον."}
                    for row in blocked
                ],
                "message": (f"Διαγράφηκαν {_plural(len(deleted), 'πελάτης', 'πελάτες')}."
                            if deleted else "Δεν διαγράφηκε κανένας πελάτης."),
            }
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


@app.post("/api/clients/bulk-archive")
def bulk_archive_clients(body: BulkArchive,
                         user: str = Depends(get_current_user)):
    """Archive (or, with archived=false, restore) the selected clients.

    The reversible counterpart to delete, and the answer for every client
    bulk-delete refuses. An archived client keeps all its history, drops out of
    the active lists and out of every total that sums them, and reappears under
    the grid's Αρχειοθετημένοι filter.
    """
    _require_db()
    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user, write=True)
            changed, unchanged, missing = store.set_clients_archived(
                session, tenant, body.ids, archived=body.archived)
            verb = "Αρχειοθετήθηκαν" if body.archived else "Επαναφέρθηκαν"
            none = ("Δεν αρχειοθετήθηκε κανένας πελάτης." if body.archived
                    else "Δεν επαναφέρθηκε κανένας πελάτης.")
            return {
                "ok": True,
                "archived": body.archived,
                "requested": len(body.ids),
                "changed": len(changed),
                "skipped": len(unchanged) + len(missing),
                "already": len(unchanged),
                "changed_ids": [row["id"] for row in changed],
                "message": (f"{verb} {_plural(len(changed), 'πελάτης', 'πελάτες')}."
                            if changed else none),
            }
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


# --------------------------------------------------------------------------
# Bulk import (CSV / Excel)
# --------------------------------------------------------------------------
# The mirror image of the export above, and the front door for a new user
# arriving from Excel or a legacy Greek accounting package. Parsing and
# validation live in server/imports.py, the matching and the writes in
# server/store.py; what is left here is the upload, the paywall and the shape
# of the summary the modal renders.
async def _read_upload(file):
    """The uploaded bytes, refusing anything past the size cap.

    Read with an explicit limit rather than `await file.read()`: the unbounded
    form pulls the whole body into memory BEFORE the size can be checked, so a
    500 MB upload is a memory problem no later check can undo. One byte past
    the cap is enough to know it was exceeded.
    """
    data = await file.read(imports.MAX_BYTES + 1)
    if len(data) > imports.MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Το αρχείο ξεπερνά τα {imports.MAX_BYTES // (1024 * 1024)} MB.")
    if not data:
        raise HTTPException(status_code=422, detail="Το αρχείο είναι κενό.")
    return data


# Greek needs the noun AND the verb to agree with the count, and "Εισήχθησαν 1
# πελάτες" is exactly the detail that makes a product feel machine-translated.
_IMPORT_NOUNS = {
    imports.CLIENTS: ("πελάτης", "πελάτες"),
    imports.TRANSACTIONS: ("κίνηση", "κινήσεις"),
}


def _import_message(kind, imported):
    if not imported:
        return "Δεν εισήχθη καμία νέα εγγραφή."
    singular, plural = _IMPORT_NOUNS[kind]
    if imported == 1:
        return f"Εισήχθη 1 {singular} επιτυχώς!"
    return f"Εισήχθησαν {imported} {plural} επιτυχώς!"


def _import_summary(kind, filename, parsed, imported, skipped,
                    clients_created=0):
    """What the upload modal renders.

    The four counts always add up — total == imported + skipped + failed — so
    the user can see at a glance that nothing went missing between the file and
    the book. The per-row lists are CAPPED (imports.MAX_ISSUES) while the counts
    stay exact: a wholly mis-mapped file produces one error per row, and
    returning four thousand of them helps nobody and costs a megabyte.
    """
    return {
        "ok": True,
        "kind": kind,
        "filename": filename,
        "total_rows": parsed.total,
        "imported": imported,
        "skipped": len(skipped),
        "failed": len(parsed.errors),
        # Transactions only: clients that had to be created to hold the rows,
        # which is the one side effect of this import worth announcing.
        "clients_created": clients_created,
        "message": _import_message(kind, imported),
        "errors": parsed.errors[:imports.MAX_ISSUES],
        "warnings": parsed.warnings[:imports.MAX_ISSUES],
        "skipped_rows": skipped[:imports.MAX_ISSUES],
    }


@app.post("/api/import/clients")
async def import_clients(file: UploadFile = File(...),
                         user: str = Depends(get_current_user)):
    """Bulk-create clients from a CSV/XLSX (Επωνυμία, Α.Φ.Μ., Τηλέφωνο, Email).

    Rows are validated independently: a bad ΑΦΜ on line 12 is reported against
    line 12 and the other 400 rows still import. A client the book already has
    is skipped rather than duplicated or refused, so re-uploading the same file
    is a safe no-op — which is what makes "fix the eight rows it complained
    about and try again" a workable instruction.
    """
    _require_db()
    # Before the file is even parsed: an import is a write, and a lapsed tenant
    # should be told so rather than after a 5 MB upload has been processed.
    _assert_can_write(user)
    data = await _read_upload(file)
    try:
        parsed = imports.parse_clients(data)
    except imports.ImportFileError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))

    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user, write=True)
            created, skipped = store.import_clients(session, tenant, parsed.rows)
            return _import_summary(imports.CLIENTS, file.filename, parsed,
                                   len(created), skipped)
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


@app.post("/api/import/transactions")
async def import_transactions(file: UploadFile = File(...),
                              user: str = Depends(get_current_user)):
    """Bulk-create historic transactions from a CSV/XLSX.

    Each row is matched to a client by ΑΦΜ first and by name second; one that
    matches neither has its client created, because the ordinary case is a year
    of history landing in an empty account. Invoices already on file are
    skipped by the same (number, date, issuer) rule the manual duplicate guard
    uses, so an interrupted import can simply be re-run.
    """
    _require_db()
    _assert_can_write(user)
    data = await _read_upload(file)
    try:
        parsed = imports.parse_transactions(data)
    except imports.ImportFileError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))

    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user, write=True)
            created, skipped, new_clients = store.import_transactions(
                session, tenant, parsed.rows)
            return _import_summary(imports.TRANSACTIONS, file.filename, parsed,
                                   len(created), skipped,
                                   clients_created=len(new_clients))
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


@app.get("/api/import/templates/{kind}")
def import_template(kind: str, user: str = Depends(get_current_user)):
    """The blank file to fill in — "Κατεβάστε το πρότυπο αρχείο CSV".

    Served by the backend rather than written into the bundle so the template
    and the parser cannot drift: the columns below are the same tuples
    server/imports.py matches on. Same Excel dialect as the export (BOM,
    semicolons, decimal commas), so a template downloaded, filled in and
    re-uploaded survives a round trip through a Greek Excel unaltered.
    """
    try:
        body, name = imports.template(kind)
    except imports.ImportFileError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/api/transactions")
def transactions(user: str = Depends(get_current_user),
                 client: str | None = None,
                 limit: int = 200):
    """Recent transactions (newest first) for the tenant, optionally filtered
    to one client."""
    limit = max(1, min(limit, 1000))
    _active, _completed, txns, _state = _load(user)
    if client:
        target = client.strip().lower()
        txns = [t for t in txns
                if (t["fields"].get("Category") or "").strip().lower() == target]
    txns = sorted(txns, key=lambda t: finance.txn_date(t) or _dt.date.min,
                  reverse=True)[:limit]
    return {"username": user,
            "transactions": [_serialize_txn(t) for t in txns]}


# --------------------------------------------------------------------------
# Write endpoints (tenant from the JWT, never from the body)
# --------------------------------------------------------------------------
@app.post("/api/transactions", status_code=201)
def create_transaction(body: TransactionCreate,
                       user: str = Depends(get_current_user)):
    if body.type not in _TXN_TYPES:
        raise HTTPException(status_code=422,
                            detail=f"type must be one of {sorted(_TXN_TYPES)}")
    if body.doc_type and body.doc_type not in finance.DOC_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"doc_type must be one of {list(finance.DOC_TYPES)}")
    _require_db()
    if finance.is_credit_note(body.doc_type) and body.type == finance.DEBT_TYPE:
        raise HTTPException(
            status_code=422,
            detail="Το πιστωτικό δεν μπορεί να καταχωρηθεί ως χρεωστούμενο.")

    # The net→gross conversion, the sign convention, the credit-note reversal
    # and the VAT orientation all live in finance.book_amounts, which the bulk
    # importer shares — see its docstring for why they must not be duplicated.
    signed, vat_amount = finance.book_amounts(
        body.amount, body.type, doc_type=body.doc_type,
        vat_rate=body.vat_rate, vat_amount=body.vat_amount,
        basis=body.amount_basis)
    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user, write=True)
            # Same invoice, same issuer, same date = the same document being
            # entered twice. WARN rather than refuse: a genuine reissue under
            # the same number exists, so the caller can repeat the request with
            # force=true once the user has seen what it collided with.
            if not body.force:
                clash = store.find_duplicate_transaction(
                    session, tenant, body.doc_number,
                    counterparty_afm=body.counterparty_afm,
                    client_name=body.client,
                    txn_date=body.date)
                if clash is not None:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": "Το παραστατικό υπάρχει ήδη καταχωρημένο.",
                            "duplicate": _duplicate_payload(clash),
                        })
            txn = store.create_transaction(
                session, tenant, body.client.strip(), signed,
                description=(body.description or None),
                txn_date=body.date,
                type_=body.type,
                source="Web",
                vat_amount=vat_amount,
                vat_rate=body.vat_rate,
                client_id=body.client_id,
                doc_number=body.doc_number,
                counterparty_afm=body.counterparty_afm,
                doc_type=body.doc_type,
                due_date=body.due_date,
            )
            return {"ok": True, "id": str(txn.id),
                    "amount": signed, "vat_amount": vat_amount}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


@app.post("/api/v1/transactions/check-duplicate")
def check_transaction_duplicate(body: DuplicateCheck,
                                user: str = Depends(get_current_user)):
    """Look for the same invoice without writing anything — the form calls this
    as soon as a scan fills the document number in, so the warning appears
    before the user has retyped the whole thing."""
    _require_db()
    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user)
            clash = store.find_duplicate_transaction(
                session, tenant, body.doc_number,
                counterparty_afm=body.counterparty_afm,
                client_name=body.client,
                txn_date=body.date)
            return {"duplicate": _duplicate_payload(clash)}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


def _settle(record_id, amount, vat_rate, when, note, user):
    """Shared body of the two settlement endpoints."""
    _require_db()
    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user, write=True)
            # store.settle_debt is tenant-scoped, so another tenant's id is
            # indistinguishable from a missing one.
            result = store.settle_debt(session, tenant, record_id,
                                       amount=amount, vat_rate=vat_rate,
                                       paid_date=when, note=note)
            if result is None:
                raise HTTPException(status_code=404, detail="Η κίνηση δεν βρέθηκε.")
            txn, payment = result
            return {
                "ok": True,
                "id": str(txn.id),
                "settled": payment.kind == store.SETTLEMENT_FULL,
                "paid": round(payment.amount, 2),
                "remaining": round(payment.remaining, 2),
                "payment": payment.to_detail(),
            }
    except store.SettlementError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


@app.post("/api/v1/transactions/{record_id}/settle")
def settle_debt(record_id: str, body: DebtSettle,
                user: str = Depends(get_current_user)):
    """Εξόφληση Χρέους — full or partial.

    Omitting `amount` pays off the whole remaining balance and flips the row to
    realised revenue. Sending less books that much as revenue now and leaves
    the rest outstanding, so a €500 debt paid €200 leaves €300. Either way the
    payment is written to the settlement log and every client balance
    recalculates from it.
    """
    return _settle(record_id, body.amount, body.vat_rate, body.date, body.note,
                   user)


@app.post("/api/transactions/{record_id}/resolve")
def resolve_debt(record_id: str, body: DebtResolve,
                 user: str = Depends(get_current_user)):
    """Εξόφληση (legacy path, kept for existing callers).

    Now backed by the same settlement logic, which also fixes the case it got
    wrong: sending less than the balance used to flip the WHOLE debt to revenue
    while stamping VAT computed on the smaller figure. It now settles exactly
    the amount sent.
    """
    return _settle(record_id, body.amount, body.vat_rate, body.date, None, user)


@app.get("/api/v1/transactions/{record_id}/payments")
def debt_payments(record_id: str, user: str = Depends(get_current_user)):
    """The settlement history of one debt, newest first."""
    _require_db()
    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user)
            txn = store.get_transaction(session, tenant, record_id)
            if txn is None:
                raise HTTPException(status_code=404, detail="Η κίνηση δεν βρέθηκε.")
            rows = store.get_debt_payments(session, tenant, debt_id=txn.id)
            return {
                "id": str(txn.id),
                "remaining": round(abs(float(txn.amount or 0)), 2)
                if finance.DEBT_TYPE == (txn.type or "").strip() else 0.0,
                "paid": round(sum(r.amount for r in rows), 2),
                "payments": [r.to_detail() for r in rows],
            }
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")


# --------------------------------------------------------------------------
# Document scanning (OCR)
# --------------------------------------------------------------------------
@app.post("/api/v1/documents/scan")
async def scan_document(file: UploadFile = File(...),
                        user: str = Depends(get_current_user)):
    """Read a PDF / photo of an invoice into the transaction form.

    Writes NOTHING. The extraction comes back for the user to review and save
    through the ordinary create endpoint, so a misread never lands in the books
    unseen. The client match and duplicate check ride along so the form can
    pre-select the client it recognised and warn on an invoice already filed.

    Gated behind the subscription anyway, despite writing nothing: it is the
    front door of the transaction-entry flow, and it bills a real OpenAI call
    per request. Checked BEFORE the extraction, so a lapsed account costs
    nothing rather than being told 402 after the money is spent.
    """
    if not ocr.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Η σάρωση παραστατικών δεν έχει ρυθμιστεί (ορίστε OPENAI_API_KEY).")
    _assert_can_write(user)
    data = await file.read()
    try:
        extracted = ocr.extract(data, file.content_type, filename=file.filename)
    except ocr.OcrError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))

    payload = {"extracted": extracted, "client_match": None, "duplicate": None}
    # The lookups need a database; without one the extraction is still useful
    # on its own, so this is not fatal.
    if not database.is_configured():
        return payload
    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user)
            match, reason = store.detect_client_duplicate(
                session, tenant,
                name=extracted.get("counterparty_name"),
                afm=extracted.get("counterparty_afm"))
            if match is not None:
                payload["client_match"] = {**match.to_detail(),
                                           "matched_by": reason}
            payload["duplicate"] = _duplicate_payload(
                store.find_duplicate_transaction(
                    session, tenant, extracted.get("doc_number"),
                    counterparty_afm=extracted.get("counterparty_afm"),
                    client_name=extracted.get("counterparty_name"),
                    txn_date=extracted.get("doc_date")))
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")
    return payload


@app.delete("/api/transactions/{record_id}")
def delete_transaction(record_id: str, user: str = Depends(get_current_user)):
    _require_db()
    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user, write=True)
            if not store.delete_transaction(session, tenant, record_id):
                raise HTTPException(status_code=404, detail="Η κίνηση δεν βρέθηκε.")
            return {"ok": True, "id": record_id}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")
