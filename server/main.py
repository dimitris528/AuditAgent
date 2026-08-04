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

Data source:
  - DATABASE_URL configured → LIVE PostgreSQL data (Supabase); any database
    error surfaces (502) rather than being masked.
  - DATABASE_URL NOT configured + DASHBOARD_DEMO != "0" → the in-memory demo
    dataset (login demo/demo), so the UI is testable before secrets exist.
"""

import datetime as _dt
import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text as _text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import auth
import finance
import passwords
from config import STRIPE_WEBHOOK_SECRET
from server import database, demo, ocr, store
from server.webhooks import router as webhooks_router

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create any missing tables on boot.

    Deliberately non-fatal: a database that is unreachable at boot must not
    prevent the process from starting, or Render's health check fails and the
    deploy rolls back with no way to reach /api/status and see why.
    """
    if not database.is_configured():
        print("[WARN] DATABASE_URL is not configured — running in demo mode.")
    else:
        try:
            database.init_db()
            print("[INFO] Database schema verified.")
        except Exception as exc:
            print(f"[ERROR] Could not initialise the database schema: {exc}")
    yield


app = FastAPI(title="Accounting SaaS API", version="3.0.0", lifespan=lifespan)

# Stripe billing webhook (POST /api/v1/webhooks/stripe). Public by design —
# it authenticates via Stripe's payload signature, not a bearer token.
app.include_router(webhooks_router)

_origins = os.getenv(
    "FRONTEND_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEMO_ENABLED = os.getenv("DASHBOARD_DEMO", "1") != "0"
TREND_MONTHS = int(os.getenv("DASHBOARD_TREND_MONTHS", "12"))
# Free-trial length for self-service signups, and the minimum password we will
# store. Both are enforced server-side; the UI only mirrors them.
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "15"))
MIN_PASSWORD_LENGTH = 8
_TXN_TYPES = {"Έσοδο", "Έξοδο", finance.DEBT_TYPE}

_bearer = HTTPBearer(auto_error=False)


# --------------------------------------------------------------------------
# Auth dependency — the tenant is the JWT subject, nothing else
# --------------------------------------------------------------------------
def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    if cred is None or not cred.credentials:
        raise HTTPException(status_code=401, detail="Απαιτείται σύνδεση.")
    try:
        payload = auth.decode_token(cred.credentials)
    except auth.AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Μη έγκυρη συνεδρία.")
    return sub


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
def _require_db():
    """Fail with 503 when writes are attempted without a database."""
    if not database.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Η βάση δεδομένων δεν έχει ρυθμιστεί (ορίστε DATABASE_URL).")


def _resolve_user(session, username):
    """Turn the JWT subject into the tenant's row, or 401 if it no longer
    exists — a token outliving its account must not resolve to anything."""
    user = store.get_user_by_username(session, username)
    if user is None:
        raise HTTPException(status_code=401, detail="Ο λογαριασμός δεν βρέθηκε.")
    return user


def _load(username):
    """Return (active, completed, transactions, is_demo) for one tenant.

    The live database is preferred whenever it is configured, and its errors
    surface (502) rather than being masked. The demo dataset is used ONLY when
    DATABASE_URL is absent (bootstrap), so live data can never be silently
    replaced by demo figures."""
    if not database.is_configured():
        if DEMO_ENABLED:
            return (demo.demo_active_projects(), demo.demo_completed_projects(),
                    demo.demo_transactions(), True)
        raise HTTPException(
            status_code=503,
            detail="Η βάση δεδομένων δεν έχει ρυθμιστεί (ορίστε DATABASE_URL).")
    try:
        with database.session_scope() as session:
            user = _resolve_user(session, username)
            return (store.get_active_projects(session, user),
                    store.get_completed_projects(session, user),
                    store.get_transactions(session, user),
                    False)
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
    return {"service": "Accounting SaaS API", "status": "ok", "docs": "/docs"}


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
        "demo_enabled": DEMO_ENABLED,
        "auth_mode": "postgres" if configured else ("demo" if DEMO_ENABLED else "disabled"),
        "jwt_secret_set": not auth.JWT_SECRET_IS_DEFAULT,
        "stripe_webhook_configured": bool(STRIPE_WEBHOOK_SECRET),
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
    token = auth.create_access_token(
        user["username"], extra={"demo": bool(user.get("demo"))})
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user["username"],
        "subscription": user.get("subscription"),
        "demo": bool(user.get("demo")),
        "expires_hours": auth.JWT_EXPIRE_HOURS,
    }


@app.get("/api/auth/me")
def me(user: str = Depends(get_current_user)):
    return {"username": user}


@app.post("/api/v1/auth/register", status_code=201)
def register(body: RegisterRequest):
    """Self-service signup, writing straight to PostgreSQL.

    New accounts start Active on a 15-day trial; trial_expiry is what the
    subscription gate reads to flip them to Inactive, and a paid Stripe
    checkout clears it (see server/webhooks.py).

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
                subscription_status="Active",
                trial_expiry=store.utcnow() + _dt.timedelta(days=TRIAL_DAYS),
            )
            token = auth.create_access_token(user.username, extra={"demo": False})
            return {
                "ok": True,
                "access_token": token,
                "token_type": "bearer",
                "username": user.username,
                "email": user.email,
                "subscription": user.subscription_status,
                "demo": False,
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
    active, completed, transactions, is_demo = _load(user)
    start, end = finance.period_bounds(year, quarter, month)
    scoped = finance.filter_period(transactions, start, end)
    payload = finance.build_dashboard(active, completed, scoped,
                                      trend_months=TREND_MONTHS,
                                      # Alerts run over the UNFILTERED book:
                                      # an overdue debt must not disappear
                                      # because a past period is selected.
                                      all_transactions=transactions)
    payload["username"] = user
    payload["demo"] = is_demo
    payload["scan_enabled"] = ocr.is_configured()
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
            tenant = _resolve_user(session, user)
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
            tenant = _resolve_user(session, user)
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


@app.get("/api/transactions")
def transactions(user: str = Depends(get_current_user),
                 client: str | None = None,
                 limit: int = 200):
    """Recent transactions (newest first) for the tenant, optionally filtered
    to one client."""
    limit = max(1, min(limit, 1000))
    _active, _completed, txns, is_demo = _load(user)
    if client:
        target = client.strip().lower()
        txns = [t for t in txns
                if (t["fields"].get("Category") or "").strip().lower() == target]
    txns = sorted(txns, key=lambda t: finance.txn_date(t) or _dt.date.min,
                  reverse=True)[:limit]
    return {"username": user, "transactions": [_serialize_txn(t) for t in txns],
            "demo": is_demo}


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
    is_debt = body.type == finance.DEBT_TYPE
    is_revenue = body.type == "Έσοδο"
    credit = finance.is_credit_note(body.doc_type)
    if credit and is_debt:
        raise HTTPException(
            status_code=422,
            detail="Το πιστωτικό δεν μπορεί να καταχωρηθεί ως χρεωστούμενο.")

    # Whichever side was typed, the row stores the GROSS figure.
    gross = (finance.gross_from_net(body.amount, body.vat_rate)
             if body.amount_basis == "net" else round(body.amount, 2))

    # Sign convention: revenue/debt are positive, expense negative — the
    # analytics read the sign, not the Type column.
    signed = gross if (is_revenue or is_debt) else -gross
    if credit:
        # A Πιστωτικό reverses an earlier document, so it is booked negative
        # WITHIN its own bucket: a credit note against a sale is less revenue,
        # not an expense. sum_by_type sums each bucket signed, so this lands
        # correctly without any special case downstream.
        signed = -signed

    # VAT is derived from the gross amount; debts carry only the rate (VAT is
    # stamped on Εξόφληση).
    if is_debt:
        vat_amount = None
    elif body.vat_amount is not None:
        # The figure printed on the document wins over the derived one, but is
        # re-oriented here: vat_for_write puts positive cents in both buckets
        # and negative ones on a credit note, and a stored VAT that disagreed
        # with its own row's sign would break the summation guarantee.
        magnitude = round(abs(body.vat_amount), 2)
        vat_amount = -magnitude if credit else magnitude
    else:
        vat_amount = finance.vat_for_write(signed, is_revenue, body.vat_rate)
    try:
        with database.session_scope() as session:
            tenant = _resolve_user(session, user)
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
            tenant = _resolve_user(session, user)
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
    """
    if not ocr.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Η σάρωση παραστατικών δεν έχει ρυθμιστεί (ορίστε ANTHROPIC_API_KEY).")
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
            tenant = _resolve_user(session, user)
            if not store.delete_transaction(session, tenant, record_id):
                raise HTTPException(status_code=404, detail="Η κίνηση δεν βρέθηκε.")
            return {"ok": True, "id": record_id}
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=502, detail=f"Σφάλμα βάσης δεδομένων: {exc}")
