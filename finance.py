"""
Canonical financial logic for the accounting SaaS — the SINGLE source of truth
for the FastAPI web backend (server/).

Everything here is PURE Python (stdlib only): it operates on Airtable record
dicts of the shape ``{"id": ..., "fields": {...}, "createdTime": ...}`` and
never touches a UI framework, Airtable I/O, or config. That keeps the VAT-summation
guarantees identical everywhere: VAT is rounded ONCE at the transaction level
and every aggregate is a pure partition-sum, so total VAT == Σ per-client VATs
by construction (see the module tests / vat-summation-consistency notes).

Transaction record fields used:
    Amount      SIGNED float — revenue positive, expense negative
    Type        "Έσοδο" / "Έξοδο" / "Χρεωστούμενο" (optional label)
    Category    the client name this row belongs to
    Date        ISO "YYYY-MM-DD" (optional; falls back to createdTime)
    VAT_Amount  euro VAT persisted at creation/edit (optional; derived if absent)
    VAT_Rate    decimal rate applied, e.g. 0.24 (optional)
"""

from datetime import date, datetime

# --------------------------------------------------------------------------
# Φ.Π.Α. (VAT) — per-transaction rate model
# --------------------------------------------------------------------------
VAT_RATES = (0.24, 0.13, 0.06, 0.0)
DEFAULT_VAT_RATE = 0.24
VAT_RATE_LABELS = {0.24: "24 %", 0.13: "13 %", 0.06: "6 %",
                   0.0: "0 % (Απαλλαγή)"}

# --------------------------------------------------------------------------
# Φόρος Εισοδήματος — foundational per-client income-tax scale (placeholder)
# --------------------------------------------------------------------------
TAX_BRACKET_LIMIT = 10000.0   # € net taxable income where the rate steps up
TAX_RATE_LOW = 0.22           # bracket below the limit
TAX_RATE_HIGH = 0.29          # bracket at/above the limit

# Χρεωστούμενα (debts/receivables) ride the Transactions table with this Type.
DEBT_TYPE = "Χρεωστούμενο"


# --------------------------------------------------------------------------
# Row-level primitives
# --------------------------------------------------------------------------
def amount(record):
    return float(record["fields"].get("Amount") or 0)


def is_revenue(record):
    """Revenue vs expense: the explicit Type label wins, else the sign of
    Amount (positive/zero = revenue)."""
    type_ = (record["fields"].get("Type") or "").strip()
    if type_ == "Έσοδο":
        return True
    if type_ == "Έξοδο":
        return False
    return amount(record) >= 0


def is_debt(record):
    return (record["fields"].get("Type") or "").strip() == DEBT_TYPE


def split_debts(transactions):
    """Return (regular, debts): debts never enter the revenue/expense/VAT math
    until resolved (their Type flips to "Έσοδο")."""
    regular, debts = [], []
    for txn in transactions:
        (debts if is_debt(txn) else regular).append(txn)
    return regular, debts


def sum_by_type(records):
    """Return (expense_total, revenue_total) as positive magnitudes. Each row
    is summed SIGNED within its bucket, so a manual correction adjusts its own
    bucket in either direction."""
    expense = revenue = 0.0
    for rec in records:
        amt = amount(rec)
        if is_revenue(rec):
            revenue += amt
        else:
            expense += -amt
    return expense, revenue


def transactions_by_category(transactions):
    grouped = {}
    for txn in transactions:
        key = (txn["fields"].get("Category") or "").strip().lower()
        grouped.setdefault(key, []).append(txn)
    return grouped


# --------------------------------------------------------------------------
# Φ.Π.Α. (VAT) helpers
# --------------------------------------------------------------------------
def vat_of(gross, rate):
    """The ΦΠΑ portion of a GROSS (VAT-inclusive) figure at `rate`, rounded to
    cents. Sign follows `gross`; a 0 rate yields exactly 0."""
    if not rate:
        return 0.0
    return round(gross * rate / (1 + rate), 2)


def vat_for_write(signed_amount, revenue, rate):
    """The VAT_Amount to STORE for a new/edited row — same bucket orientation
    as sum_by_type so the stored cents drop straight into the right bucket at
    read time (output VAT on revenue, input VAT on expenses)."""
    bucket = signed_amount if revenue else -signed_amount
    return vat_of(bucket, rate)


def txn_vat(record):
    """One row's VAT in its bucket's orientation — the STORED VAT_Amount when
    present, else the identical derivation at the default rate (so legacy rows
    never introduce a discrepancy)."""
    stored = record["fields"].get("VAT_Amount")
    if stored is not None:
        return float(stored)
    bucket = amount(record) if is_revenue(record) else -amount(record)
    return vat_of(bucket, DEFAULT_VAT_RATE)


def vat_by_type(records):
    """Return (expense_vat, revenue_vat): input/output ΦΠΑ magnitudes bucketed
    exactly like sum_by_type."""
    expense_vat = revenue_vat = 0.0
    for rec in records:
        v = txn_vat(rec)
        if is_revenue(rec):
            revenue_vat += v
        else:
            expense_vat += v
    return expense_vat, revenue_vat


def debts_by_client(debts):
    """Sum of OUTSTANDING debt amounts per client (category key)."""
    totals = {}
    for txn in debts:
        key = (txn["fields"].get("Category") or "").strip().lower()
        totals[key] = totals.get(key, 0.0) + amount(txn)
    return totals


def income_tax_status(taxable):
    """Foundational income-tax bracket status for a net taxable income.

    Returns a structured dict the UI renders as the progress bar: fill percent
    toward TAX_BRACKET_LIMIT, the applicable rate, and a status flag that flips
    from the low (22 %) to the high (29 %) bracket once the limit is crossed.
    """
    limit = TAX_BRACKET_LIMIT
    pct = min(max(taxable, 0.0) / limit, 1.0) * 100 if limit > 0 else 0.0
    if taxable <= 0:
        status, rate = "none", TAX_RATE_LOW
    elif taxable >= limit:
        status, rate = "over", TAX_RATE_HIGH
    else:
        status, rate = "within", TAX_RATE_LOW
    return {
        "taxable": round(taxable, 2),
        "limit": limit,
        "pct": round(pct, 1),
        "rate": rate,
        "status": status,            # "none" | "within" | "over"
        "low_rate": TAX_RATE_LOW,
        "high_rate": TAX_RATE_HIGH,
    }


def client_metrics(name, grouped, client_debt=0.0):
    """Full accounting snapshot for one client: gross AND net-of-VAT flows, the
    net ΦΠΑ balance, outstanding debts, and the net-of-VAT profit. Every euro
    figure is rounded to cents; net_vat is the per-client VAT the total sums."""
    recs = grouped.get((name or "").strip().lower(), [])
    gross_exp, gross_rev = sum_by_type(recs)
    exp_vat, rev_vat = vat_by_type(recs)
    net_vat = round(rev_vat - exp_vat, 2)          # output − input VAT
    net_rev = round(gross_rev - rev_vat, 2)
    net_exp = round(gross_exp - exp_vat, 2)
    net_profit = round(net_rev - net_exp, 2)       # net-of-VAT result
    return {
        "gross_rev": round(gross_rev, 2),
        "net_rev": net_rev,
        "gross_exp": round(gross_exp, 2),
        "net_exp": net_exp,
        "net_vat": net_vat,
        "debt": round(client_debt, 2),
        "net_profit": net_profit,
        "taxable": net_profit,
    }


def project_financials(name, grouped):
    """(revenue, expense, net) gross tuple for one client — used by the
    archived-client one-liners."""
    exp, rev = sum_by_type(grouped.get((name or "").strip().lower(), []))
    return rev, exp, rev - exp


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------
def closed_date(record):
    raw = (record["fields"].get("ClosedDate") or "")[:10]
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def txn_date(record):
    """A transaction's effective date. Prefers the ISO Date field; falls back
    to Airtable's createdTime converted to LOCAL time before taking the day."""
    raw = (record["fields"].get("Date") or "")[:10]
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    created = record.get("createdTime") or ""
    try:
        stamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.astimezone().date()


# --------------------------------------------------------------------------
# High-level builders for the web API
# --------------------------------------------------------------------------
def _client_key(name):
    return (name or "").strip().lower()


def build_clients(active_projects, grouped, debt_by_client):
    """One rich client object per ACTIVE project: identity + the 5 metrics +
    the income-tax status. The order mirrors the projects list."""
    clients = []
    for proj in active_projects:
        name = proj["fields"].get("Name") or "—"
        key = _client_key(name)
        m = client_metrics(name, grouped, debt_by_client.get(key, 0.0))
        clients.append({
            "id": proj.get("id"),
            "name": name,
            "key": key,
            "metrics": m,
            "tax": income_tax_status(m["taxable"]),
        })
    return clients


def build_header(clients, orphan_debt=0.0):
    """Executive header totals as the EXACT sum of the active client cards, so
    the header can never disagree with the grid. `orphan_debt` folds in debts
    whose client isn't active (kept separate for transparency)."""
    tv = round(sum(c["metrics"]["net_vat"] for c in clients), 2)
    return {
        "total_gross_rev": round(sum(c["metrics"]["gross_rev"] for c in clients), 2),
        "total_net_rev": round(sum(c["metrics"]["net_rev"] for c in clients), 2),
        "total_gross_exp": round(sum(c["metrics"]["gross_exp"] for c in clients), 2),
        "total_net_exp": round(sum(c["metrics"]["net_exp"] for c in clients), 2),
        "total_net_profit": round(sum(c["metrics"]["net_profit"] for c in clients), 2),
        "total_vat": tv,
        "total_debt": round(sum(c["metrics"]["debt"] for c in clients) + orphan_debt, 2),
        "vat_status": "refund" if tv < 0 else "payable" if tv > 0 else "zero",
    }


def monthly_trend(regular_txns, months=12, today=None):
    """Revenue/expense per calendar month over the last `months` months,
    oldest first — for the trend line chart."""
    today = today or date.today()
    # Build the ordered list of (year, month) buckets ending at the current one.
    buckets = []
    y, mth = today.year, today.month
    for _ in range(months):
        buckets.append((y, mth))
        mth -= 1
        if mth == 0:
            mth = 12
            y -= 1
    buckets.reverse()
    index = {ym: i for i, ym in enumerate(buckets)}
    rev = [0.0] * months
    exp = [0.0] * months
    for txn in regular_txns:
        d = txn_date(txn)
        if not d:
            continue
        i = index.get((d.year, d.month))
        if i is None:
            continue
        a = amount(txn)
        if is_revenue(txn):
            rev[i] += a
        else:
            exp[i] += -a
    labels = [f"{y:04d}-{m:02d}" for (y, m) in buckets]
    return [
        {"month": labels[i], "revenue": round(rev[i], 2), "expense": round(exp[i], 2)}
        for i in range(months)
    ]


def build_dashboard(active_projects, completed_projects, transactions,
                    trend_months=12, today=None):
    """The full dashboard payload for the web API: executive header, rich
    client cards, and the three analytics datasets — all derived from ONE pass
    over the data so every figure reconciles."""
    regular, debts = split_debts(transactions)
    grouped = transactions_by_category(regular)
    debt_by_client = debts_by_client(debts)

    clients = build_clients(active_projects, grouped, debt_by_client)
    active_keys = {c["key"] for c in clients}
    orphan_debt = round(
        sum(v for k, v in debt_by_client.items() if k not in active_keys), 2)
    header = build_header(clients, orphan_debt=orphan_debt)

    analytics = {
        # Revenue vs Expenses (gross) per active client.
        "revenue_vs_expenses": [
            {"client": c["name"],
             "revenue": c["metrics"]["gross_rev"],
             "expense": c["metrics"]["gross_exp"]}
            for c in clients
        ],
        # Net ΦΠΑ balance per active client.
        "vat_breakdown": [
            {"client": c["name"], "vat": c["metrics"]["net_vat"]}
            for c in clients
        ],
        # Monthly revenue/expense trend across the whole book.
        "monthly_trend": monthly_trend(regular, months=trend_months, today=today),
    }

    return {
        "header": header,
        "clients": clients,
        "analytics": analytics,
        "counts": {
            "active_clients": len(active_projects),
            "archived_clients": len(completed_projects),
            "transactions": len(transactions),
            "open_debts": len(debts),
        },
        "vat_rates": [
            {"value": r, "label": VAT_RATE_LABELS.get(r, f"{r:.0%}")}
            for r in VAT_RATES
        ],
    }
