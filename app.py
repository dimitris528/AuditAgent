"""
AuditAgent.ai — Streamlit PWA front-end (premium dark theme, Greek UI).

Presentation layer only differs from the classic build; the multi-tenant,
cached Airtable logic and the data-retention hooks are unchanged:
    1. Multi-tenant login (UserId + 4-digit PIN, both checked against the
       Users table in ONE Airtable round-trip). The subscription verdict is
       cached in st.session_state and re-verified only on "Ανανέωση".
    2. Executive dashboard: revenue / expenses / net profit KPI cards, plus
       an "Αρχειοθέτηση Κατηγορίας" action that archives a category. The UI
       speaks generic business Greek ("Κατηγορίες") for retail merchants and
       shop owners; the Airtable schema underneath (Projects/Status/Name
       columns) is unchanged.
    3. Single-tap document upload (camera photo or PDF) -> GPT-4o extraction
       -> confirmation screen -> saved to Airtable. If extraction fails for
       ANY reason the form degrades to blank manual entry instead of blocking.
       Each upload's SHA-256 lands in the Transactions FileHash column and is
       re-checked (per tenant) before every save to reject duplicate receipts.
    4. Date-range recap of archived categories, plus the period's individual
       transactions with a confirm-then-delete control on each row.
    5. The 2-year data-retention cleanup runs automatically in the background.

Theme: #121214 canvas, #1C1C1E surfaces, #2C2C2E borders, #00E676 accent,
#FFFFFF / #8E8E93 text, Inter with system-sans fallback. All accent-on-surface
pairs were contrast-checked (mint 10.2:1, amber 7.8:1, red 6.1:1, gray 5.2:1).

Run with:   streamlit run app.py
"""

import difflib
import hashlib
import html
import os
import tempfile
from datetime import date

import streamlit as st

import airtable_client as db
from airtable_client import _subtract_years
from auditor import HIGH_EXPENSE_THRESHOLD, _parse_amount
from extractor import extract_invoice_data

RETENTION_YEARS = 2
UPLOAD_TYPES = ["pdf", "jpg", "jpeg", "png", "webp"]

st.set_page_config(
    page_title="AuditAgent.ai",
    page_icon="🧾",
    layout="centered",  # single column reads well on phones
)

# --------------------------------------------------------------------------
# Design system (CSS injection)
# --------------------------------------------------------------------------
_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, .stApp, .stApp * {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI',
                 Roboto, 'Helvetica Neue', Arial, sans-serif;
}
/* Material icon glyphs are ligatures — they must keep their icon font or they
   render as raw words ("keyboard_arrow_right"). Re-assert after the override. */
[data-testid="stIconMaterial"], [class*="material-symbols"], [class*="material-icons"] {
    font-family: 'Material Symbols Rounded', 'Material Symbols Outlined',
                 'Material Icons' !important;
}
.stApp { background: #121214; }

/* Chrome: hide menu/footer/toolbar, keep the header bar (mobile sidebar toggle) */
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] { display: none; }
header[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 2.2rem; max-width: 46rem; }

h1, h2, h3, h4, [data-testid="stMarkdownContainer"] h4 {
    color: #FFFFFF; letter-spacing: -0.02em;
}
.aud-section {
    color: #8E8E93; font-size: .78rem; font-weight: 600; letter-spacing: .08em;
    text-transform: uppercase; margin: 18px 0 10px;
}

/* --- Brand ------------------------------------------------------------- */
.aud-brand {
    text-align: center; font-size: 2.1rem; font-weight: 800; color: #FFFFFF;
    letter-spacing: -0.03em; margin: 10vh 0 4px;
}
.aud-brand span { color: #00E676; }
.aud-tagline { text-align: center; color: #8E8E93; font-size: .95rem; margin-bottom: 26px; }
.aud-brand-small {
    font-size: 1.1rem; font-weight: 800; color: #FFFFFF; letter-spacing: -0.02em;
}
.aud-brand-small span { color: #00E676; }
.aud-user { color: #8E8E93; font-size: .82rem; margin: 2px 0 14px; word-break: break-all; }

/* --- KPI cards ----------------------------------------------------------- */
.aud-kpi-row { display: flex; gap: 12px; flex-wrap: wrap; margin: 2px 0 14px; }
.aud-kpi {
    flex: 1 1 150px; background: #1C1C1E; border: 1px solid #2C2C2E;
    border-radius: 16px; padding: 18px 20px;
    transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease;
}
.aud-kpi:hover {
    transform: translateY(-2px); border-color: #3A3A3C;
    box-shadow: 0 8px 24px rgba(0, 0, 0, .35);
}
.aud-kpi-label { color: #8E8E93; font-size: .8rem; font-weight: 500; margin-bottom: 6px; }
.aud-kpi-value {
    color: #F5F5F7; font-size: clamp(1.1rem, 4.5vw, 1.5rem); font-weight: 600;
    line-height: 1.15; white-space: nowrap;
}
.aud-kpi.warm  .aud-kpi-value { color: #E6A23C; }
.aud-kpi.loss  .aud-kpi-value { color: #FF6B57; }
.aud-kpi.accent { border-color: rgba(0, 230, 118, .35); }
.aud-kpi.accent:hover {
    border-color: rgba(0, 230, 118, .7);
    box-shadow: 0 8px 24px rgba(0, 230, 118, .12);
}
.aud-kpi.accent .aud-kpi-value {
    color: #00E676; font-size: clamp(1.3rem, 5.5vw, 1.75rem); font-weight: 700;
}

/* --- Project cards -------------------------------------------------------- */
.aud-proj {
    background: #1C1C1E; border: 1px solid #2C2C2E; border-radius: 16px;
    padding: 16px 20px; margin-bottom: 12px; transition: border-color .18s ease;
}
.aud-proj:hover { border-color: #3A3A3C; }
.aud-proj-name { color: #FFFFFF; font-weight: 600; font-size: 1.02rem; margin-bottom: 10px; }
.aud-proj-stats { display: flex; gap: 26px; flex-wrap: wrap; }
.aud-proj-value { color: #F5F5F7; font-size: .98rem; font-weight: 600; }
.aud-proj-value.warm { color: #E6A23C; }
.aud-proj-value.accent { color: #00E676; }
.aud-proj-value.loss { color: #FF6B57; }

/* --- Close-project control ------------------------------------------------ */
div[class*="st-key-close_"] { margin: -4px 0 14px; }
div[class*="st-key-close_"] button {
    background: transparent; color: #8E8E93; border: 1px dashed #3A3A3C;
    font-size: .85rem; font-weight: 500; padding: .38rem .9rem;
}
div[class*="st-key-close_"] button:hover {
    color: #FF6B57; border-color: rgba(255, 107, 87, .55); background: transparent;
}

/* --- Transaction rows (recap) + ghost delete button ------------------------- */
.aud-txn-row {
    display: flex; justify-content: space-between; align-items: baseline;
    gap: 12px; flex-wrap: wrap; padding: 9px 2px;
    border-bottom: 1px solid #2C2C2E; color: #F5F5F7; font-size: .92rem;
}
.aud-txn-meta { color: #F5F5F7; }
div[class*="st-key-del_"] button {
    background: transparent; color: #8E8E93; border: 1px dashed #3A3A3C;
    font-size: .8rem; font-weight: 500; padding: .3rem .7rem;
}
div[class*="st-key-del_"] button:hover {
    color: #FF6B57; border-color: rgba(255, 107, 87, .55); background: transparent;
}

/* --- Closed projects list --------------------------------------------------- */
.aud-closed-row {
    display: flex; justify-content: space-between; align-items: baseline;
    gap: 12px; flex-wrap: wrap; padding: 10px 2px;
    border-bottom: 1px solid #2C2C2E; color: #F5F5F7; font-size: .95rem;
}
.aud-closed-row:last-child { border-bottom: none; }
.aud-closed-name { font-weight: 600; }
.aud-closed-meta { color: #8E8E93; font-size: .85rem; }

/* --- Empty state ----------------------------------------------------------- */
.aud-empty {
    border: 1.5px dashed #2C2C2E; border-radius: 16px; padding: 30px 22px;
    text-align: center; color: #8E8E93; font-size: .95rem; margin-bottom: 14px;
}

/* --- Subscription lock ------------------------------------------------------ */
.aud-lock {
    background: #1C1C1E; border: 1px solid rgba(255, 107, 87, .35);
    border-radius: 20px; padding: 44px 30px; text-align: center;
    max-width: 430px; margin: 14vh auto 22px;
}
.aud-lock-icon {
    width: 58px; height: 58px; border-radius: 50%;
    background: rgba(255, 107, 87, .12); color: #FF6B57;
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 18px; font-size: 1.5rem;
}
.aud-lock-title { color: #FFFFFF; font-weight: 700; font-size: 1.12rem; margin-bottom: 8px; }
.aud-lock-text { color: #8E8E93; font-size: .95rem; line-height: 1.6; }

/* --- Buttons ------------------------------------------------------------- */
.stButton > button, [data-testid="stFormSubmitButton"] > button {
    border-radius: 12px; font-weight: 600; padding: .62rem 1rem;
    transition: all .18s ease;
}
button[kind="secondary"], button[data-testid="stBaseButton-secondaryFormSubmit"] {
    background: #1C1C1E; color: #F5F5F7; border: 1px solid #2C2C2E;
}
button[kind="secondary"]:hover, button[data-testid="stBaseButton-secondaryFormSubmit"]:hover {
    border-color: #8E8E93; color: #FFFFFF;
}
button[kind="primary"], button[data-testid="stBaseButton-primaryFormSubmit"] {
    background: #00E676 !important; border: none !important;
}
button[kind="primary"], button[data-testid="stBaseButton-primaryFormSubmit"],
button[kind="primary"] *, button[data-testid="stBaseButton-primaryFormSubmit"] * {
    color: #121214 !important;
}
button[kind="primary"]:hover, button[data-testid="stBaseButton-primaryFormSubmit"]:hover {
    filter: brightness(1.06);
    box-shadow: 0 0 0 1px rgba(0, 230, 118, .45), 0 6px 22px rgba(0, 230, 118, .28);
}
button[kind="primary"]:active { transform: scale(.985); }

/* --- Inputs ------------------------------------------------------------- */
div[data-baseweb="input"], div[data-baseweb="textarea"], div[data-baseweb="select"] > div {
    background: #1C1C1E !important; border-color: #2C2C2E !important;
    border-radius: 12px !important;
}
div[data-baseweb="input"]:focus-within, div[data-baseweb="textarea"]:focus-within,
div[data-baseweb="select"] > div:focus-within {
    border-color: #00E676 !important;
}

/* --- Forms / expanders as cards ------------------------------------------- */
[data-testid="stForm"] {
    background: #1C1C1E; border: 1px solid #2C2C2E; border-radius: 16px;
    padding: 22px 22px 16px;
}
[data-testid="stExpander"] {
    background: #1C1C1E; border: 1px solid #2C2C2E; border-radius: 16px;
}
[data-testid="stExpander"] summary { color: #F5F5F7; }

/* --- Upload zone --------------------------------------------------------- */
[data-testid="stFileUploaderDropzone"] {
    background: #1C1C1E; border: 1.5px dashed #3A3A3C; border-radius: 16px;
    padding: 30px 22px;
    transition: border-color .18s ease, box-shadow .18s ease;
}
[data-testid="stFileUploaderDropzone"]:hover {
    border-color: #00E676;
    box-shadow: 0 0 0 1px rgba(0, 230, 118, .2), 0 8px 28px rgba(0, 230, 118, .08);
}
[data-testid="stFileUploaderDropzone"] button {
    background: transparent; color: #00E676; border: 1px solid rgba(0, 230, 118, .45);
    border-radius: 10px; font-weight: 600;
}
/* Streamlit's dropzone strings ("Drag and drop file here", "Browse files")
   are hard-coded in English — swap them for Greek copy via CSS content.
   Every native child is hidden (not just spans/smalls): newer Streamlit
   builds nest a Material icon ligature inside the browse button, and it
   used to leak through the font-size:0 trick as the literal word "Upload"
   glued to the Greek label. */
[data-testid="stFileUploaderDropzoneInstructions"] > * { display: none !important; }
[data-testid="stFileUploaderDropzoneInstructions"]::before {
    content: 'Σύρετε το αρχείο εδώ'; display: block;
    color: #F5F5F7; font-weight: 600; font-size: .95rem;
}
[data-testid="stFileUploaderDropzoneInstructions"]::after {
    content: 'PDF ή φωτογραφία (JPG, PNG)'; display: block;
    color: #8E8E93; font-size: .8rem; margin-top: 3px;
}
[data-testid="stFileUploaderDropzone"] button { font-size: 0; line-height: 0; }
[data-testid="stFileUploaderDropzone"] button * { display: none !important; }
[data-testid="stFileUploaderDropzone"] button::after {
    content: 'Επιλογή αρχείου'; font-size: .9rem; line-height: 1.2;
}
.aud-upload-hint { color: #8E8E93; font-size: .95rem; line-height: 1.55; margin: 2px 0 12px; }

/* --- Spinner (minimal, on-accent) ----------------------------------------- */
[data-testid="stSpinner"] { color: #8E8E93; }
[data-testid="stSpinner"] i {
    border-color: #00E676 rgba(0, 230, 118, .15) rgba(0, 230, 118, .15) !important;
}

/* --- Tabs ---------------------------------------------------------------- */
.stTabs [data-baseweb="tab-list"] { gap: 4px; }
.stTabs button[data-baseweb="tab"] {
    color: #8E8E93; font-weight: 500; background: transparent;
}
.stTabs button[data-baseweb="tab"]:hover { color: #F5F5F7; }
.stTabs button[data-baseweb="tab"][aria-selected="true"] { color: #FFFFFF; font-weight: 600; }
.stTabs [data-baseweb="tab-highlight"] { background-color: #00E676; }
.stTabs [data-baseweb="tab-border"] { background-color: #2C2C2E; }

/* --- Sidebar ------------------------------------------------------------- */
[data-testid="stSidebar"] { background: #161618; border-right: 1px solid #2C2C2E; }

/* --- Alerts ------------------------------------------------------------- */
[data-testid="stAlert"] { border-radius: 12px; }
</style>
"""


def _inject_css():
    st.markdown(_CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Background data-retention hook
# --------------------------------------------------------------------------
@st.cache_resource(ttl=24 * 3600, show_spinner=False)
def _run_retention_cleanup():
    """Enforce the retention policy at most once per day per server process.

    Failures are swallowed so a cleanup hiccup never blocks the UI.
    """
    try:
        return db.cleanup_old_closed_projects(retention_years=RETENTION_YEARS)
    except db.AirtableError as exc:
        print(f"[WARN] Data-retention cleanup skipped: {exc}")
        return None


# --------------------------------------------------------------------------
# Cached tenant-scoped reads (cleared after every write)
# --------------------------------------------------------------------------
@st.cache_data(ttl=120, show_spinner=False)
def _load_active_projects(tenant_id):
    return db.get_active_projects(tenant_id)


@st.cache_data(ttl=120, show_spinner=False)
def _load_completed_projects(tenant_id):
    return db.get_completed_projects(tenant_id)


@st.cache_data(ttl=120, show_spinner=False)
def _load_transactions(tenant_id):
    return db.get_transactions(tenant_id)


def _invalidate_caches():
    _load_active_projects.clear()
    _load_completed_projects.clear()
    _load_transactions.clear()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _money(value):
    # Non-breaking space keeps the amount and € glued on narrow screens.
    return f"{float(value or 0):,.2f} €"


def _sum_by_type(records):
    """Return (expense_total, revenue_total) for a list of transaction records."""
    expense = revenue = 0.0
    for rec in records:
        fields = rec["fields"]
        amount = float(fields.get("Amount") or 0)
        if (fields.get("Type") or "Expense") == "Revenue":
            revenue += amount
        else:
            expense += amount
    return expense, revenue


def _transactions_by_project(transactions):
    grouped = {}
    for txn in transactions:
        key = (txn["fields"].get("Project") or "").strip().lower()
        grouped.setdefault(key, []).append(txn)
    return grouped


def _project_financials(name, grouped):
    exp, rev = _sum_by_type(grouped.get((name or "").strip().lower(), []))
    return rev, exp, rev - exp


def _closed_date(record):
    raw = (record["fields"].get("ClosedDate") or "")[:10]
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _txn_date(record):
    """A transaction's effective date: the Date field, else createdTime."""
    raw = (record["fields"].get("Date") or record.get("createdTime") or "")[:10]
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _extract_uploaded_invoice(uploaded):
    """Persist the upload to a temp file and run the GPT-4o extraction on it."""
    suffix = os.path.splitext(uploaded.name)[1].lower() or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded.getbuffer())
        tmp_path = tmp.name
    try:
        return extract_invoice_data(tmp_path)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# --------------------------------------------------------------------------
# HTML component helpers (all dynamic text is escaped)
# --------------------------------------------------------------------------
def _section(label):
    st.markdown(f'<div class="aud-section">{html.escape(label)}</div>',
                unsafe_allow_html=True)


def _kpi_row(items):
    """Render KPI cards: items = [(label, value, kind)], kind in
    {"", "warm", "accent", "loss"}."""
    cards = "".join(
        f'<div class="aud-kpi {kind}">'
        f'<div class="aud-kpi-label">{html.escape(label)}</div>'
        f'<div class="aud-kpi-value">{html.escape(value)}</div>'
        f"</div>"
        for label, value, kind in items
    )
    st.markdown(f'<div class="aud-kpi-row">{cards}</div>', unsafe_allow_html=True)


def _project_card(name, rev, exp, net):
    stats = "".join(
        f'<div><div class="aud-kpi-label">{html.escape(label)}</div>'
        f'<div class="aud-proj-value {kind}">{html.escape(value)}</div></div>'
        for label, value, kind in [
            ("Έσοδα", _money(rev), ""),
            ("Έξοδα", _money(exp), "warm"),
            ("Καθαρό", _money(net), "accent" if net >= 0 else "loss"),
        ]
    )
    st.markdown(
        f'<div class="aud-proj">'
        f'<div class="aud-proj-name">{html.escape(name)}</div>'
        f'<div class="aud-proj-stats">{stats}</div></div>',
        unsafe_allow_html=True,
    )


def _empty_state(text):
    st.markdown(f'<div class="aud-empty">{html.escape(text)}</div>',
                unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Login (tenant context + PIN check)
# --------------------------------------------------------------------------
def _pin_matches(stored, typed):
    """Compare the Users-table PIN column against the typed PIN.

    The column may be Text or Number in Airtable; a numeric cell comes back
    as int/float, so both sides are normalized to a digit string. An empty
    or missing stored PIN never matches (fail closed).
    """
    if stored is None:
        return False
    if isinstance(stored, float) and stored.is_integer():
        stored = int(stored)
    stored = str(stored).strip()
    return bool(stored) and stored == typed.strip()


def login_screen():
    st.markdown('<div class="aud-brand">AuditAgent<span>.ai</span></div>',
                unsafe_allow_html=True)
    st.markdown('<div class="aud-tagline">Οικονομικός έλεγχος για τη '
                "σύγχρονη επιχείρηση</div>", unsafe_allow_html=True)

    _, mid, _ = st.columns([1, 1.7, 1])
    with mid:
        with st.form("login"):
            tenant = st.text_input(
                "UserId (Τηλέφωνο)",
                placeholder="+30 69X XXX XXXX",
                help="Αυτό είναι το κλειδί του λογαριασμού σας — το ίδιο "
                     "UserId που είναι αποθηκευμένο με τα δεδομένα σας στο Airtable.",
            )
            pin = st.text_input(
                "4ψήφιο PIN",
                type="password",
                max_chars=4,
                placeholder="••••",
            )
            submitted = st.form_submit_button("Σύνδεση", type="primary",
                                              width="stretch")
    if submitted:
        tenant = tenant.strip()
        pin = pin.strip()
        with mid:
            if not tenant or not pin:
                st.error("Παρακαλώ συμπληρώστε το UserId και το 4ψήφιο PIN σας.")
                return
            try:
                record = db.get_user_record(tenant)
            except db.AirtableError as exc:
                print(f"[WARN] Login lookup failed for {tenant!r}: {exc}")
                st.error("Ο έλεγχος του λογαριασμού σας απέτυχε προσωρινά. "
                         "Παρακαλώ δοκιμάστε ξανά σε λίγο.")
                return
            if record is None or not _pin_matches(record.get("PIN"), pin):
                st.error("❌ Το UserId ή το PIN είναι εσφαλμένο.")
                return
        # Credentials verified — reuse the same Users row for the
        # subscription verdict so login stays a single Airtable call.
        st.session_state["tenant_id"] = tenant
        st.session_state["subscription_verified"] = True
        st.session_state["subscription_status"] = record.get("SubscriptionStatus")
        st.rerun()


def logout():
    for key in ("tenant_id", "pending_invoice", "processed_upload",
                "extraction_error", "subscription_verified",
                "subscription_status", "confirm_close", "flash",
                "confirm_delete_txn", "flash_recap"):
        st.session_state.pop(key, None)
    st.rerun()


def _lock_screen(text):
    st.markdown(
        '<div class="aud-lock">'
        '<div class="aud-lock-icon">🔒</div>'
        '<div class="aud-lock-title">AuditAgent.ai</div>'
        f'<div class="aud-lock-text">{html.escape(text)}</div></div>',
        unsafe_allow_html=True,
    )
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        if st.button("Αποσύνδεση", width="stretch"):
            logout()


def subscription_gate(tenant_id):
    """Return True only for a UserId with a Users row whose SubscriptionStatus
    is "Active"; render the appropriate block screen otherwise.

    Performance: the Users-table lookup runs ONCE per browser session and the
    result is cached in st.session_state, so widget clicks and reruns never
    re-query Airtable. The sidebar "Ανανέωση" button (and a fresh login)
    clears the cached verdict and forces a re-check.

    Strict allow-list: unknown UserIds, blank/other statuses, and Users-table
    lookup failures are all denied. Failing closed on lookup errors matters —
    failing open would let anyone in whenever the Users table is unreachable.
    Lookup FAILURES are never cached, so a transient outage doesn't lock the
    user out for the rest of the session.
    """
    if not st.session_state.get("subscription_verified"):
        try:
            status = db.get_subscription_status(tenant_id)
        except db.AirtableError as exc:
            print(f"[WARN] Subscription check failed for {tenant_id!r}: {exc}")
            _lock_screen("Ο έλεγχος του λογαριασμού σας απέτυχε προσωρινά. "
                         "Παρακαλώ δοκιμάστε ξανά σε λίγο.")
            return False
        st.session_state["subscription_verified"] = True
        st.session_state["subscription_status"] = status

    status = (st.session_state.get("subscription_status") or "").strip().lower()
    if status == "active":
        return True

    if status == "expired":
        _lock_screen("Η συνδρομή σας έχει λήξει. Παρακαλώ επικοινωνήστε με "
                     "τον διαχειριστή για ανανέωση.")
    else:
        # No Users row, or a blank/unrecognized status.
        _lock_screen("Το UserId δεν βρέθηκε ή δεν είναι ενεργό. Παρακαλώ "
                     "επικοινωνήστε με τον διαχειριστή.")
    return False


# --------------------------------------------------------------------------
# Tab 1 — Executive dashboard
# --------------------------------------------------------------------------
def _close_project_control(proj, name):
    """Render the 🔒 archive action for one active category.

    First tap arms an inline confirmation (stored in session state); the
    second tap flips the row's Status and stamps ClosedDate = today.
    """
    record_id = proj["id"]
    if st.session_state.get("confirm_close") == record_id:
        st.warning(f"Να αρχειοθετηθεί η κατηγορία «{name}»; Θα μεταφερθεί "
                   "στις Αρχειοθετημένες Κατηγορίες.")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("✅ Ναι, αρχειοθέτηση", key=f"yes_{record_id}",
                         type="primary", width="stretch"):
                try:
                    db.close_project(record_id, date.today())
                except db.AirtableError as exc:
                    st.error(f"Δεν ήταν δυνατή η αρχειοθέτηση της κατηγορίας: {exc}")
                else:
                    st.session_state.pop("confirm_close", None)
                    st.session_state["flash"] = (
                        f"Η κατηγορία «{name}» αρχειοθετήθηκε επιτυχώς και "
                        "μεταφέρθηκε στις Αρχειοθετημένες Κατηγορίες."
                    )
                    _invalidate_caches()
                    st.rerun()
        with col_no:
            if st.button("✕ Άκυρο", key=f"no_{record_id}", width="stretch"):
                st.session_state.pop("confirm_close", None)
                st.rerun()
    elif st.button("🔒 Αρχειοθέτηση Κατηγορίας", key=f"close_{record_id}"):
        st.session_state["confirm_close"] = record_id
        st.rerun()


def _closed_projects_section(completed, grouped):
    """Collapsed archive so archived categories never clutter the daily flow."""
    with st.expander(f"🔒 Αρχειοθετημένες Κατηγορίες ({len(completed)})"):
        rows = "".join(
            f'<div class="aud-closed-row">'
            f'<span class="aud-closed-name">{html.escape(p["fields"].get("Name") or "—")}</span>'
            f'<span class="aud-closed-meta">'
            f'αρχειοθετήθηκε {d.strftime("%d/%m/%Y") if (d := _closed_date(p)) else "—"}'
            f' · Καθαρό {html.escape(_money(_project_financials(p["fields"].get("Name") or "—", grouped)[2]))}'
            f"</span></div>"
            for p in sorted(completed, key=lambda p: _closed_date(p) or date.min,
                            reverse=True)
        )
        st.markdown(rows, unsafe_allow_html=True)


def dashboard_tab(tenant_id):
    flash = st.session_state.pop("flash", None)
    if flash:
        st.success(flash)

    try:
        active = _load_active_projects(tenant_id)
        completed = _load_completed_projects(tenant_id)
        grouped = _transactions_by_project(_load_transactions(tenant_id))
    except db.AirtableError as exc:
        st.error(f"Δεν ήταν δυνατή η φόρτωση των δεδομένων σας: {exc}")
        return

    if not active:
        _section("Επισκόπηση")
        _empty_state("Δεν υπάρχουν ενεργές κατηγορίες ακόμη — δημιουργήστε "
                     "μία παρακάτω για να ξεκινήσετε την καταχώρηση "
                     "παραστατικών.")
    else:
        totals = [_project_financials(p["fields"].get("Name") or "—", grouped)
                  for p in active]
        total_rev = sum(t[0] for t in totals)
        total_exp = sum(t[1] for t in totals)
        net = total_rev - total_exp

        _section("Επισκόπηση")
        _kpi_row([
            ("Συνολικά έσοδα", _money(total_rev), ""),
            ("Συνολικά έξοδα", _money(total_exp), "warm"),
            ("Καθαρό κέρδος", _money(net), "accent" if net >= 0 else "loss"),
        ])

        _section("Ενεργές κατηγορίες")
        for proj, (rev, exp, pnet) in zip(active, totals):
            name = proj["fields"].get("Name") or "—"
            _project_card(name, rev, exp, pnet)
            _close_project_control(proj, name)

    with st.expander("➕ Νέα Κατηγορία/Φάκελος"):
        with st.form("new_project", clear_on_submit=True):
            name = st.text_input("Όνομα κατηγορίας")
            if st.form_submit_button("Δημιουργία κατηγορίας", type="primary",
                                     width="stretch"):
                name = name.strip()
                if not name:
                    st.error("Παρακαλώ εισάγετε ένα όνομα κατηγορίας.")
                elif db.find_active_project(tenant_id, name):
                    st.error(f"Υπάρχει ήδη ενεργή κατηγορία με το όνομα «{name}».")
                else:
                    try:
                        db.create_project(tenant_id, name)
                    except db.AirtableError as exc:
                        st.error("Δεν ήταν δυνατή η δημιουργία της "
                                 f"κατηγορίας: {exc}")
                    else:
                        _invalidate_caches()
                        st.rerun()

    if completed:
        _closed_projects_section(completed, grouped)


# --------------------------------------------------------------------------
# Tab 2 — Invoice upload + confirmation
# --------------------------------------------------------------------------
def upload_tab(tenant_id):
    _section("📄 ΝΕΟ ΠΑΡΑΣΤΑΤΙΚΟ")
    st.markdown(
        '<div class="aud-upload-hint">📸 Τραβήξτε φωτογραφία με την κάμερα '
        "ή επιλέξτε αρχείο (PDF, JPG, PNG)</div>",
        unsafe_allow_html=True,
    )

    uploaded = st.file_uploader(
        "Φωτογραφία παραστατικού ή PDF",
        type=UPLOAD_TYPES,
        key="invoice_upload",
        label_visibility="collapsed",
    )

    # Extract once per distinct upload (keyed by content hash); reruns and
    # failed attempts must never re-bill the Vision API on their own.
    if uploaded is not None:
        fingerprint = hashlib.sha256(uploaded.getbuffer()).hexdigest()
        if st.session_state.get("processed_upload") != fingerprint:
            st.session_state["processed_upload"] = fingerprint
            st.session_state["pending_invoice"] = None
            st.session_state["extraction_error"] = None
            with st.spinner("Ανάγνωση του παραστατικού με GPT-4o Vision…"):
                # Graceful degradation: ANY extraction failure (API down,
                # throttled, unreadable image) falls back to a blank manual
                # form — the upload flow must never dead-end on the AI step.
                try:
                    st.session_state["pending_invoice"] = _extract_uploaded_invoice(uploaded)
                except Exception as err:  # noqa: BLE001
                    print(f"[WARN] Vision extraction failed: {err}")
                    st.session_state["extraction_error"] = str(err)
                    st.session_state["pending_invoice"] = {}

    if st.session_state.get("extraction_error"):
        st.warning("Δεν ήταν δυνατή η αυτόματη ανάγνωση. Παρακαλώ συμπληρώστε "
                   "τα στοιχεία χειροκίνητα παρακάτω.")

    # None = no upload yet; {} = extraction failed -> blank manual entry.
    pending = st.session_state.get("pending_invoice")
    if pending is None:
        _empty_state("Τα στοιχεία που θα εντοπιστούν θα εμφανιστούν εδώ για "
                     "επιβεβαίωση πριν αποθηκευτεί οτιδήποτε.")
        return

    try:
        active = _load_active_projects(tenant_id)
    except db.AirtableError as exc:
        st.error(f"Δεν ήταν δυνατή η φόρτωση των κατηγοριών σας: {exc}")
        return
    if not active:
        st.warning("Χρειάζεστε μια ενεργή κατηγορία πριν αποθηκεύσετε "
                   "παραστατικό — δημιουργήστε μία στην καρτέλα "
                   "«Πίνακας Ελέγχου».")
        return

    _section("Επιβεβαίωση των στοιχείων")
    project_names = [p["fields"].get("Name") or "—" for p in active]

    # Fuzzy-match the Vision-detected category name to preselect the dropdown.
    default_index = 0
    detected_project = str(pending.get("project_name") or "").strip().lower()
    if detected_project and detected_project not in {"null", "none", "n/a", "na", "-"}:
        lowered = [n.strip().lower() for n in project_names]
        closest = difflib.get_close_matches(detected_project, lowered, n=1, cutoff=0.6)
        if closest:
            default_index = lowered.index(closest[0])

    with st.form("confirm_invoice"):
        project = st.selectbox("Κατηγορία", project_names, index=default_index)
        provider = st.text_input("Προμηθευτής", value=pending.get("provider_name") or "")
        # No min_value: credit notes legitimately carry negative totals.
        amount = st.number_input(
            "Συνολικό ποσό (€)",
            step=0.01,
            value=float(_parse_amount(pending.get("total_amount")) or 0.0),
        )
        # A real date picker: the user can't submit a malformed date, and the
        # extracted EU-format string ("26/06/2026") is parsed to prefill it.
        detected_iso = db.to_iso_date(pending.get("date"))
        inv_date = st.date_input(
            "Ημερομηνία παραστατικού",
            value=date.fromisoformat(detected_iso) if detected_iso else date.today(),
            format="DD/MM/YYYY",
        )
        description = st.text_area("Υλικά / περιγραφή",
                                   value=pending.get("materials_summary") or "")
        save = st.form_submit_button("✅ Επιβεβαίωση & Αποθήκευση στην Κατηγορία",
                                     type="primary", width="stretch")
        discard = st.form_submit_button("🗑 Απόρριψη", width="stretch")

    if discard:
        st.session_state.pop("pending_invoice", None)
        st.session_state.pop("extraction_error", None)
        st.rerun()

    if save:
        # Duplicate-receipt guard: the SHA-256 fingerprint computed at upload
        # time is checked against this tenant's FileHash column before the
        # save, and stored with the new row so future re-uploads are caught.
        file_hash = st.session_state.get("processed_upload")
        try:
            if file_hash and db.find_transaction_by_hash(tenant_id, file_hash):
                st.error("⚠️ Αυτό το παραστατικό έχει ήδη καταχωρηθεί στο σύστημα!")
                return
            db.create_transaction(
                tenant_id, project, amount, type_="Expense",
                description=description.strip() or None,
                provider=provider.strip() or None,
                date=inv_date,  # date object; normalized to ISO in the client
                source="Invoice",
                file_hash=file_hash,
            )
        except db.AirtableError as exc:
            st.error(f"Η αποθήκευση απέτυχε: {exc}")
            return

        _invalidate_caches()
        st.session_state.pop("pending_invoice", None)
        st.session_state.pop("extraction_error", None)
        st.success(f"Το παραστατικό αποθηκεύτηκε επιτυχώς στην Κατηγορία "
                   f"«{project}» — {_money(amount)}.")
        if amount > HIGH_EXPENSE_THRESHOLD:
            st.error(
                f"🚨 ΠΡΟΕΙΔΟΠΟΙΗΣΗ ΥΨΗΛΟΥ ΕΞΟΔΟΥ — "
                f"{provider.strip() or 'άγνωστος προμηθευτής'}: {_money(amount)} "
                f"υπερβαίνει το όριο των {_money(HIGH_EXPENSE_THRESHOLD)}."
            )


# --------------------------------------------------------------------------
# Tab 3 — Date-range recap
# --------------------------------------------------------------------------
def _delete_txn_control(record_id):
    """Two-step delete for one transaction row: the 🗑️ button arms an inline
    confirmation; only the explicit "Ναι" tap deletes the Airtable record."""
    if st.session_state.get("confirm_delete_txn") != record_id:
        return
    st.warning("Είστε σίγουροι; Η κίνηση θα διαγραφεί οριστικά.")
    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button("✅ Ναι, διαγραφή", key=f"yesdel_{record_id}",
                     type="primary", width="stretch"):
            try:
                db.delete_transaction(record_id)
            except db.AirtableError as exc:
                st.error(f"Δεν ήταν δυνατή η διαγραφή: {exc}")
            else:
                st.session_state.pop("confirm_delete_txn", None)
                st.session_state["flash_recap"] = "Η κίνηση διαγράφηκε επιτυχώς."
                _invalidate_caches()
                st.rerun()
    with col_no:
        if st.button("✕ Άκυρο", key=f"nodel_{record_id}", width="stretch"):
            st.session_state.pop("confirm_delete_txn", None)
            st.rerun()


def _period_transactions_section(transactions, start, end):
    """List the period's individual transactions, each with a delete control."""
    period = sorted(
        (t for t in transactions if (d := _txn_date(t)) and start <= d <= end),
        key=_txn_date, reverse=True,
    )
    _section(f"Κινήσεις περιόδου ({len(period)})")
    if not period:
        _empty_state("Δεν υπάρχουν κινήσεις στην επιλεγμένη περίοδο.")
        return
    for txn in period:
        fields = txn["fields"]
        is_revenue = (fields.get("Type") or "Expense") == "Revenue"
        detail = (fields.get("Provider") or fields.get("Description")
                  or ("Έσοδο" if is_revenue else "Έξοδο"))
        meta = " · ".join(part for part in (
            _txn_date(txn).strftime("%d/%m/%Y"),
            fields.get("Project"),
            detail,
        ) if part)
        col_info, col_del = st.columns([4, 1.3], vertical_alignment="center")
        with col_info:
            st.markdown(
                f'<div class="aud-txn-row">'
                f'<span class="aud-txn-meta">{html.escape(meta)}</span>'
                f'<span class="aud-proj-value {"accent" if is_revenue else "warm"}">'
                f'{html.escape(_money(fields.get("Amount")))}</span></div>',
                unsafe_allow_html=True,
            )
        with col_del:
            if st.button("🗑️ Διαγραφή", key=f"del_{txn['id']}"):
                st.session_state["confirm_delete_txn"] = txn["id"]
                st.rerun()
        _delete_txn_control(txn["id"])


def recap_tab(tenant_id):
    _section("Ανασκόπηση περιόδου")
    flash = st.session_state.pop("flash_recap", None)
    if flash:
        st.success(flash)
    st.markdown(
        f'<div class="aud-upload-hint">Αρχειοθετημένες κατηγορίες και τα '
        f"αποτελέσματά τους. Δεδομένα παλαιότερα των {RETENTION_YEARS} ετών διαγράφονται "
        f"αυτόματα, οπότε η ανασκόπηση καλύπτει έως τα τελευταία "
        f"{RETENTION_YEARS} έτη.</div>",
        unsafe_allow_html=True,
    )

    today = date.today()
    earliest = _subtract_years(today, RETENTION_YEARS)
    selection = st.date_input(
        "Περίοδος",
        value=(earliest, today),
        min_value=earliest,
        max_value=today,
        format="DD/MM/YYYY",
    )
    if not (isinstance(selection, tuple) and len(selection) == 2):
        _empty_state("Επιλέξτε ημερομηνία έναρξης και ημερομηνία λήξης.")
        return
    start, end = selection
    if start > end:
        start, end = end, start

    try:
        completed = _load_completed_projects(tenant_id)
        transactions = _load_transactions(tenant_id)
    except db.AirtableError as exc:
        st.error(f"Δεν ήταν δυνατή η φόρτωση των δεδομένων σας: {exc}")
        return
    grouped = _transactions_by_project(transactions)

    selected = [p for p in completed
                if (d := _closed_date(p)) and start <= d <= end]
    if not selected:
        _empty_state(f"Δεν αρχειοθετήθηκαν κατηγορίες μεταξύ "
                     f"{start.strftime('%d/%m/%Y')} και {end.strftime('%d/%m/%Y')}.")
    else:
        total_rev = total_exp = 0.0
        rows = []
        for proj in sorted(selected, key=lambda p: _closed_date(p)):
            name = proj["fields"].get("Name") or "—"
            rev, exp, net = _project_financials(name, grouped)
            total_rev += rev
            total_exp += exp
            rows.append({
                "Κατηγορία": name,
                "Αρχειοθετήθηκε": _closed_date(proj).strftime("%d/%m/%Y"),
                "Έσοδα": _money(rev),
                "Έξοδα": _money(exp),
                "Καθαρό": _money(net),
            })

        st.dataframe(rows, width="stretch", hide_index=True)

        net = total_rev - total_exp
        _kpi_row([
            ("Συνολικά έσοδα", _money(total_rev), ""),
            ("Συνολικά έξοδα", _money(total_exp), "warm"),
            ("Καθαρό κέρδος", _money(net), "accent" if net >= 0 else "loss"),
        ])

    _period_transactions_section(transactions, start, end)


# --------------------------------------------------------------------------
# App entry point
# --------------------------------------------------------------------------
def main():
    _inject_css()
    _run_retention_cleanup()

    tenant_id = st.session_state.get("tenant_id")
    if not tenant_id:
        login_screen()
        return

    if not subscription_gate(tenant_id):
        return

    with st.sidebar:
        st.markdown('<div class="aud-brand-small">AuditAgent<span>.ai</span></div>',
                    unsafe_allow_html=True)
        st.markdown(f'<div class="aud-user">{html.escape(tenant_id)}</div>',
                    unsafe_allow_html=True)
        if st.button("Αποσύνδεση", width="stretch"):
            logout()
        # Manual refresh: the only place (besides login) that re-verifies the
        # subscription and re-reads Airtable data.
        if st.button("🔄 Ανανέωση", width="stretch"):
            _invalidate_caches()
            st.session_state.pop("subscription_verified", None)
            st.session_state.pop("subscription_status", None)
            st.rerun()

    tab_dashboard, tab_upload, tab_recap = st.tabs(
        ["📊 Πίνακας Ελέγχου", "📸 Καταχώρηση", "🗓 Ανασκόπηση"]
    )
    with tab_dashboard:
        dashboard_tab(tenant_id)
    with tab_upload:
        upload_tab(tenant_id)
    with tab_recap:
        recap_tab(tenant_id)


main()
