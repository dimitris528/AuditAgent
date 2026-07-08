"""
Auditor & Notifier Agent — Step 3 of the AI Document Auditor.

Inspects the extracted invoice data for budget-policy violations and raises
highly-visible alerts. The notifier is simulated in the terminal for now,
standing in for a real SMS/push-notification integration later.
"""

import re
import sys

# Make stdout UTF-8 where the platform allows it (Windows consoles default to
# a legacy codepage that can't encode emoji/currency symbols).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# Any invoice total above this (in the document's currency) is flagged.
HIGH_EXPENSE_THRESHOLD = 1000.0


def _safe_print(text):
    """Print text, degrading to an ASCII-safe form if the console can't encode it."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(text.encode(enc, errors="replace").decode(enc))


def _parse_amount(raw_amount):
    """Strip currency symbols/thousands separators and return a float.

    Handles values like "£1,204.80", "1.204,80 €", "$2000", 1500, etc.
    Returns None if no numeric value can be recovered.
    """
    if raw_amount is None:
        return None
    if isinstance(raw_amount, (int, float)):
        return float(raw_amount)

    text = str(raw_amount).strip()

    # Keep only digits, separators and sign; drop currency symbols/letters.
    cleaned = re.sub(r"[^\d,.\-]", "", text)
    if not cleaned:
        return None

    # Normalise decimal/thousands separators. If both appear, the right-most
    # one is the decimal separator (e.g. "1,204.80" or "1.204,80").
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # Lone comma: treat as decimal separator (European style).
        cleaned = cleaned.replace(",", ".")

    try:
        return float(cleaned)
    except ValueError:
        return None


def _send_manager_alert(provider, amount):
    """Simulate an urgent SMS/push alert by printing a loud terminal banner."""
    border = "!" * 64
    _safe_print("\n" + border)
    _safe_print(f"🚨 ALERT FOR MANAGER: High expense detected from provider "
                f"{provider}! Amount: {amount}")
    _safe_print("   (Simulated SMS/push notification)")
    _safe_print(border + "\n")


def check_budget_alerts(data_json):
    """Check the invoice total against the budget threshold.

    Returns True if a high-expense alert was triggered, else False.
    """
    raw_amount = data_json.get("total_amount")
    provider = data_json.get("provider_name") or "Unknown provider"

    amount = _parse_amount(raw_amount)
    if amount is None:
        print(f"[AUDITOR] Could not parse total_amount ({raw_amount!r}); "
              "skipping budget check.")
        return False

    if amount > HIGH_EXPENSE_THRESHOLD:
        # Echo the original (formatted) amount in the alert for readability.
        _send_manager_alert(provider, raw_amount)
        return True

    print(f"[AUDITOR] OK — {provider} total {raw_amount} is within the "
          f"{HIGH_EXPENSE_THRESHOLD:.0f} threshold.")
    return False


if __name__ == "__main__":
    # Standalone smoke test: one over-threshold, one under.
    check_budget_alerts({
        "provider_name": "BuildSupply Materials Ltd.",
        "total_amount": "£1,204.80",
    })
    check_budget_alerts({
        "provider_name": "Corner Hardware",
        "total_amount": "£420.00",
    })
