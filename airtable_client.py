"""
Airtable data layer for the AI Document Auditor Micro-SaaS.

EVERY read is filtered by the caller's UserId (the tenant key), so one tenant
can never see or touch another tenant's data.

Required Airtable schema (create these in your base before running the bot):

  Projects table  (env AIRTABLE_PROJECTS_TABLE, default "Projects")
      Name          Single line text    - project name
      UserId   Single line text    - owner's user id  (tenant key)
      Status        Single select       - options: "Active", "Completed"
      ClosedDate    Date                - set when the project is closed

  Transactions table  (env AIRTABLE_TRANSACTIONS_TABLE, default "Transactions")
      UserId   Single line text    - owner's user id  (tenant key)
      Project       Single line text    - project name this row belongs to
      Type          Single select       - options: "Expense", "Revenue"
      Amount        Number              - numeric value, no currency symbol
      Description   Single line text    - free text / materials summary
      Provider      Single line text    - supplier (invoices only)
      Date          Date                - invoice date, ISO "YYYY-MM-DD" (optional)
      Source        Single select       - options: "Invoice", "Manual"
      FileHash      Single line text    - SHA-256 of the uploaded file; the
                                          per-tenant duplicate-receipt guard
      (Airtable's built-in createdTime is used for monthly/annual grouping.)

  Users table  (env AIRTABLE_USERS_TABLE, default "Users")
      UserId              Single line text  - tenant key (phone number / username)
      SubscriptionStatus  Single select     - options: "Active", "Expired"
      PIN                 Text or Number    - 4-digit login PIN for this user
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


class AirtableError(RuntimeError):
    """Raised on configuration, network, or Airtable API failures."""


def _headers():
    if not AIRTABLE_PAT or AIRTABLE_PAT == "YOUR_PERSONAL_ACCESS_TOKEN":
        raise AirtableError("Airtable PAT is not configured (set AIRTABLE_PAT in .env).")
    if not AIRTABLE_BASE_ID or AIRTABLE_BASE_ID == "YOUR_BASE_ID":
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


def _request(method, table, **kwargs):
    try:
        resp = requests.request(method, _url(table), headers=_headers(), timeout=30, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise AirtableError(f"Network error talking to Airtable: {exc}") from exc
    if resp.status_code >= 400:
        raise AirtableError(f"Airtable API error {resp.status_code}: {resp.text}")
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
def get_user_record(user_id):
    """Return the tenant's Users-table fields dict, or None when no row exists.

    Used by the login gate to verify the 4-digit PIN and read the
    SubscriptionStatus in a single Airtable round-trip.
    Raises AirtableError on API failures, including a missing Users table.
    """
    formula = f"{{UserId}}='{_sanitize(user_id)}'"
    records = _select(AIRTABLE_USERS_TABLE, formula)
    return records[0]["fields"] if records else None


def get_subscription_status(user_id):
    """Return the tenant's SubscriptionStatus ("Active" / "Expired").

    Returns None when the tenant has no row in the Users table — the app
    treats anything other than an existing row with "Active" as access denied.
    Raises AirtableError on API failures, including a missing Users table.
    """
    formula = f"{{UserId}}='{_sanitize(user_id)}'"
    records = _select(AIRTABLE_USERS_TABLE, formula)
    if not records:
        return None
    return records[0]["fields"].get("SubscriptionStatus")


# --- Projects -------------------------------------------------------------
def create_project(user_id, name):
    fields = {"Name": name, "UserId": user_id, "Status": "Active"}
    return _request("POST", AIRTABLE_PROJECTS_TABLE, json={"fields": fields, "typecast": True})


def get_active_projects(user_id):
    formula = f"AND({{UserId}}='{_sanitize(user_id)}', {{Status}}='Active')"
    return _select(AIRTABLE_PROJECTS_TABLE, formula)


def get_completed_projects(user_id):
    formula = f"AND({{UserId}}='{_sanitize(user_id)}', {{Status}}='Completed')"
    return _select(AIRTABLE_PROJECTS_TABLE, formula)


def find_active_project(user_id, name):
    """Return the caller's active project whose name matches (case-insensitive), or None."""
    target = (name or "").strip().lower()
    for rec in get_active_projects(user_id):
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
def create_transaction(user_id, project, amount, type_="Expense",
                       description=None, provider=None, date=None, source="Manual",
                       file_hash=None):
    fields = {
        "UserId": user_id,
        "Project": project,
        "Type": type_,
        "Amount": amount,
        "Source": source,
    }
    if description:
        fields["Description"] = description
    if provider:
        fields["Provider"] = provider
    if file_hash:
        fields["FileHash"] = file_hash
    # Airtable's Date column rejects non-ISO strings with a 422, so normalize
    # here — every caller (web app, bot) is covered. An unparseable date is
    # dropped rather than allowed to fail the whole save.
    iso_date = to_iso_date(date)
    if iso_date:
        fields["Date"] = iso_date
    return _request("POST", AIRTABLE_TRANSACTIONS_TABLE, json={"fields": fields, "typecast": True})


def find_transaction_by_hash(user_id, file_hash):
    """Return this tenant's first transaction carrying the given FileHash,
    or None. Scoped to UserId so one tenant's receipt never blocks another's.
    """
    if not file_hash:
        return None
    formula = (f"AND({{UserId}}='{_sanitize(user_id)}', "
               f"{{FileHash}}='{_sanitize(file_hash)}')")
    records = _select(AIRTABLE_TRANSACTIONS_TABLE, formula)
    return records[0] if records else None


def delete_transaction(record_id):
    """Permanently delete a single transaction row."""
    return _delete_records(AIRTABLE_TRANSACTIONS_TABLE, [record_id])


def get_transactions(user_id, project=None):
    """All transactions for this user id, optionally narrowed to one project."""
    formula = f"{{UserId}}='{_sanitize(user_id)}'"
    records = _select(AIRTABLE_TRANSACTIONS_TABLE, formula)
    if project is not None:
        target = project.strip().lower()
        records = [r for r in records
                   if (r["fields"].get("Project") or "").strip().lower() == target]
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
    tenant's UserId + project Name). Returns (projects_deleted,
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

        # Cascade: delete this tenant's transactions for this project name.
        user_id = fields.get("UserId")
        name = fields.get("Name")
        if user_id and name:
            formula = (f"AND({{UserId}}='{_sanitize(user_id)}', "
                       f"{{Project}}='{_sanitize(name)}')")
            transaction_ids += [t["id"] for t in
                                _select(AIRTABLE_TRANSACTIONS_TABLE, formula)]

    # Delete children first so a partial failure never orphans transactions.
    deleted_txns = _delete_records(AIRTABLE_TRANSACTIONS_TABLE, transaction_ids)
    deleted_projects = _delete_records(AIRTABLE_PROJECTS_TABLE, project_ids)
    return deleted_projects, deleted_txns
