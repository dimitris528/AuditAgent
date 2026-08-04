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

from datetime import date, datetime, timedelta

_ONE_DAY = timedelta(days=1)

# --------------------------------------------------------------------------
# Φ.Π.Α. (VAT) — per-transaction rate model
# --------------------------------------------------------------------------
VAT_RATES = (0.24, 0.13, 0.06, 0.0)
DEFAULT_VAT_RATE = 0.24
VAT_RATE_LABELS = {0.24: "24 %", 0.13: "13 %", 0.06: "6 %",
                   0.0: "0 % (Απαλλασσόμενο)"}


# --------------------------------------------------------------------------
# Τύποι Παραστατικών — the document a transaction was recorded from
# --------------------------------------------------------------------------
# Stored as the Greek label, matching how Type already stores "Έσοδο"/"Έξοδο":
# one convention per table beats a second, tidier one nobody expects.
DOC_SALES_INVOICE = "Τιμολόγιο Πώλησης"
DOC_SERVICE_RECEIPT = "ΑΠΥ"
DOC_CREDIT_NOTE = "Πιστωτικό"
DOC_EXPENSE = "Δαπάνη/Έξοδο"
DOC_OPERATING_EXPENSE = "Λειτουργικό Έξοδο"

# `suggests` is what the form pre-selects when this document type is chosen —
# a hint, never a constraint: a credit note can be issued against a purchase
# as well as a sale.
DOC_TYPE_META = {
    DOC_SALES_INVOICE: {"suggests": "Έσοδο", "credit": False},
    DOC_SERVICE_RECEIPT: {"suggests": "Έσοδο", "credit": False},
    DOC_CREDIT_NOTE: {"suggests": "Έσοδο", "credit": True},
    DOC_EXPENSE: {"suggests": "Έξοδο", "credit": False},
    DOC_OPERATING_EXPENSE: {"suggests": "Έξοδο", "credit": False},
}
DOC_TYPES = tuple(DOC_TYPE_META)


def is_credit_note(doc_type):
    """A Πιστωτικό REVERSES an earlier document, so its amount is booked
    negative within its own bucket rather than as the opposite bucket.

    A credit note against a sale is not an expense — it is less revenue, and
    filing it as an expense would overstate both sides of the books while
    leaving the net result right. sum_by_type already sums each row signed
    within its bucket, so a negative row does exactly the right thing.
    """
    return (doc_type or "").strip() == DOC_CREDIT_NOTE

# --------------------------------------------------------------------------
# Φόρος Εισοδήματος — foundational per-client income-tax scale (placeholder)
# --------------------------------------------------------------------------
TAX_BRACKET_LIMIT = 10000.0   # € net taxable income where the rate steps up
TAX_RATE_LOW = 0.22           # bracket below the limit
TAX_RATE_HIGH = 0.29          # bracket at/above the limit

# Χρεωστούμενα (debts/receivables) ride the Transactions table with this Type.
DEBT_TYPE = "Χρεωστούμενο"


# --------------------------------------------------------------------------
# Περίοδος — period filtering for the dashboard
# --------------------------------------------------------------------------
QUARTER_MONTHS = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}


def period_bounds(year=None, quarter=None, month=None):
    """Return (start, end) inclusive dates for a period, or (None, None) for
    "all time".

    Precedence is month > quarter > year, so a caller that sends both a quarter
    and a month gets the narrower window rather than a contradiction. A quarter
    or month without a year is meaningless and is treated as all-time.
    """
    if not year:
        return None, None
    year = int(year)
    if month:
        month = int(month)
        if not 1 <= month <= 12:
            return None, None
        start = date(year, month, 1)
        end = (date(year + 1, 1, 1) if month == 12
               else date(year, month + 1, 1)) - _ONE_DAY
        return start, end
    if quarter:
        quarter = int(quarter)
        if quarter not in QUARTER_MONTHS:
            return None, None
        first, last = QUARTER_MONTHS[quarter]
        start = date(year, first, 1)
        end = (date(year + 1, 1, 1) if last == 12
               else date(year, last + 1, 1)) - _ONE_DAY
        return start, end
    return date(year, 1, 1), date(year, 12, 31)


def in_period(record, start, end):
    """True when a transaction falls inside [start, end].

    A row whose date cannot be determined at all is KEPT for the all-time view
    and EXCLUDED from any bounded period: silently dropping it from every view
    would make the totals disagree with the transaction list, while forcing it
    into an arbitrary period would misstate that period.
    """
    if start is None and end is None:
        return True
    d = txn_date(record)
    if d is None:
        return False
    if start is not None and d < start:
        return False
    if end is not None and d > end:
        return False
    return True


def filter_period(transactions, start, end):
    if start is None and end is None:
        return list(transactions)
    return [t for t in transactions if in_period(t, start, end)]


def available_years(transactions):
    """Descending list of years present in the data, for the period selector."""
    years = {d.year for d in (txn_date(t) for t in transactions) if d}
    return sorted(years, reverse=True)


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


def gross_from_net(net, rate):
    """The VAT-inclusive total for a NET (VAT-exclusive) figure.

    The form lets either side be typed — an invoice states its net value, a
    till receipt only its total — and the conversion happens HERE rather than
    in the browser so both entry modes round identically. Doing it in
    JavaScript instead would put a second, subtly different rounding rule on
    the other side of the wire.
    """
    return round(net * (1 + (rate or 0)), 2)


def net_from_gross(gross, rate):
    """The VAT-exclusive value of a GROSS figure.

    Deliberately `gross - vat_of(...)` rather than `gross / (1 + rate)`: those
    two disagree by a cent often enough to matter, and this way net + VAT is
    always exactly the gross that was stored.
    """
    return round(gross - vat_of(gross, rate), 2)


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


def txn_net(record):
    """One row's value net of VAT, keeping the row's own sign — or None when
    no VAT was stored.

    The two figures are oriented differently and cannot simply be subtracted:
    `Amount` is signed (expenses negative), while `VAT_Amount` is a bucket
    magnitude that is positive on both sides (see vat_for_write). Subtracting
    directly turns a -124 expense with 24 of VAT into -148 instead of -100.
    Magnitudes are subtracted and the gross sign reapplied.

    Returns None rather than deriving a rate, so a Χρεωστούμενο row — which
    carries no VAT until it is settled — does not display an invented one.
    """
    stored = record["fields"].get("VAT_Amount")
    if stored is None:
        return None
    gross = amount(record)
    magnitude = abs(gross) - abs(float(stored))
    return round(-magnitude if gross < 0 else magnitude, 2)


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


# --------------------------------------------------------------------------
# Χρεωστούμενα — ageing and overdue alerts
# --------------------------------------------------------------------------
# Greek B2B invoices are conventionally settled within 30 days. Rows written
# before due dates existed carry none, so rather than treat them as never due
# — which would silently exclude the oldest debts from every alert, exactly
# the ones worth alerting on — their due date is inferred from the issue date.
DEFAULT_PAYMENT_TERMS = 30
# How far ahead "λήγει σύντομα" reaches.
DUE_SOON_DAYS = 7

# (label, from_day, to_day) — to_day None means open-ended.
AGING_BUCKETS = (
    ("1-30", 1, 30),
    ("31-60", 31, 60),
    ("61-90", 61, 90),
    ("90+", 91, None),
)


def debt_due_date(record, terms=DEFAULT_PAYMENT_TERMS):
    """When a debt falls due: the stated date, else the issue date + terms."""
    raw = (record["fields"].get("DueDate") or "")[:10]
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    issued = txn_date(record)
    return issued + timedelta(days=terms) if issued else None


def debt_status(record, today=None, terms=DEFAULT_PAYMENT_TERMS):
    """"overdue" | "due_soon" | "current" | "unknown".

    "unknown" is for a debt with neither a due date nor a usable issue date:
    it is still outstanding and still counted in the totals, but claiming it is
    overdue would be an invention.
    """
    due = debt_due_date(record, terms)
    if due is None:
        return "unknown"
    today = today or date.today()
    if due < today:
        return "overdue"
    return "due_soon" if (due - today).days <= DUE_SOON_DAYS else "current"


def days_overdue(record, today=None, terms=DEFAULT_PAYMENT_TERMS):
    """Days past due, or 0 for anything not yet overdue."""
    due = debt_due_date(record, terms)
    if due is None:
        return 0
    return max(0, ((today or date.today()) - due).days)


def aging_buckets(debts, today=None, terms=DEFAULT_PAYMENT_TERMS):
    """Overdue amounts split by how long they have been overdue.

    Only OVERDUE money is bucketed — a debt that is not yet due has no age to
    report, and folding it into "1-30" would overstate the oldest bucket the
    first time anyone looked.
    """
    today = today or date.today()
    totals = {label: 0.0 for label, _, _ in AGING_BUCKETS}
    for txn in debts:
        days = days_overdue(txn, today, terms)
        if days <= 0:
            continue
        for label, start, end in AGING_BUCKETS:
            if days >= start and (end is None or days <= end):
                totals[label] += amount(txn)
                break
    return [{"label": label, "amount": round(totals[label], 2)}
            for label, _, _ in AGING_BUCKETS]


# Worst-first, so a client's headline status is the worst of its debts.
_STATUS_RANK = {"overdue": 3, "due_soon": 2, "current": 1, "unknown": 0}


def build_debt_alerts(projects, debts, today=None, terms=DEFAULT_PAYMENT_TERMS):
    """Who owes money, how much, and how late — the dashboard alert section.

    Fed the FULL transaction history rather than the selected period. An
    overdue debt is a fact about today: suppressing it because the user
    happens to be looking at last quarter would hide exactly the thing the
    alert exists to surface. The header's own debt KPI stays period-scoped as
    it always was, and the UI labels this section as all-time.

    `projects` supplies the client ids the UI needs to open a drawer, and is
    taken from active AND archived clients — a closed client can still owe.
    """
    today = today or date.today()
    ids = {}
    for proj in projects:
        key = _client_key(proj["fields"].get("Name"))
        if key and proj.get("id") is not None:
            ids[key] = proj["id"]

    by_client = {}
    for txn in debts:
        name = (txn["fields"].get("Category") or "—").strip() or "—"
        key = _client_key(name)
        entry = by_client.setdefault(key, {
            "id": ids.get(key),
            "name": name,
            "key": key,
            "total": 0.0,
            "overdue": 0.0,
            "count": 0,
            "status": "unknown",
            "max_days_overdue": 0,
            "debts": [],
        })
        owed = amount(txn)
        status = debt_status(txn, today, terms)
        late = days_overdue(txn, today, terms)
        due = debt_due_date(txn, terms)

        entry["total"] += owed
        entry["count"] += 1
        if status == "overdue":
            entry["overdue"] += owed
        if _STATUS_RANK[status] > _STATUS_RANK[entry["status"]]:
            entry["status"] = status
        entry["max_days_overdue"] = max(entry["max_days_overdue"], late)
        issued = txn_date(txn)
        entry["debts"].append({
            "id": txn.get("id"),
            "amount": round(owed, 2),
            "date": issued.isoformat() if issued else None,
            "due_date": due.isoformat() if due else None,
            "status": status,
            "days_overdue": late,
            "doc_number": txn["fields"].get("DocNumber"),
            "doc_type": txn["fields"].get("DocType"),
            "description": txn["fields"].get("Description"),
        })

    clients = []
    for entry in by_client.values():
        entry["total"] = round(entry["total"], 2)
        entry["overdue"] = round(entry["overdue"], 2)
        # Latest first within a client, so the most pressing debt reads first.
        entry["debts"].sort(key=lambda d: d["days_overdue"], reverse=True)
        clients.append(entry)

    # Worst offenders first: most overdue, then largest balance.
    clients.sort(key=lambda c: (-_STATUS_RANK[c["status"]],
                                -c["max_days_overdue"], -c["total"]))

    overdue_total = round(sum(c["overdue"] for c in clients), 2)
    return {
        "total": round(sum(c["total"] for c in clients), 2),
        "overdue_total": overdue_total,
        "count": sum(c["count"] for c in clients),
        "overdue_count": sum(1 for t in debts
                             if debt_status(t, today, terms) == "overdue"),
        "clients_affected": len(clients),
        "clients_overdue": sum(1 for c in clients if c["status"] == "overdue"),
        "aging": aging_buckets(debts, today, terms),
        "clients": clients,
        "payment_terms": terms,
    }


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
            # Absent on the Airtable path, which never had the column — the UI
            # treats it as optional and simply searches by name there.
            "afm": proj["fields"].get("AFM"),
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
                    trend_months=12, today=None, all_transactions=None,
                    payment_terms=DEFAULT_PAYMENT_TERMS):
    """The full dashboard payload for the web API: executive header, rich
    client cards, the analytics datasets, and the overdue-debt alerts — all
    derived from ONE pass over the data so every figure reconciles.

    `all_transactions` is the UNFILTERED history, used only for the alerts:
    every other figure here is period-scoped, but "who is overdue" is a fact
    about today and must not change because the user selected a past quarter.
    Defaults to `transactions`, so a caller that does not filter gets the same
    answer either way.
    """
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

    # Alerts run over the whole book, and over active AND archived clients —
    # a closed client can still owe, and that debt is the one most likely to be
    # forgotten.
    _regular, all_debts = split_debts(
        transactions if all_transactions is None else all_transactions)
    alerts = build_debt_alerts(
        list(active_projects) + list(completed_projects), all_debts,
        today=today, terms=payment_terms)

    return {
        "header": header,
        "clients": clients,
        "analytics": analytics,
        "debt_alerts": alerts,
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
        "doc_types": [
            {"value": d, "label": d, **DOC_TYPE_META[d]} for d in DOC_TYPES
        ],
    }
