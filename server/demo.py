"""In-memory demo dataset.

Served by the API when Airtable isn't configured yet (placeholder PAT) so the
Next.js dashboard renders real-looking numbers on the very first run. The
shapes match Airtable records exactly, so build_dashboard treats them
identically to live data. Flip DASHBOARD_DEMO=0 to disable the fallback.
"""

from datetime import date, timedelta

import finance


def _today():
    return date.today()


def _iso(d):
    return d.isoformat()


def _txn(rec_id, cat, amount, type_, vat_rate=None, days_ago=0, desc=None):
    """Build one demo transaction record, deriving/storing VAT exactly like the
    app's write path so the numbers reconcile."""
    fields = {
        "Username": "demo",
        "Category": cat,
        "Amount": amount,
        "Type": type_,
        "Date": _iso(_today() - timedelta(days=days_ago)),
    }
    if desc:
        fields["Description"] = desc
    if type_ != finance.DEBT_TYPE and vat_rate is not None:
        fields["VAT_Amount"] = finance.vat_for_write(
            amount, type_ == "Έσοδο", vat_rate)
        fields["VAT_Rate"] = vat_rate
    elif vat_rate is not None:
        fields["VAT_Rate"] = vat_rate
    return {"id": rec_id, "fields": fields,
            "createdTime": _iso(_today()) + "T09:00:00.000Z"}


def demo_active_projects():
    return [
        {"id": "recDemoA", "fields": {"Name": "Παπαδόπουλος Α.Ε.",
                                      "Username": "demo", "Status": "Active"}},
        {"id": "recDemoB", "fields": {"Name": "Γεωργίου & Σία Ο.Ε.",
                                      "Username": "demo", "Status": "Active"}},
        {"id": "recDemoC", "fields": {"Name": "Δημητρίου Consulting",
                                      "Username": "demo", "Status": "Active"}},
        {"id": "recDemoD", "fields": {"Name": " Νησίδα Café",
                                      "Username": "demo", "Status": "Active"}},
    ]


def demo_completed_projects():
    return [
        {"id": "recDemoZ", "fields": {"Name": "Παλαιός Πελάτης",
                                      "Username": "demo", "Status": "Completed",
                                      "ClosedDate": _iso(_today() - timedelta(days=40))}},
    ]


def demo_transactions():
    t = []
    # Παπαδόπουλος Α.Ε. — high revenue: net profit ≈ 11.000 €, OVER the tax
    # scale limit (shows the red 29 % bracket bar).
    t += [
        _txn("t1", "Παπαδόπουλος Α.Ε.", 18600.0, "Έσοδο", 0.24, 2, "Τιμολόγιο έργου"),
        _txn("t2", "Παπαδόπουλος Α.Ε.", -3720.0, "Έξοδο", 0.24, 1, "Υλικά"),
        _txn("t3", "Παπαδόπουλος Α.Ε.", -1130.0, "Έξοδο", 0.13, 8, "Μεταφορικά"),
        _txn("t4", "Παπαδόπουλος Α.Ε.", 2000.0, finance.DEBT_TYPE, 0.24, 5, "Εκκρεμές τιμολόγιο"),
    ]
    # Γεωργίου & Σία Ο.Ε. — mid, within low bracket.
    t += [
        _txn("t5", "Γεωργίου & Σία Ο.Ε.", 7440.0, "Έσοδο", 0.24, 30, "Πωλήσεις"),
        _txn("t6", "Γεωργίου & Σία Ο.Ε.", -2260.0, "Έξοδο", 0.13, 12, "Προμήθειες"),
        _txn("t7", "Γεωργίου & Σία Ο.Ε.", 500.0, finance.DEBT_TYPE, 0.13, 3),
    ]
    # Δημητρίου Consulting — services, VAT credit-ish.
    t += [
        _txn("t8", "Δημητρίου Consulting", 6200.0, "Έσοδο", 0.24, 45, "Συμβουλευτική"),
        _txn("t9", "Δημητρίου Consulting", -5000.0, "Έξοδο", 0.24, 22, "Υπεργολαβία"),
    ]
    # Νησίδα Café — small, mixed reduced rates.
    t += [
        _txn("t10", " Νησίδα Café", 3180.0, "Έσοδο", 0.13, 10, "Ταμείο"),
        _txn("t11", "Νησίδα Café", 636.0, "Έσοδο", 0.06, 60, "Εκδηλώσεις"),
        _txn("t12", "Νησίδα Café", -1240.0, "Έξοδο", 0.24, 18, "Εξοπλισμός"),
    ]
    # A resolved historical revenue for the archived client (shows in trend).
    t += [_txn("t13", "Παλαιός Πελάτης", 4960.0, "Έσοδο", 0.24, 120)]
    return t
