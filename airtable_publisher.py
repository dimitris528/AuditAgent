"""
Airtable Publisher — Step 2 of the AI Document Auditor.

Takes the structured invoice JSON produced by extractor.py and creates a new
record in an Airtable table via the Airtable REST API.
"""

import requests

from config import AIRTABLE_PAT, AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME


# Map the extractor's JSON keys -> Airtable column (field) names.
FIELD_MAP = {
    "provider_name": "Provider",
    "date": "Date",
    "total_amount": "Amount",
    "materials_summary": "Materials",
}


def _build_fields(data_json):
    """Translate the extracted JSON into an Airtable 'fields' dict.

    Only keys present in FIELD_MAP are forwarded; anything else is ignored so
    stray keys from the model can't break the request.
    """
    fields = {}
    for json_key, airtable_field in FIELD_MAP.items():
        if json_key in data_json and data_json[json_key] is not None:
            fields[airtable_field] = data_json[json_key]
    return fields


def publish_to_airtable(data_json):
    """Send one extracted invoice (dict) to Airtable as a new record.

    Returns the created record's JSON on success.
    Raises RuntimeError on configuration or API failures.
    """
    # 1) Guard against unconfigured credentials.
    if not AIRTABLE_PAT or AIRTABLE_PAT == "YOUR_PERSONAL_ACCESS_TOKEN":
        raise RuntimeError(
            "Airtable PAT is not configured. Edit config.py and set AIRTABLE_PAT."
        )
    if not AIRTABLE_BASE_ID or AIRTABLE_BASE_ID == "YOUR_BASE_ID":
        raise RuntimeError(
            "Airtable Base ID is not configured. Edit config.py and set AIRTABLE_BASE_ID."
        )

    fields = _build_fields(data_json)
    if not fields:
        raise RuntimeError(
            "No mappable fields found in the extracted JSON; nothing to publish."
        )

    # 2) Build the request. Table name is URL-encoded by requests via params.
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{requests.utils.quote(AIRTABLE_TABLE_NAME)}"
    headers = {
        "Authorization": f"Bearer {AIRTABLE_PAT}",
        "Content-Type": "application/json",
    }
    payload = {"fields": fields, "typecast": True}  # typecast lets Airtable coerce types

    # 3) Send it, with network + HTTP error handling.
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Network error talking to Airtable: {exc}") from exc

    if response.status_code >= 400:
        raise RuntimeError(
            f"Airtable API error {response.status_code}: {response.text}"
        )

    record = response.json()
    print(f"✅ Published to Airtable — record id: {record.get('id', '?')}")
    return record


if __name__ == "__main__":
    # Quick standalone smoke test with a sample record.
    sample = {
        "provider_name": "BuildSupply Materials Ltd.",
        "date": "18 June 2026",
        "total_amount": "£1,204.80",
        "materials_summary": "Cement, sharp sand, concrete blocks, rebar steel.",
    }
    try:
        publish_to_airtable(sample)
    except RuntimeError as err:
        print(f"[ERROR] {err}")
