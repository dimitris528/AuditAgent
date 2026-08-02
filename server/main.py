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
  - Airtable configured (real PAT/Base) → LIVE data; any Airtable error surfaces
    (502) rather than being masked.
  - Airtable NOT configured + DASHBOARD_DEMO != "0" → the in-memory demo dataset
    (login demo/demo), so the UI is testable before secrets exist.
"""

import datetime as _dt
import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

import airtable_client as db
import auth
import finance
from server import demo

app = FastAPI(title="Accounting SaaS API", version="2.0.0")

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
    username: str
    password: str


class TransactionCreate(BaseModel):
    client: str = Field(..., min_length=1, description="Client (Category) name")
    amount: float = Field(..., gt=0, description="GROSS amount incl. VAT, > 0")
    type: str = Field(..., description="Έσοδο | Έξοδο | Χρεωστούμενο")
    vat_rate: float = Field(default=finance.DEFAULT_VAT_RATE, ge=0, le=1)
    date: str | None = Field(default=None, description="ISO date; defaults today")
    description: str | None = None


class DebtResolve(BaseModel):
    amount: float = Field(..., gt=0)
    vat_rate: float = Field(default=finance.DEFAULT_VAT_RATE, ge=0, le=1)
    date: str | None = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _load(username):
    """Return (active, completed, transactions, is_demo) for one tenant.

    Live Airtable is preferred whenever it is configured, and its errors are
    surfaced (502) rather than masked. The demo dataset is used ONLY when
    Airtable is not configured at all (bootstrap), so live data can never be
    silently replaced by demo figures."""
    if not db.is_configured():
        if DEMO_ENABLED:
            return (demo.demo_active_projects(), demo.demo_completed_projects(),
                    demo.demo_transactions(), True)
        raise HTTPException(
            status_code=503,
            detail="Το Airtable δεν έχει ρυθμιστεί (ορίστε AIRTABLE_PAT / "
                   "AIRTABLE_BASE_ID στο .env).")
    try:
        active = db.get_active_projects(username)
        completed = db.get_completed_projects(username)
        transactions = db.get_transactions(username)
        return active, completed, transactions, False
    except db.AirtableError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


def _assert_owned(user, record_id):
    """Guard by-id mutations: the record must belong to THIS tenant. Reuses the
    Username-filtered read so one user can never resolve/delete another's row
    even with a guessed record id."""
    try:
        ids = {t.get("id") for t in db.get_transactions(user)}
    except db.AirtableError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if record_id not in ids:
        raise HTTPException(status_code=404, detail="Η κίνηση δεν βρέθηκε.")


def _serialize_txn(rec):
    f = rec.get("fields", {})
    d = finance.txn_date(rec)
    return {
        "id": rec.get("id"),
        "client": f.get("Category"),
        "amount": finance.amount(rec),
        "type": f.get("Type"),
        "is_revenue": finance.is_revenue(rec),
        "is_debt": finance.is_debt(rec),
        "vat_amount": f.get("VAT_Amount"),
        "vat_rate": f.get("VAT_Rate"),
        "date": d.isoformat() if d else None,
        "description": f.get("Description"),
        "source": f.get("Source"),
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
    """Setup diagnostics: is Airtable configured, does the connection work, and
    which auth mode is active. Handy for Step 1 verification (no secrets echoed)."""
    configured = db.is_configured()
    result = {
        "airtable_configured": configured,
        "demo_enabled": DEMO_ENABLED,
        "auth_mode": "airtable" if configured else ("demo" if DEMO_ENABLED else "disabled"),
        "jwt_secret_set": os.getenv("JWT_SECRET") not in (None, "", "dev-insecure-change-me"),
    }
    if configured:
        try:
            # Harmless connectivity probe: a formula that matches nothing.
            db.get_active_projects("__connectivity_probe__")
            result["airtable_connection"] = "ok"
        except db.AirtableError as exc:
            result["airtable_connection"] = "error"
            result["airtable_error"] = str(exc)
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


# --------------------------------------------------------------------------
# Read endpoints (tenant-scoped by the JWT)
# --------------------------------------------------------------------------
@app.get("/api/dashboard")
def dashboard(user: str = Depends(get_current_user)):
    """The full dashboard payload for the authenticated tenant: executive
    header, rich client cards, and the analytics datasets — all with the exact
    VAT/net-profit summation."""
    active, completed, transactions, is_demo = _load(user)
    payload = finance.build_dashboard(active, completed, transactions,
                                      trend_months=TREND_MONTHS)
    payload["username"] = user
    payload["demo"] = is_demo
    return payload


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
# Write endpoints (reuse the exact app.py write logic; tenant from the JWT)
# --------------------------------------------------------------------------
@app.post("/api/transactions", status_code=201)
def create_transaction(body: TransactionCreate,
                       user: str = Depends(get_current_user)):
    if body.type not in _TXN_TYPES:
        raise HTTPException(status_code=422,
                            detail=f"type must be one of {sorted(_TXN_TYPES)}")
    if not db.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Οι εγγραφές απαιτούν ρυθμισμένο Airtable (demo = μόνο ανάγνωση).")
    is_debt = body.type == finance.DEBT_TYPE
    is_revenue = body.type == "Έσοδο"
    # Sign convention identical to the Streamlit quick-entry: revenue/debt are
    # positive, expense negative. VAT is derived from the gross amount; debts
    # carry only the rate (VAT is stamped on Εξόφληση).
    signed = body.amount if (is_revenue or is_debt) else -body.amount
    vat_amount = None if is_debt else finance.vat_for_write(
        signed, is_revenue, body.vat_rate)
    try:
        rec = db.create_transaction(
            user, body.client.strip(), signed,
            description=(body.description or None),
            date=body.date,
            type_=body.type,
            source="Web",
            vat_amount=vat_amount,
            vat_rate=body.vat_rate,
        )
    except db.AirtableError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "id": rec.get("id")}


@app.post("/api/transactions/{record_id}/resolve")
def resolve_debt(record_id: str, body: DebtResolve,
                 user: str = Depends(get_current_user)):
    """Εξόφληση: flip a Χρεωστούμενο row to realised revenue and stamp its
    output VAT from the given rate."""
    if not db.is_configured():
        raise HTTPException(status_code=503, detail="Απαιτείται ρυθμισμένο Airtable.")
    _assert_owned(user, record_id)
    vat_amount = finance.vat_for_write(body.amount, True, body.vat_rate)
    try:
        db.resolve_debt_transaction(record_id, paid_date=body.date,
                                    vat_amount=vat_amount, vat_rate=body.vat_rate)
    except db.AirtableError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "id": record_id}


@app.delete("/api/transactions/{record_id}")
def delete_transaction(record_id: str, user: str = Depends(get_current_user)):
    if not db.is_configured():
        raise HTTPException(status_code=503, detail="Απαιτείται ρυθμισμένο Airtable.")
    _assert_owned(user, record_id)
    try:
        db.delete_transaction(record_id)
    except db.AirtableError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "id": record_id}
