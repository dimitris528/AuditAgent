"""
Airtable data layer for the AI Document Auditor Micro-SaaS.

EVERY read is filtered by the caller's Username (the tenant key), so one
tenant can never see or touch another tenant's data.

Required Airtable schema (create these in your base before running the app):

  Projects table  (env AIRTABLE_PROJECTS_TABLE, default "Projects")
      Name          Single line text    - category/project name
      Username      Single line text    - owner's username  (tenant key)
      Status        Single select       - options: "Active", "Completed"
      ClosedDate    Date                - set when the category is archived

  Transactions table  (env AIRTABLE_TRANSACTIONS_TABLE, default "Transactions")
      Username      Single line text    - owner's username  (tenant key)
      Category      Single line text    - category name this row belongs to
      Amount        Number              - SIGNED: revenue positive, expense
                                          negative (the app displays abs())
      Date          Date                - ISO "YYYY-MM-DD" (optional)
      Description   Single line text    - free notes / supplier (optional)
      FileHash      Single line text    - SHA-256 of the uploaded file; the
                                          per-tenant duplicate-receipt guard
                                          (AI scanning path only)
      Type          Single select       - "Έσοδο" / "Έξοδο" / "Χρεωστούμενο"
                                          label (optional; set by both the
                                          manual-entry and AI-scan paths — the
                                          sign of Amount stays the source of
                                          truth for revenue vs expense).
                                          "Χρεωστούμενο" rows are DEBTS/
                                          receivables: positive Amount,
                                          excluded from every revenue/expense
                                          figure until resolve_debt_transaction
                                          flips the Type to "Έσοδο"
      Source        Single select       - "Manual" (quick-entry/debt forms) or
                                          "AI Scan" (invoice/receipt upload
                                          flow); optional
      VAT_Amount    Number              - the ΦΠΑ (VAT) portion of this row's
                                          gross Amount, in euro, rounded to
                                          cents and STORED at creation/edit
                                          so per-client and total VAT are
                                          exact partition-sums (revenue rows
                                          carry output VAT, expense rows input
                                          VAT). OPTIONAL — see the VAT-column
                                          note below.
      VAT_Rate      Number              - the ΦΠΑ rate applied to this row as a
                                          decimal (0.24 / 0.13 / 0.06 / 0.0);
                                          per-transaction, chosen at entry.
                                          OPTIONAL.
      The save payload NEVER contains any key outside these columns —
      enforced by _TRANSACTION_COLUMNS below — so a failed write can only
      mean one of them is missing/renamed in the base.

      VAT-column note: VAT_Amount / VAT_Rate are recent OPTIONAL additions. If
      the base does not have them yet, create_transaction/resolve_debt_
      transaction catch the resulting UNKNOWN_FIELD_NAME and RETRY the write
      without the VAT fields, so saves keep working (VAT just isn't persisted
      until you add the two Number columns). The app derives VAT on the fly
      for any row missing VAT_Amount, so analytics never break either.

  Users table  (env AIRTABLE_USERS_TABLE, default "Users")
      Username            Single line text  - tenant key (login name)
      Password            Text              - pbkdf2_sha256$… hash of the
                                              login password (passwords.py);
                                              legacy plaintext cells still
                                              verify and are rewritten as
                                              hashes on first login
      SubscriptionStatus  Single select     - options: "Active", "Expired",
                                              "Inactive" (trial ran out)
      TrialExpiry         Date+time         - end of the 15-day free trial
                                              (UTC ISO, set at registration);
                                              blank on paying accounts — the
                                              Stripe activation clears it
      Email               Single line text  - the password-reset flow's
                                              lookup key (and the Stripe
                                              webhook's fallback match when
                                              the checkout carries no
                                              client_reference_id); accounts
                                              without it simply can't use
                                              "Ξέχασα τον κωδικό μου"
      ResetToken          Text              - 6-digit password-reset code
                                              (app.py); prefer Text — a
                                              Number column drops leading
                                              zeros, which the app tolerates
                                              by zero-padding on comparison
      ResetTokenExpiry    Date+time         - UTC expiry of ResetToken
                                              (written as ISO-8601 …Z)
"""

import re
from datetime import date, datetime

import requests

from config import (
    AIRTABLE_PAT,
    AIRTABLE_BASE_ID,
    AIRTABLE_PROJECTS_TABLE,
    AIRTABLE_TRANSACTIONS_TABLE,
    AIRTABLE_USERS_TABLE,
)

API_ROOT = "https://api.airtable.com/v0"

# The ONLY columns a Transactions write may carry (case-sensitive — they must
# match the Airtable base exactly). Type and Source are optional labels the
# manual-entry and AI-scan flows send; the analytics still derive revenue/
# expense from the sign of Amount.
_TRANSACTION_COLUMNS = frozenset(
    {"Username", "Amount", "Date", "Category", "FileHash", "Description",
     "Type", "Source", "VAT_Amount", "VAT_Rate"}
)

# The OPTIONAL VAT columns. A base that hasn't added them yet rejects a write
# that carries them (UNKNOWN_FIELD_NAME); _write_transaction_fields transparently
# retries WITHOUT these keys so saves never break before the columns exist.
_VAT_COLUMNS = frozenset({"VAT_Amount", "VAT_Rate"})


class AirtableError(RuntimeError):
    """Raised on configuration, network, or Airtable API failures."""


# The placeholder values config.py falls back to when a secret is unset.
_PAT_PLACEHOLDER = "YOUR_PERSONAL_ACCESS_TOKEN"
_BASE_PLACEHOLDER = "YOUR_BASE_ID"


def is_configured():
    """True when a real Airtable PAT + Base ID are present (not the placeholder
    defaults). Lets the web backend decide between live data and the demo
    dataset WITHOUT issuing a failing request first."""
    return bool(
        AIRTABLE_PAT and AIRTABLE_PAT != _PAT_PLACEHOLDER
        and AIRTABLE_BASE_ID and AIRTABLE_BASE_ID != _BASE_PLACEHOLDER
    )


def _headers():
    if not AIRTABLE_PAT or AIRTABLE_PAT == _PAT_PLACEHOLDER:
        raise AirtableError("Airtable PAT is not configured (set AIRTABLE_PAT in .env).")
    if not AIRTABLE_BASE_ID or AIRTABLE_BASE_ID == _BASE_PLACEHOLDER:
        raise AirtableError("Airtable Base ID is not configured (set AIRTABLE_BASE_ID in .env).")
    return {
        "Authorization": f"Bearer {AIRTABLE_PAT}",
        "Content-Type": "application/json",
    }


def _url(table):
    return f"{API_ROOT}/{AIRTABLE_BASE_ID}/{requests.utils.quote(table)}"


def _sanitize(value):
    """Strip single quotes so a value can't break out of a filterByFormula literal."""
    return str(value).replace("'", "")


def _api_error_detail(resp):
    """Extract Airtable's structured error (type + message) from a response.

    Airtable reports schema problems here — e.g. UNKNOWN_FIELD_NAME names the
    exact column that broke the write — so surface it verbatim instead of the
    raw JSON blob.
    """
    try:
        err = resp.json().get("error")
    except ValueError:
        return resp.text
    if isinstance(err, dict):
        etype = err.get("type") or "UNKNOWN"
        message = err.get("message") or ""
        return f"{etype} — {message}".strip(" —")
    return str(err) if err else resp.text


def _request(method, table, **kwargs):
    try:
        resp = requests.request(method, _url(table), headers=_headers(), timeout=30, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise AirtableError(f"Network error talking to Airtable: {exc}") from exc
    if resp.status_code >= 400:
        detail = _api_error_detail(resp)
        sent_fields = ""
        payload = kwargs.get("json")
        if isinstance(payload, dict):
            fields = payload.get("fields") or {}
            if not fields and payload.get("records"):
                fields = payload["records"][0].get("fields", {})
            if fields:
                sent_fields = f" | στάλθηκαν οι στήλες: {sorted(fields)}"
        print(f"[ERROR] Airtable {method} '{table}' failed "
              f"({resp.status_code}): {detail}{sent_fields}")
        raise AirtableError(
            f"Airtable {resp.status_code} στον πίνακα «{table}»: {detail}{sent_fields}"
        )
    return resp.json()


def _select(table, formula):
    """Return all records matching an Airtable formula, following pagination."""
    records = []
    params = {"filterByFormula": formula, "pageSize": 100}
    while True:
        data = _request("GET", table, params=params)
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            return records
        params["offset"] = offset


def _delete_records(table, record_ids):
    """Delete records by id, batched to Airtable's 10-per-request limit."""
    deleted = 0
    for start in range(0, len(record_ids), 10):
        batch = record_ids[start:start + 10]
        params = [("records[]", rid) for rid in batch]
        data = _request("DELETE", table, params=params)
        deleted += len(data.get("records", []))
    return deleted


# --- Users ------------------------------------------------------------------
def find_user(username=None, email=None):
    """Return the first matching Users record ({id, fields, ...}) or None.

    Username (the tenant key) is tried first — the Stripe webhook passes the
    checkout's client_reference_id here for an exact match. Email is the
    fallback and needs the OPTIONAL Email column; if that column doesn't
    exist in the base, Airtable rejects the formula and the fallback quietly
    yields None instead of erroring the caller.
    """
    if username:
        records = _select(AIRTABLE_USERS_TABLE,
                          f"{{Username}}='{_sanitize(username)}'")
        if records:
            return records[0]
    if email:
        formula = f"LOWER({{Email}})='{_sanitize(email).strip().lower()}'"
        try:
            records = _select(AIRTABLE_USERS_TABLE, formula)
        except AirtableError:
            return None  # base has no Email column — username match only
        if records:
            return records[0]
    return None


def create_user(username, email, hashed_password, trial_expiry_iso):
    """Create a Users row for the self-registration form (app.py).

    Password arrives ALREADY hashed (passwords.hash_password) — plaintext
    never reaches Airtable. The account starts "Active" on a free trial:
    TrialExpiry (UTC ISO) marks when the subscription gate auto-flips it to
    "Inactive" and shows the Stripe paywall. The caller is responsible for
    uniqueness checks (find_user) before creating.
    """
    fields = {"Username": username, "Email": email,
              "Password": hashed_password,
              "SubscriptionStatus": "Active",
              "TrialExpiry": trial_expiry_iso}
    return _request("POST", AIRTABLE_USERS_TABLE,
                    json={"fields": fields, "typecast": True})


def set_reset_token(record_id, token, expiry_iso):
    """Store a password-reset code and its UTC expiry on one Users row.

    typecast lets a dateTime-typed ResetTokenExpiry cell parse the ISO
    string (and a Number-typed ResetToken coerce — see the schema note on
    leading zeros).
    """
    body = {"records": [{"id": record_id,
                         "fields": {"ResetToken": token,
                                    "ResetTokenExpiry": expiry_iso}}],
            "typecast": True}
    return _request("PATCH", AIRTABLE_USERS_TABLE, json=body)


def clear_reset_token(record_id):
    """Blank one Users row's reset columns (expired code / too many tries)."""
    body = {"records": [{"id": record_id,
                         "fields": {"ResetToken": None,
                                    "ResetTokenExpiry": None}}]}
    return _request("PATCH", AIRTABLE_USERS_TABLE, json=body)


def complete_password_reset(record_id, hashed_password):
    """Write the new Password hash and clear both reset columns in ONE
    PATCH, so a verified reset can't leave a still-live code behind."""
    body = {"records": [{"id": record_id,
                         "fields": {"Password": hashed_password,
                                    "ResetToken": None,
                                    "ResetTokenExpiry": None}}]}
    return _request("PATCH", AIRTABLE_USERS_TABLE, json=body)


def update_user_password(record_id, hashed_password):
    """Overwrite one Users row's Password cell with a pbkdf2_sha256$… hash.

    Used by the login gate's transparent migration: a legacy plaintext cell
    that just verified gets rewritten as its hash. No typecast — if the
    Password column was created as Number instead of Text, the write fails
    loudly (AirtableError) rather than silently mangling the hash.
    """
    body = {"records": [{"id": record_id,
                         "fields": {"Password": hashed_password}}]}
    return _request("PATCH", AIRTABLE_USERS_TABLE, json=body)


def set_subscription_status(record_id, status):
    """Flip one Users row's SubscriptionStatus ("Active" / "Expired" /
    "Inactive").

    Setting "Active" also clears TrialExpiry: activation comes from a PAID
    Stripe checkout, and a leftover past trial date would make the
    subscription gate flip the paying account straight back to Inactive.

    typecast lets Airtable create the select option if it's missing, so a
    base whose SubscriptionStatus choices were hand-typed differently can't
    silently reject the webhook's activation.
    """
    fields = {"SubscriptionStatus": status}
    if status == "Active":
        fields["TrialExpiry"] = None
    body = {"records": [{"id": record_id, "fields": fields}],
            "typecast": True}
    return _request("PATCH", AIRTABLE_USERS_TABLE, json=body)


# --- Projects -------------------------------------------------------------
def create_project(username, name):
    fields = {"Name": name, "Username": username, "Status": "Active"}
    return _request("POST", AIRTABLE_PROJECTS_TABLE, json={"fields": fields, "typecast": True})


def get_active_projects(username):
    formula = f"AND({{Username}}='{_sanitize(username)}', {{Status}}='Active')"
    return _select(AIRTABLE_PROJECTS_TABLE, formula)


def get_completed_projects(username):
    formula = f"AND({{Username}}='{_sanitize(username)}', {{Status}}='Completed')"
    return _select(AIRTABLE_PROJECTS_TABLE, formula)


def find_active_project(username, name):
    """Return the caller's active project whose name matches (case-insensitive), or None."""
    target = (name or "").strip().lower()
    for rec in get_active_projects(username):
        if (rec["fields"].get("Name") or "").strip().lower() == target:
            return rec
    return None


def close_project(record_id, closed_date=None):
    """Close a project: Status -> "Completed", ClosedDate -> today (or the
    given date). "Completed" is the canonical closed status — the recap tab
    and the retention cleanup both filter on it — so the UI's "Αρχειοθέτηση
    Κατηγορίας" action maps here.

    ClosedDate is always stamped: the recap date-range filter and the
    retention policy silently skip rows without one.
    """
    fields = {
        "Status": "Completed",
        "ClosedDate": to_iso_date(closed_date) or date.today().isoformat(),
    }
    body = {"records": [{"id": record_id, "fields": fields}], "typecast": True}
    return _request("PATCH", AIRTABLE_PROJECTS_TABLE, json=body)


def to_iso_date(value):
    """Normalize a date-ish value to Airtable's strict "YYYY-MM-DD", or None.

    Accepts date/datetime objects and numeric strings in ISO or EU order —
    "2026-06-26", "26/06/2026", "26.6.26", "26-06-2026", with or without a
    trailing time part. Ambiguous day/month is read day-first (EU invoices);
    if that yields an impossible month the two are swapped. Anything
    unparseable returns None so a save never fails on a bad date string.
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return None

    parts = [p for p in re.split(r"\D+", text) if p]
    if len(parts) < 3:
        return None
    a, b, c = parts[:3]  # extra parts (a time component) are ignored

    if len(a) == 4:                      # ISO order: YYYY-MM-DD
        year, month, day = a, b, c
    else:                                # EU order: DD-MM-YYYY (or -YY)
        day, month, year = a, b, c
        if len(year) == 2:
            year = "20" + year
    day, month, year = int(day), int(month), int(year)
    if month > 12 and day <= 12:         # tolerate a US-ordered slip
        day, month = month, day
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


# --- Transactions ---------------------------------------------------------
def _looks_like_missing_vat_column(exc):
    """True when an AirtableError is an UNKNOWN_FIELD_NAME naming a VAT column.

    Lets create/resolve retry without the optional VAT fields when the base
    hasn't added them yet, instead of failing the whole save."""
    text = str(exc)
    return "UNKNOWN_FIELD_NAME" in text and any(col in text for col in _VAT_COLUMNS)


def _write_transaction_fields(method, fields, record_id=None):
    """POST (create) or PATCH (update by record_id) a Transactions row, with a
    one-shot fallback that strips the OPTIONAL VAT columns if the base rejects
    them as unknown — so VAT support degrades gracefully on a base that hasn't
    added VAT_Amount / VAT_Rate yet."""
    def body(payload):
        if record_id is None:
            return {"fields": payload, "typecast": True}
        return {"records": [{"id": record_id, "fields": payload}],
                "typecast": True}

    try:
        return _request(method, AIRTABLE_TRANSACTIONS_TABLE, json=body(fields))
    except AirtableError as exc:
        if _VAT_COLUMNS & set(fields) and _looks_like_missing_vat_column(exc):
            trimmed = {k: v for k, v in fields.items() if k not in _VAT_COLUMNS}
            print("[WARN] Transactions base has no VAT_Amount/VAT_Rate column — "
                  "saving without VAT (add the columns to persist ΦΠΑ).")
            return _request(method, AIRTABLE_TRANSACTIONS_TABLE, json=body(trimmed))
        raise


def create_transaction(username, category, amount,
                       description=None, date=None, file_hash=None,
                       type_=None, source=None, vat_amount=None, vat_rate=None):
    """Save one transaction row.

    `amount` is SIGNED: positive = revenue (Έσοδο), negative = expense
    (Έξοδο) — the analytics read the sign, not the Type column. `type_`
    ("Έσοδο"/"Έξοδο") and `source` (e.g. "Manual") are optional labels,
    written only when provided. `vat_amount` (euro, already rounded to cents
    by the caller) and `vat_rate` (decimal, e.g. 0.24) persist this row's
    per-transaction ΦΠΑ; both are optional and, if the base lacks the columns,
    are dropped by _write_transaction_fields so the save still succeeds. The
    payload is restricted to the schema columns and any accidental extra key
    raises BEFORE the API call, so a save can never fail because of a stray
    field name.
    """
    fields = {
        "Username": username,
        "Category": category,
        "Amount": amount,
    }
    if description:
        fields["Description"] = description
    if file_hash:
        fields["FileHash"] = file_hash
    if type_:
        fields["Type"] = type_
    if source:
        fields["Source"] = source
    if vat_amount is not None:
        fields["VAT_Amount"] = vat_amount
    if vat_rate is not None:
        fields["VAT_Rate"] = vat_rate
    # Airtable's Date column rejects non-ISO strings with a 422, so normalize
    # here — every caller is covered. An unparseable date is dropped rather
    # than allowed to fail the whole save.
    iso_date = to_iso_date(date)
    if iso_date:
        fields["Date"] = iso_date

    unexpected = set(fields) - _TRANSACTION_COLUMNS
    if unexpected:
        raise AirtableError(
            f"Εσωτερικό σφάλμα: μη έγκυρες στήλες στο payload: {sorted(unexpected)}"
        )
    return _write_transaction_fields("POST", fields)


def resolve_debt_transaction(record_id, paid_date=None,
                             vat_amount=None, vat_rate=None):
    """Mark a Χρεωστούμενο row as paid: Type -> "Έσοδο" and Date -> the
    payment date (today by default).

    The Amount is already stored positive, so the Type flip alone moves the
    value out of the Χρεωστούμενα KPI and into the same Category's revenue —
    one row, nothing to keep in sync. Now that the row becomes REALISED
    revenue, its output ΦΠΑ is stamped too (vat_amount/vat_rate, computed by
    the caller from the stored rate); both are optional and stripped
    automatically on a base without the VAT columns. typecast lets Airtable
    create the "Έσοδο" select option if the base doesn't have it yet.
    """
    fields = {
        "Type": "Έσοδο",
        "Date": to_iso_date(paid_date) or date.today().isoformat(),
    }
    if vat_amount is not None:
        fields["VAT_Amount"] = vat_amount
    if vat_rate is not None:
        fields["VAT_Rate"] = vat_rate
    return _write_transaction_fields("PATCH", fields, record_id=record_id)


def find_transaction_by_hash(username, file_hash):
    """Return this tenant's first transaction carrying the given FileHash,
    or None. Scoped to Username so one tenant's receipt never blocks another's.
    """
    if not file_hash:
        return None
    formula = (f"AND({{Username}}='{_sanitize(username)}', "
               f"{{FileHash}}='{_sanitize(file_hash)}')")
    records = _select(AIRTABLE_TRANSACTIONS_TABLE, formula)
    return records[0] if records else None


def delete_transaction(record_id):
    """Permanently delete a single transaction row."""
    return _delete_records(AIRTABLE_TRANSACTIONS_TABLE, [record_id])


def get_transactions(username, category=None):
    """All transactions for this username, optionally narrowed to one category."""
    formula = f"{{Username}}='{_sanitize(username)}'"
    records = _select(AIRTABLE_TRANSACTIONS_TABLE, formula)
    if category is not None:
        target = category.strip().lower()
        records = [r for r in records
                   if (r["fields"].get("Category") or "").strip().lower() == target]
    return records


# --- Data retention -------------------------------------------------------
def _subtract_years(d, years):
    """Return `d` minus `years`, clamping Feb 29 -> Feb 28 on non-leap years."""
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(year=d.year - years, day=28)


def cleanup_old_closed_projects(retention_years=2, today=None):
    """Enforce the data-retention policy across ALL tenants.

    Scans the Projects table for rows whose Status is "Completed" and whose
    ClosedDate is older than `retention_years` from today, deletes them, and
    cascades the delete to each project's Transactions (matched on the same
    tenant's Username + Category name). Returns (projects_deleted,
    transactions_deleted).
    """
    cutoff = _subtract_years(today or date.today(), retention_years)

    project_ids = []
    transaction_ids = []
    for proj in _select(AIRTABLE_PROJECTS_TABLE, "{Status}='Completed'"):
        fields = proj["fields"]
        closed = (fields.get("ClosedDate") or "")[:10]
        if not closed:
            continue
        try:
            closed_date = date.fromisoformat(closed)
        except ValueError:
            continue  # unparseable date — leave it untouched
        if closed_date >= cutoff:
            continue

        project_ids.append(proj["id"])

        # Cascade: delete this tenant's transactions for this category name.
        username = fields.get("Username")
        name = fields.get("Name")
        if username and name:
            formula = (f"AND({{Username}}='{_sanitize(username)}', "
                       f"{{Category}}='{_sanitize(name)}')")
            transaction_ids += [t["id"] for t in
                                _select(AIRTABLE_TRANSACTIONS_TABLE, formula)]

    # Delete children first so a partial failure never orphans transactions.
    deleted_txns = _delete_records(AIRTABLE_TRANSACTIONS_TABLE, transaction_ids)
    deleted_projects = _delete_records(AIRTABLE_PROJECTS_TABLE, project_ids)
    return deleted_projects, deleted_txns
