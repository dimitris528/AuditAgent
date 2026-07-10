"""
AuditAgent.ai — Streamlit PWA front-end (dark/light themes, Greek UI).

Presentation layer only differs from the classic build; the multi-tenant,
cached Airtable logic and the data-retention hooks are unchanged:
    1. Multi-tenant login (Username + Password, both checked against the
       Users table in ONE Airtable round-trip). The subscription verdict is
       cached in st.session_state and re-verified only on "Ανανέωση" or after
       a browser refresh. Logins survive refreshes via a server-side token
       store mirrored into the ?session= query param (see _session_store).
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
       A "✍️ Χειροκίνητη Καταχώρηση" expander covers document-less entries.
       Every successful save clears the data caches and reruns immediately,
       so dashboard/recap analytics update without a manual refresh; the
       confirmation arrives as a toast.
    4. Smart-period recap (current month / quarter / year / custom range) of
       archived categories, plus the period's individual transactions with a
       confirm-then-delete control on each row.
    5. The 2-year data-retention cleanup runs automatically in the background.

Transactions schema note: the table carries EXACTLY six columns — Username,
Amount, Date, Category, FileHash, Description. Revenue vs expense lives in
the SIGN of Amount (Έσοδο positive, Έξοδο negative); the UI always displays
absolute values. The supplier name from invoice extraction is folded into
Description.

Theming: every color is a CSS custom property (--aud-*) injected per theme.
Dark: #121214 canvas / #1C1C1E surfaces / #00E676 accent. Light: #F4F4F6
canvas / #FFFFFF surfaces / #00843D accent text. The sidebar toggle
"☀️ Φωτεινό / 🌙 Σκοτεινό Μορφότυπο" (ON = 🌙 dark, the default) swaps the
palette live.

Run with:   streamlit run app.py
"""

import difflib
import hashlib
import html
import os
import secrets
import tempfile
import time
from datetime import date

import streamlit as st

import airtable_client as db
from airtable_client import _subtract_years
from auditor import HIGH_EXPENSE_THRESHOLD, _parse_amount
from extractor import extract_invoice_data

RETENTION_YEARS = 2
UPLOAD_TYPES = ["pdf", "jpg", "jpeg", "png", "webp"]
# A login survives browser refreshes for this long (sliding window, renewed
# on every restored page load).
SESSION_TTL_SECONDS = 12 * 3600

st.set_page_config(
    page_title="AuditAgent.ai",
    page_icon="🧾",
    layout="centered",  # single column reads well on phones
)

# --------------------------------------------------------------------------
# Design system (CSS injection, theme-aware)
# --------------------------------------------------------------------------
# Every color below is exposed as a CSS variable so the sidebar toggle can
# swap the whole palette with a single :root block.
_DARK_PALETTE = {
    "bg": "#121214",
    "surface": "#1C1C1E",
    "sidebar": "#161618",
    "border": "#2C2C2E",
    "border-strong": "#3A3A3C",
    "text": "#FFFFFF",
    "text-soft": "#F5F5F7",
    "muted": "#8E8E93",
    "accent": "#00E676",        # fills (primary buttons, tab highlight)
    "accent-text": "#00E676",   # accent-colored TEXT on surfaces
    "warm": "#E6A23C",
    "loss": "#FF6B57",
    "shadow": "rgba(0, 0, 0, .35)",
}

_LIGHT_PALETTE = {
    "bg": "#F4F4F6",
    "surface": "#FFFFFF",
    "sidebar": "#ECECEF",
    "border": "#E1E1E6",
    "border-strong": "#C8C8CF",
    "text": "#1A1A1C",
    "text-soft": "#2B2B2E",
    "muted": "#6E6E73",
    "accent": "#00E676",
    "accent-text": "#00843D",   # darker green: mint text is unreadable on white
    "warm": "#B26A00",
    "loss": "#D63A22",
    "shadow": "rgba(0, 0, 0, .10)",
}

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
.stApp { background: var(--aud-bg); }

/* Chrome: hide menu/footer/toolbar, keep the header bar (mobile sidebar toggle) */
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] { display: none; }
header[data-testid="stHeader"] { background: transparent; }
/* Native sidebar toggle (collapse arrow / hamburger): NEVER hide it, and
   force it on even in builds that only reveal it on hover. Covers every
   testid Streamlit has used for the control. */
[data-testid="stSidebarCollapse"], [data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarCollapsedControl"], [data-testid="stExpandSidebarButton"] {
    display: flex !important; visibility: visible !important; opacity: 1 !important;
}
.block-container { padding-top: 2.2rem; max-width: 46rem; }

h1, h2, h3, h4, [data-testid="stMarkdownContainer"] h4 {
    color: var(--aud-text); letter-spacing: -0.02em;
}
/* Streamlit's built-in theme paints widget labels and body copy for dark
   mode only; re-anchor them to the active palette so light mode is legible. */
[data-testid="stWidgetLabel"] p, [data-testid="stMarkdownContainer"] p,
[data-testid="stWidgetLabel"] label, .stApp label {
    color: var(--aud-text-soft);
}
div[data-baseweb="input"] input, div[data-baseweb="textarea"] textarea,
div[data-baseweb="select"] input, div[data-baseweb="select"] > div {
    color: var(--aud-text) !important;
    -webkit-text-fill-color: var(--aud-text);
}
div[data-baseweb="input"] input::placeholder,
div[data-baseweb="textarea"] textarea::placeholder {
    color: var(--aud-muted); -webkit-text-fill-color: var(--aud-muted);
}
ul[data-baseweb="menu"] { background: var(--aud-surface) !important; }
ul[data-baseweb="menu"] li {
    background: var(--aud-surface) !important; color: var(--aud-text-soft) !important;
}
ul[data-baseweb="menu"] li:hover { background: var(--aud-border) !important; }

.aud-section {
    color: var(--aud-muted); font-size: .78rem; font-weight: 600; letter-spacing: .08em;
    text-transform: uppercase; margin: 18px 0 10px;
}

/* --- Brand ------------------------------------------------------------- */
.aud-brand {
    text-align: center; font-size: 2.1rem; font-weight: 800; color: var(--aud-text);
    letter-spacing: -0.03em; margin: 10vh 0 4px;
}
.aud-brand span { color: var(--aud-accent-text); }
.aud-tagline { text-align: center; color: var(--aud-muted); font-size: .95rem; margin-bottom: 26px; }
.aud-brand-small {
    font-size: 1.1rem; font-weight: 800; color: var(--aud-text); letter-spacing: -0.02em;
}
.aud-brand-small span { color: var(--aud-accent-text); }
.aud-user { color: var(--aud-muted); font-size: .82rem; margin: 2px 0 14px; word-break: break-all; }

/* --- KPI cards ----------------------------------------------------------- */
.aud-kpi-row { display: flex; gap: 12px; flex-wrap: wrap; margin: 2px 0 14px; }
.aud-kpi {
    flex: 1 1 150px; background: var(--aud-surface); border: 1px solid var(--aud-border);
    border-radius: 16px; padding: 18px 20px;
    transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease;
}
.aud-kpi:hover {
    transform: translateY(-2px); border-color: var(--aud-border-strong);
    box-shadow: 0 8px 24px var(--aud-shadow);
}
.aud-kpi-label { color: var(--aud-muted); font-size: .8rem; font-weight: 500; margin-bottom: 6px; }
.aud-kpi-value {
    color: var(--aud-text-soft); font-size: clamp(1.1rem, 4.5vw, 1.5rem); font-weight: 600;
    line-height: 1.15; white-space: nowrap;
}
.aud-kpi.warm  .aud-kpi-value { color: var(--aud-warm); }
.aud-kpi.loss  .aud-kpi-value { color: var(--aud-loss); }
.aud-kpi.accent { border-color: rgba(0, 230, 118, .35); }
.aud-kpi.accent:hover {
    border-color: rgba(0, 230, 118, .7);
    box-shadow: 0 8px 24px rgba(0, 230, 118, .12);
}
.aud-kpi.accent .aud-kpi-value {
    color: var(--aud-accent-text); font-size: clamp(1.3rem, 5.5vw, 1.75rem); font-weight: 700;
}

/* --- Project cards -------------------------------------------------------- */
.aud-proj {
    background: var(--aud-surface); border: 1px solid var(--aud-border); border-radius: 16px;
    padding: 16px 20px; margin-bottom: 12px; transition: border-color .18s ease;
}
.aud-proj:hover { border-color: var(--aud-border-strong); }
.aud-proj-name { color: var(--aud-text); font-weight: 600; font-size: 1.02rem; margin-bottom: 10px; }
.aud-proj-stats { display: flex; gap: 26px; flex-wrap: wrap; }
.aud-proj-value { color: var(--aud-text-soft); font-size: .98rem; font-weight: 600; }
.aud-proj-value.warm { color: var(--aud-warm); }
.aud-proj-value.accent { color: var(--aud-accent-text); }
.aud-proj-value.loss { color: var(--aud-loss); }

/* --- Close-project control ------------------------------------------------ */
div[class*="st-key-close_"] { margin: -4px 0 14px; }
div[class*="st-key-close_"] button {
    background: transparent; color: var(--aud-muted); border: 1px dashed var(--aud-border-strong);
    font-size: .85rem; font-weight: 500; padding: .38rem .9rem;
}
div[class*="st-key-close_"] button:hover {
    color: var(--aud-loss); border-color: rgba(255, 107, 87, .55); background: transparent;
}

/* --- Transaction rows (recap) + ghost delete button ------------------------- */
.aud-txn-row {
    display: flex; justify-content: space-between; align-items: baseline;
    gap: 12px; flex-wrap: wrap; padding: 9px 2px;
    border-bottom: 1px solid var(--aud-border); color: var(--aud-text-soft); font-size: .92rem;
}
.aud-txn-meta { color: var(--aud-text-soft); }
div[class*="st-key-del_"] button {
    background: transparent; color: var(--aud-muted); border: 1px dashed var(--aud-border-strong);
    font-size: .8rem; font-weight: 500; padding: .3rem .7rem;
}
div[class*="st-key-del_"] button:hover {
    color: var(--aud-loss); border-color: rgba(255, 107, 87, .55); background: transparent;
}

/* --- Closed projects list --------------------------------------------------- */
.aud-closed-row {
    display: flex; justify-content: space-between; align-items: baseline;
    gap: 12px; flex-wrap: wrap; padding: 10px 2px;
    border-bottom: 1px solid var(--aud-border); color: var(--aud-text-soft); font-size: .95rem;
}
.aud-closed-row:last-child { border-bottom: none; }
.aud-closed-name { font-weight: 600; }
.aud-closed-meta { color: var(--aud-muted); font-size: .85rem; }

/* --- Empty state ----------------------------------------------------------- */
.aud-empty {
    border: 1.5px dashed var(--aud-border); border-radius: 16px; padding: 30px 22px;
    text-align: center; color: var(--aud-muted); font-size: .95rem; margin-bottom: 14px;
}

/* --- Subscription lock ------------------------------------------------------ */
.aud-lock {
    background: var(--aud-surface); border: 1px solid rgba(255, 107, 87, .35);
    border-radius: 20px; padding: 44px 30px; text-align: center;
    max-width: 430px; margin: 14vh auto 22px;
}
.aud-lock-icon {
    width: 58px; height: 58px; border-radius: 50%;
    background: rgba(255, 107, 87, .12); color: var(--aud-loss);
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 18px; font-size: 1.5rem;
}
.aud-lock-title { color: var(--aud-text); font-weight: 700; font-size: 1.12rem; margin-bottom: 8px; }
.aud-lock-text { color: var(--aud-muted); font-size: .95rem; line-height: 1.6; }

/* --- Buttons ------------------------------------------------------------- */
.stButton > button, [data-testid="stFormSubmitButton"] > button {
    border-radius: 12px; font-weight: 600; padding: .62rem 1rem;
    transition: all .18s ease;
}
button[kind="secondary"], button[data-testid="stBaseButton-secondaryFormSubmit"] {
    background: var(--aud-surface); color: var(--aud-text-soft); border: 1px solid var(--aud-border);
}
button[kind="secondary"]:hover, button[data-testid="stBaseButton-secondaryFormSubmit"]:hover {
    border-color: var(--aud-muted); color: var(--aud-text);
}
button[kind="primary"], button[data-testid="stBaseButton-primaryFormSubmit"] {
    background: var(--aud-accent) !important; border: none !important;
}
button[kind="primary"], button[data-testid="stBaseButton-primaryFormSubmit"],
button[kind="primary"] *, button[data-testid="stBaseButton-primaryFormSubmit"] * {
    color: #121214 !important;  /* dark label on the mint fill in BOTH themes */
}
button[kind="primary"]:hover, button[data-testid="stBaseButton-primaryFormSubmit"]:hover {
    filter: brightness(1.06);
    box-shadow: 0 0 0 1px rgba(0, 230, 118, .45), 0 6px 22px rgba(0, 230, 118, .28);
}
button[kind="primary"]:active { transform: scale(.985); }

/* --- Inputs ------------------------------------------------------------- */
div[data-baseweb="input"], div[data-baseweb="textarea"], div[data-baseweb="select"] > div {
    background: var(--aud-surface) !important; border-color: var(--aud-border) !important;
    border-radius: 12px !important;
}
div[data-baseweb="input"]:focus-within, div[data-baseweb="textarea"]:focus-within,
div[data-baseweb="select"] > div:focus-within {
    border-color: var(--aud-accent) !important;
}

/* --- Forms / expanders as cards ------------------------------------------- */
[data-testid="stForm"] {
    background: var(--aud-surface); border: 1px solid var(--aud-border); border-radius: 16px;
    padding: 22px 22px 16px;
}
[data-testid="stExpander"] {
    background: var(--aud-surface); border: 1px solid var(--aud-border); border-radius: 16px;
}
[data-testid="stExpander"] summary { color: var(--aud-text-soft); }
[data-testid="stExpander"] summary:hover { color: var(--aud-text); }

/* --- Upload zone --------------------------------------------------------- */
[data-testid="stFileUploaderDropzone"] {
    background: var(--aud-surface); border: 1.5px dashed var(--aud-border-strong); border-radius: 16px;
    padding: 30px 22px;
    transition: border-color .18s ease, box-shadow .18s ease;
}
[data-testid="stFileUploaderDropzone"]:hover {
    border-color: var(--aud-accent);
    box-shadow: 0 0 0 1px rgba(0, 230, 118, .2), 0 8px 28px rgba(0, 230, 118, .08);
}
[data-testid="stFileUploaderDropzone"] button {
    background: transparent; color: var(--aud-accent-text); border: 1px solid rgba(0, 230, 118, .45);
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
    color: var(--aud-text-soft); font-weight: 600; font-size: .95rem;
}
[data-testid="stFileUploaderDropzoneInstructions"]::after {
    content: 'PDF ή φωτογραφία (JPG, PNG)'; display: block;
    color: var(--aud-muted); font-size: .8rem; margin-top: 3px;
}
[data-testid="stFileUploaderDropzone"] button { font-size: 0; line-height: 0; }
[data-testid="stFileUploaderDropzone"] button * { display: none !important; }
[data-testid="stFileUploaderDropzone"] button::after {
    content: 'Επιλογή αρχείου'; font-size: .9rem; line-height: 1.2;
}
.aud-upload-hint { color: var(--aud-muted); font-size: .95rem; line-height: 1.55; margin: 2px 0 12px; }

/* --- Spinner (minimal, on-accent) ----------------------------------------- */
[data-testid="stSpinner"] { color: var(--aud-muted); }
[data-testid="stSpinner"] i {
    border-color: var(--aud-accent) rgba(0, 230, 118, .15) rgba(0, 230, 118, .15) !important;
}

/* --- Tabs ---------------------------------------------------------------- */
.stTabs [data-baseweb="tab-list"] { gap: 4px; }
.stTabs button[data-baseweb="tab"] {
    color: var(--aud-muted); font-weight: 500; background: transparent;
}
.stTabs button[data-baseweb="tab"]:hover { color: var(--aud-text-soft); }
.stTabs button[data-baseweb="tab"][aria-selected="true"] { color: var(--aud-text); font-weight: 600; }
.stTabs [data-baseweb="tab-highlight"] { background-color: var(--aud-accent); }
.stTabs [data-baseweb="tab-border"] { background-color: var(--aud-border); }

/* --- Sidebar ------------------------------------------------------------- */
[data-testid="stSidebar"] { background: var(--aud-sidebar); border-right: 1px solid var(--aud-border); }

/* --- Alerts ------------------------------------------------------------- */
[data-testid="stAlert"] { border-radius: 12px; }
</style>
"""


def _inject_css():
    """Emit the palette for the active theme, then the static stylesheet.

    The toggle widget (key "theme_dark") lives in the sidebar and triggers a
    rerun on change; by the time this runs again st.session_state already
    holds the new value. ON (True) = 🌙 dark, the default look.
    """
    palette = _DARK_PALETTE if st.session_state.get("theme_dark", True) else _LIGHT_PALETTE
    root = "".join(f"--aud-{name}: {value};" for name, value in palette.items())
    st.markdown(f"<style>:root {{ {root} }}</style>", unsafe_allow_html=True)
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
def _load_active_projects(username):
    return db.get_active_projects(username)


@st.cache_data(ttl=120, show_spinner=False)
def _load_completed_projects(username):
    return db.get_completed_projects(username)


@st.cache_data(ttl=120, show_spinner=False)
def _load_transactions(username):
    return db.get_transactions(username)


def _invalidate_caches():
    _load_active_projects.clear()
    _load_completed_projects.clear()
    _load_transactions.clear()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _money(value):
    # Amounts are stored signed (expense negative); always display magnitude.
    return f"{abs(float(value or 0)):,.2f} €"


def _amount(record):
    return float(record["fields"].get("Amount") or 0)


def _is_revenue(record):
    """Revenue vs expense lives in the SIGN of Amount (schema has no Type
    column): positive/zero = Έσοδο, negative = Έξοδο."""
    return _amount(record) >= 0


def _sum_by_type(records):
    """Return (expense_total, revenue_total) — both positive magnitudes."""
    expense = revenue = 0.0
    for rec in records:
        amount = _amount(rec)
        if amount >= 0:
            revenue += amount
        else:
            expense += -amount
    return expense, revenue


def _transactions_by_category(transactions):
    grouped = {}
    for txn in transactions:
        key = (txn["fields"].get("Category") or "").strip().lower()
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
# Session persistence (login survives a browser refresh)
# --------------------------------------------------------------------------
# st.session_state alone dies on F5 — Streamlit starts a brand-new session on
# every browser refresh. So a successful login also mints an opaque random
# token, kept server-side (token -> username, TTL) and mirrored into the URL
# as ?session=…; the refreshed page finds the token in its query params and
# silently re-hydrates st.session_state. Only the unguessable token ever
# appears in the URL — never the username or password.
@st.cache_resource(show_spinner=False)
def _session_store():
    """Server-side {token: {"username", "expires"}} map, shared across
    Streamlit sessions in this server process."""
    return {}


def _issue_session_token(username):
    store = _session_store()
    now = time.time()
    for stale in [t for t, entry in store.items() if entry["expires"] < now]:
        store.pop(stale, None)
    token = secrets.token_urlsafe(32)
    store[token] = {"username": username, "expires": now + SESSION_TTL_SECONDS}
    st.query_params["session"] = token
    return token


def _revoke_session_token():
    token = (st.session_state.get("session_token")
             or st.query_params.get("session"))
    if token:
        _session_store().pop(token, None)
    if "session" in st.query_params:
        del st.query_params["session"]


def _restore_session():
    """After a browser refresh, log the user back in from the URL token."""
    if st.session_state.get("username"):
        return
    token = st.query_params.get("session")
    if not token:
        return
    entry = _session_store().get(token)
    now = time.time()
    if not entry or entry["expires"] < now:
        # Expired or revoked — drop the dead token so login starts clean.
        _session_store().pop(token, None)
        del st.query_params["session"]
        return
    entry["expires"] = now + SESSION_TTL_SECONDS  # sliding renewal
    st.session_state["username"] = entry["username"]
    st.session_state["session_token"] = token
    # The subscription verdict is NOT restored — subscription_gate re-checks
    # it once against Airtable, so a refresh can't outlive a cancelled plan.


# --------------------------------------------------------------------------
# Login (tenant context + password check)
# --------------------------------------------------------------------------
def _password_matches(stored, typed):
    """Compare the Users-table Password column against the typed password.

    The column may be Text or Number in Airtable; a numeric cell comes back
    as int/float, so both sides are normalized to a string. Comparison is
    case-sensitive. An empty or missing stored password never matches
    (fail closed).
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
            username = st.text_input(
                "Όνομα Χρήστη (Username)",
                placeholder="π.χ. mystore",
                help="👤 Εισάγετε το μοναδικό όνομα χρήστη που σας έχει "
                     "παραχωρηθεί από τον διαχειριστή.",
            )
            password = st.text_input(
                "Κωδικός Πρόσβασης (Password)",
                type="password",  # native browser masking
                placeholder="••••••••",
            )
            submitted = st.form_submit_button("Σύνδεση", type="primary",
                                              width="stretch")
    if submitted:
        username = username.strip()
        password = password.strip()
        with mid:
            if not username or not password:
                st.error("Παρακαλώ συμπληρώστε το Όνομα Χρήστη και τον "
                         "Κωδικό Πρόσβασης.")
                return
            try:
                record = db.get_user_record(username)
            except db.AirtableError as exc:
                print(f"[WARN] Login lookup failed for {username!r}: {exc}")
                st.error("Ο έλεγχος του λογαριασμού σας απέτυχε προσωρινά. "
                         "Παρακαλώ δοκιμάστε ξανά σε λίγο.")
                return
            if record is None or not _password_matches(record.get("Password"), password):
                st.error("❌ Το Όνομα Χρήστη ή ο Κωδικός Πρόσβασης είναι "
                         "εσφαλμένα.")
                return
        # Credentials verified — reuse the same Users row for the
        # subscription verdict so login stays a single Airtable call, and
        # mint the refresh-survival token (see _session_store above).
        st.session_state["username"] = username
        st.session_state["session_token"] = _issue_session_token(username)
        st.session_state["subscription_verified"] = True
        st.session_state["subscription_status"] = record.get("SubscriptionStatus")
        st.rerun()


def logout():
    # Revoke the refresh-survival token FIRST so a stale ?session= URL can
    # never resurrect the login after an explicit logout.
    _revoke_session_token()
    # "theme_dark" is deliberately kept so the theme survives a re-login.
    for key in ("username", "session_token", "pending_invoice",
                "processed_upload", "extraction_error",
                "subscription_verified", "subscription_status",
                "confirm_close", "flash", "confirm_delete_txn", "flash_recap",
                "flash_toast", "flash_toast_alert"):
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


def subscription_gate(username):
    """Return True only for a Username with a Users row whose
    SubscriptionStatus is "Active"; render the appropriate block screen
    otherwise.

    Performance: the Users-table lookup runs ONCE per browser session and the
    result is cached in st.session_state, so widget clicks and reruns never
    re-query Airtable. The sidebar "Ανανέωση" button (and a fresh login)
    clears the cached verdict and forces a re-check.

    Strict allow-list: unknown usernames, blank/other statuses, and
    Users-table lookup failures are all denied. Failing closed on lookup
    errors matters — failing open would let anyone in whenever the Users
    table is unreachable. Lookup FAILURES are never cached, so a transient
    outage doesn't lock the user out for the rest of the session.
    """
    if not st.session_state.get("subscription_verified"):
        try:
            status = db.get_subscription_status(username)
        except db.AirtableError as exc:
            print(f"[WARN] Subscription check failed for {username!r}: {exc}")
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
        _lock_screen("Ο λογαριασμός δεν βρέθηκε ή δεν είναι ενεργός. Παρακαλώ "
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


def dashboard_tab(username):
    flash = st.session_state.pop("flash", None)
    if flash:
        st.success(flash)

    try:
        active = _load_active_projects(username)
        completed = _load_completed_projects(username)
        grouped = _transactions_by_category(_load_transactions(username))
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
                elif db.find_active_project(username, name):
                    st.error(f"Υπάρχει ήδη ενεργή κατηγορία με το όνομα «{name}».")
                else:
                    try:
                        db.create_project(username, name)
                    except db.AirtableError as exc:
                        st.error("Δεν ήταν δυνατή η δημιουργία της "
                                 f"κατηγορίας: {exc}")
                    else:
                        _invalidate_caches()
                        st.rerun()

    if completed:
        _closed_projects_section(completed, grouped)


# --------------------------------------------------------------------------
# Tab 2 — Invoice upload + confirmation + manual entry
# --------------------------------------------------------------------------
def _invoice_flow(username):
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
        active = _load_active_projects(username)
    except db.AirtableError as exc:
        st.error(f"Δεν ήταν δυνατή η φόρτωση των κατηγοριών σας: {exc}")
        return
    if not active:
        st.warning("Χρειάζεστε μια ενεργή κατηγορία πριν αποθηκεύσετε "
                   "παραστατικό — δημιουργήστε μία στην καρτέλα "
                   "«Πίνακας Ελέγχου».")
        return

    _section("Επιβεβαίωση των στοιχείων")
    category_names = [p["fields"].get("Name") or "—" for p in active]

    # Fuzzy-match the Vision-detected category name to preselect the dropdown.
    default_index = 0
    detected = str(pending.get("project_name") or "").strip().lower()
    if detected and detected not in {"null", "none", "n/a", "na", "-"}:
        lowered = [n.strip().lower() for n in category_names]
        closest = difflib.get_close_matches(detected, lowered, n=1, cutoff=0.6)
        if closest:
            default_index = lowered.index(closest[0])

    with st.form("confirm_invoice"):
        category = st.selectbox("Κατηγορία", category_names, index=default_index)
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
        # The schema has no Provider column — the supplier rides inside
        # Description alongside the materials summary.
        full_description = " · ".join(
            part for part in (provider.strip(), description.strip()) if part
        ) or None
        try:
            if file_hash and db.find_transaction_by_hash(username, file_hash):
                st.error("⚠️ Αυτό το παραστατικό έχει ήδη καταχωρηθεί στο σύστημα!")
                return
            # Invoices are expenses -> stored negative; a credit note typed as
            # a negative total flips to positive (revenue), which is correct.
            db.create_transaction(
                username, category, -amount,
                description=full_description,
                date=inv_date,  # date object; normalized to ISO in the client
                file_hash=file_hash,
            )
        except db.AirtableError as exc:
            st.error(f"❌ Η αποθήκευση στο Airtable απέτυχε: {exc}")
            return

        # Instant analytics: clear every cached read and rerun NOW so the
        # dashboard/recap numbers already include this row on the next paint.
        # The confirmations ride session_state as toasts because widgets
        # rendered before st.rerun() never reach the screen. processed_upload
        # is kept: the uploader still holds the file after the rerun and the
        # fingerprint match is what stops a re-extraction (and a re-billing).
        _invalidate_caches()
        st.session_state.pop("pending_invoice", None)
        st.session_state.pop("extraction_error", None)
        st.session_state["flash_toast"] = (
            f"Το παραστατικό αποθηκεύτηκε επιτυχώς στην Κατηγορία "
            f"«{category}» — {_money(amount)}."
        )
        if amount > HIGH_EXPENSE_THRESHOLD:
            st.session_state["flash_toast_alert"] = (
                f"ΠΡΟΕΙΔΟΠΟΙΗΣΗ ΥΨΗΛΟΥ ΕΞΟΔΟΥ — "
                f"{provider.strip() or 'άγνωστος προμηθευτής'}: {_money(amount)} "
                f"υπερβαίνει το όριο των {_money(HIGH_EXPENSE_THRESHOLD)}."
            )
        st.rerun()


def _manual_entry_section(username):
    """Document-less entry: type + amount + date + category + optional notes.

    The notes land in the Transactions Description column; the Έσοδο/Έξοδο
    choice is encoded as the sign of Amount.
    """
    with st.expander("✍️ Χειροκίνητη Καταχώρηση"):
        try:
            active = _load_active_projects(username)
        except db.AirtableError as exc:
            st.error(f"Δεν ήταν δυνατή η φόρτωση των κατηγοριών σας: {exc}")
            return
        if not active:
            st.warning("Χρειάζεστε μια ενεργή κατηγορία πριν καταχωρήσετε "
                       "κίνηση — δημιουργήστε μία στην καρτέλα "
                       "«Πίνακας Ελέγχου».")
            return
        category_names = [p["fields"].get("Name") or "—" for p in active]

        with st.form("manual_entry", clear_on_submit=True):
            entry_type = st.selectbox("Τύπος κίνησης", ["Έξοδο", "Έσοδο"])
            amount = st.number_input("Ποσό (€)", min_value=0.0, step=0.01)
            entry_date = st.date_input("Ημερομηνία", value=date.today(),
                                       format="DD/MM/YYYY")
            category = st.selectbox("Κατηγορία", category_names)
            notes = st.text_area("📝 Αιτιολογία / Σημειώσεις (Προαιρετικό)")
            submitted = st.form_submit_button("💾 Αποθήκευση κίνησης",
                                              type="primary", width="stretch")
        if submitted:
            if amount <= 0:
                st.error("Παρακαλώ εισάγετε ποσό μεγαλύτερο από 0.")
                return
            signed = amount if entry_type == "Έσοδο" else -amount
            try:
                # Type/Source are explicit labels on manual rows; FileHash
                # (and the supplier folded into Description) stay reserved
                # for the AI scanning path.
                db.create_transaction(
                    username, category, signed,
                    description=notes.strip() or None,
                    date=entry_date,
                    type_=entry_type,   # "Έσοδο" / "Έξοδο" as picked in the UI
                    source="Manual",
                )
            except db.AirtableError as exc:
                st.error(f"❌ Η αποθήκευση στο Airtable απέτυχε: {exc}")
                return
            # Instant analytics: same clear-and-rerun as the invoice flow.
            _invalidate_caches()
            st.session_state["flash_toast"] = (
                f"Η κίνηση ({entry_type}) αποθηκεύτηκε επιτυχώς στην "
                f"Κατηγορία «{category}» — {_money(amount)}."
            )
            st.rerun()


def upload_tab(username):
    _invoice_flow(username)
    _manual_entry_section(username)


# --------------------------------------------------------------------------
# Tab 3 — Smart-period recap
# --------------------------------------------------------------------------
PERIOD_MONTH = "Τρέχων Μήνας"
PERIOD_QUARTER = "Τρέχον Τρίμηνο"
PERIOD_YEAR = "Τρέχον Έτος"
PERIOD_CUSTOM = "Προσαρμοσμένο Εύρος"


def _resolve_period(choice, today, earliest):
    """Return (start, end) for a smart-period choice, or None for custom.

    Starts are clamped to the retention horizon so the picker can never claim
    a window whose data has already been purged.
    """
    if choice == PERIOD_MONTH:
        start = today.replace(day=1)
    elif choice == PERIOD_QUARTER:
        quarter_first_month = 3 * ((today.month - 1) // 3) + 1
        start = today.replace(month=quarter_first_month, day=1)
    elif choice == PERIOD_YEAR:
        start = today.replace(month=1, day=1)
    else:
        return None
    return max(start, earliest), today


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
        is_revenue = _is_revenue(txn)
        detail = (fields.get("Description")
                  or ("Έσοδο" if is_revenue else "Έξοδο"))
        meta = " · ".join(part for part in (
            _txn_date(txn).strftime("%d/%m/%Y"),
            fields.get("Category"),
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


def recap_tab(username):
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

    choice = st.selectbox(
        "📅 Επιλογή Περιόδου",
        [PERIOD_MONTH, PERIOD_QUARTER, PERIOD_YEAR, PERIOD_CUSTOM],
    )
    resolved = _resolve_period(choice, today, earliest)
    if resolved:
        start, end = resolved
    else:
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
    st.markdown(
        f'<div class="aud-upload-hint">Περίοδος: '
        f"{start.strftime('%d/%m/%Y')} — {end.strftime('%d/%m/%Y')}</div>",
        unsafe_allow_html=True,
    )

    try:
        completed = _load_completed_projects(username)
        transactions = _load_transactions(username)
    except db.AirtableError as exc:
        st.error(f"Δεν ήταν δυνατή η φόρτωση των δεδομένων σας: {exc}")
        return
    grouped = _transactions_by_category(transactions)

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
def _render_sidebar(username):
    """The sidebar exists on EVERY screen (login included) so the native
    collapse/expand toggle is always available; account controls appear only
    once logged in."""
    with st.sidebar:
        st.markdown('<div class="aud-brand-small">AuditAgent<span>.ai</span></div>',
                    unsafe_allow_html=True)
        if username:
            st.markdown(f'<div class="aud-user">{html.escape(username)}</div>',
                        unsafe_allow_html=True)
        # ON = 🌙 dark (default). Changing it reruns the script; _inject_css
        # reads the new value from session_state at the top of the rerun.
        st.toggle("☀️ Φωτεινό / 🌙 Σκοτεινό Μορφότυπο", value=True,
                  key="theme_dark")
        if not username:
            return
        if st.button("Αποσύνδεση", width="stretch"):
            logout()
        # Manual refresh: still handy for edits made directly in Airtable and
        # for re-verifying the subscription; the app's own saves now refresh
        # the analytics automatically.
        if st.button("🔄 Ανανέωση", width="stretch"):
            _invalidate_caches()
            st.session_state.pop("subscription_verified", None)
            st.session_state.pop("subscription_status", None)
            st.rerun()


def _flush_toasts():
    """Deliver save confirmations queued before an st.rerun(). Toasts float
    above the page, so they are visible no matter which tab is active."""
    toast = st.session_state.pop("flash_toast", None)
    if toast:
        st.toast(toast, icon="✅")
    alert = st.session_state.pop("flash_toast_alert", None)
    if alert:
        st.toast(alert, icon="🚨")


def main():
    _inject_css()
    _run_retention_cleanup()
    _restore_session()

    username = st.session_state.get("username")
    _render_sidebar(username)

    if not username:
        login_screen()
        return

    if not subscription_gate(username):
        return

    _flush_toasts()

    tab_dashboard, tab_upload, tab_recap = st.tabs(
        ["📊 Πίνακας Ελέγχου", "📸 Καταχώρηση", "🗓 Ανασκόπηση"]
    )
    with tab_dashboard:
        dashboard_tab(username)
    with tab_upload:
        upload_tab(username)
    with tab_recap:
        recap_tab(username)


main()
