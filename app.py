"""
Μπακαλοχαρτο (Bakalocharto) — Streamlit PWA front-end (dark/light themes,
Greek UI).

Presentation layer only differs from the classic build; the multi-tenant,
cached Airtable logic and the data-retention hooks are unchanged:
    1. Multi-tenant login (Username + Password, both checked against the
       Users table in ONE Airtable round-trip). The subscription verdict is
       cached in st.session_state and re-verified only on "Ανανέωση" or after
       a browser refresh. Logins survive refreshes via a server-side token
       store mirrored into the ?session= query param AND into the browser's
       localStorage, so even a WebSocket reconnect that lands with empty
       session_state and no query param self-heals (see _session_store /
       _sync_browser_session). Explicit logout wipes every copy.
       "Ξέχασα τον κωδικό μου": email -> 6-digit code (smtplib/Gmail App
       Password) -> code + new password -> pbkdf2 hash written to Airtable
       (ResetToken / ResetTokenExpiry columns; 15-min TTL, 5 attempts).
       "Εγγραφή εδώ": self-registration (Username/Email/Password) creates
       the Users row with a hashed password, SubscriptionStatus "Active"
       and TrialExpiry = now + 15 days (free trial, no card). While the
       trial runs, the sidebar shows "⏳ Απομένουν X ημέρες δοκιμής"; once
       TrialExpiry passes, the subscription gate flips the row to
       "Inactive" in Airtable and the paywall takes over. A paid Stripe
       activation clears TrialExpiry, so subscribers never re-trip the
       trial check.
    2. Executive dashboard: a merged header row — the Συνολικά έσοδα (soft
       green) and Συνολικά έξοδα (soft red) KPI cards are REAL clickable
       buttons; tapping one toggles its inline Amount/VAT-rate/Date/Client/
       Description quick-entry form beneath the row (opening one closes the
       other). The third card, Καθαρό, stays static and colors itself by
       sign: green when positive, red when negative, neutral grey at zero.
       The fourth card, Χρεωστούμενα (debts/receivables), is ALWAYS yellow
       (#f1c40f family) in both themes and doubles as the debts action
       button: tapping it opens the inline «Ποσό προς προσθήκη»/«Πελάτης»
       form plus the list of outstanding debts, each with an «Εξόφληση»
       control — resolving one flips the row's Type to "Έσοδο" in Airtable,
       so the amount automatically leaves Χρεωστούμενα and lands in the
       Έσοδα of the same client. All amount fields across the app are
       plain manual text inputs (no +/- steppers), parsed leniently
       ("150", "12,50", "1.204,80").
       Each active client card carries small inline "🔒 Αρχειοθέτηση" and
       "✏️ Επεξεργασία" ghost buttons to its right. Επεξεργασία reveals an
       inline editor with two manual amount fields (Έσοδα / Έξοδα) and a
       READ-ONLY Καθαρό preview that always equals Έσοδα − Έξοδα; saving
       writes the difference from the current sums as signed correction
       rows ("Χειροκίνητη διόρθωση…") in the Transactions table, so every
       analytics view recalculates from the same source of truth. Each active
       client card shows the 5 core accounting metrics in a structured grid —
       Έσοδα (gross & net), Έξοδα (gross & net), Φ.Π.Α. (net tax balance),
       Χρεωστούμενα, Καθαρό Αποτέλεσμα (net-of-VAT profit) — plus a
       foundational Μπάρα Φόρου Εισοδήματος (income-tax progress bar). Archived
       clients hide behind a large KPI-styled "📂 Αρχειοθετημένοι
       Πελάτες" toggle button. New
       clients are created inline from ANY client dropdown via the
       permanent trailing "➕ Δημιουργία Νέου Πελάτη..." option (there
       is no standalone creation form). The UI speaks accounting Greek
       ("Πελάτες" / Clients) for accounting professionals; the Airtable schema
       underneath (Projects/Status/Name + the Category column on Transactions)
       is UNCHANGED — only the visible terminology moved from Κατηγορία to
       Πελάτης.
    3. Single-tap document upload (camera photo or PDF) -> GPT-4o extraction
       -> confirmation screen -> saved to Airtable. If extraction fails for
       ANY reason the form degrades to blank manual entry instead of blocking.
       Each upload's SHA-256 lands in the Transactions FileHash column and is
       re-checked (per tenant) before every save to reject duplicate receipts.
       Document-less entries go through the dashboard's clickable Έσοδα/
       Έξοδα cards and their inline quick-entry forms.
       Every successful save clears the data caches and reruns immediately,
       so dashboard/recap analytics update without a manual refresh; the
       confirmation arrives as a toast.
    4. Smart-period recap (current month / quarter / year / custom range) of
       archived categories. Its totals row wears the same palette as the
       dashboard header, and — like the dashboard — the Συνολικά έσοδα /
       Συνολικά έξοδα cards are REAL clickable buttons: tapping one reveals
       the period's matching transaction list (income or expense rows,
       color-coded, each with a confirm-then-delete control) directly
       beneath the row; opening one closes the other. There is no separate
       "Κινήσεις περιόδου" list anymore — recap only; the dashboard keeps
       its own layout and quick-entry behavior untouched.
    5. Πληρωμές page: Stripe billing center — Payment Link for subscribing
       (10€/μήνα) and Customer Portal link for card changes/cancellation,
       both opening in a new browser tab. Logged-in users with ANY
       non-Active status (Expired/Inactive/Unpaid/blank) land on a paywall
       gateway instead of the app pages: subscribe link, a "Πλήρωσα"
       re-check button that clears the cached verdict, and logout.
    Navigation is a styled sidebar radio (Πίνακας Ελέγχου / Καταχώρηση /
    Ανασκόπηση / Πληρωμές) next to the theme toggle and account controls —
    the old top st.tabs row is gone; only the active page renders.
       The subscribe URL stamps the Username as client_reference_id; the
       companion webhook service (stripe_webhook.py, deployed separately —
       Streamlit itself cannot receive POSTs) verifies Stripe's signature
       and flips the Users row to Active on checkout.session.completed.
    6. The 2-year data-retention cleanup runs automatically in the background.

Transactions schema note: the table carries the columns Username, Amount,
Date, Category, FileHash, Description, Type, Source. Revenue vs expense
lives in the SIGN of Amount (Έσοδο positive, Έξοδο negative); the UI always
displays absolute values. The supplier name from invoice extraction is
folded into Description. Χρεωστούμενα (debts) live in the SAME table as
Type "Χρεωστούμενο" rows with a positive Amount; they are excluded from
every revenue/expense figure until Εξόφληση flips their Type to "Έσοδο".

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
import json
import os
import re
import secrets
import smtplib
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage

import streamlit as st

import airtable_client as db
import finance
import passwords
from airtable_client import _subtract_years
from config import SMTP_EMAIL, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT
from auditor import HIGH_EXPENSE_THRESHOLD, _parse_amount
from extractor import extract_invoice_data

RETENTION_YEARS = 2
UPLOAD_TYPES = ["pdf", "jpg", "jpeg", "png", "webp"]

# --------------------------------------------------------------------------
# Φ.Π.Α. (VAT) + income-tax parameters — sourced from the shared finance module
# --------------------------------------------------------------------------
# Each transaction carries its OWN ΦΠΑ rate (Greek scale: 24 % standard, 13 %
# reduced, 6 % super-reduced, 0 % exempt). The VAT euro amount is derived from
# the GROSS figure as gross × rate/(1+rate), rounded to cents ONCE and stored
# per row, so every aggregate is a pure partition-sum and total VAT == Σ
# per-client VATs. These constants and the pure helpers now live in finance.py
# (single source of truth for Streamlit AND the web API).
VAT_RATES = finance.VAT_RATES
DEFAULT_VAT_RATE = finance.DEFAULT_VAT_RATE
VAT_RATE_LABELS = finance.VAT_RATE_LABELS
TAX_BRACKET_LIMIT = finance.TAX_BRACKET_LIMIT   # € net taxable income step-up
TAX_RATE_LOW = finance.TAX_RATE_LOW             # bracket below the limit
TAX_RATE_HIGH = finance.TAX_RATE_HIGH           # bracket at/above the limit
# A login survives browser refreshes for this long (sliding window, renewed
# on every restored page load).
SESSION_TTL_SECONDS = 12 * 3600
# A password-reset code emailed by "Ξέχασα τον κωδικό μου" is valid this long.
RESET_CODE_TTL_MINUTES = 15
# Self-registered accounts start "Active" on a free trial this long; the
# subscription gate flips them to "Inactive" (-> Stripe paywall) afterwards.
TRIAL_DAYS = 15

st.set_page_config(
    page_title="Μπακαλοχαρτο",
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
/* Belt-and-braces for auto-translate extensions (Samsung Internet Translate,
   Chrome's built-in translate, etc.): the CSS Translate Level 4 property is
   the modern equivalent of the notranslate class/attribute/meta trio that
   _install_notranslate_guard stamps onto html/body/.stApp via JS. Rewriting
   text nodes inside React's tree is what triggers the removeChild crash on
   Samsung devices, so every layer that can say "don't touch this" is
   applied. */
.notranslate, html.notranslate, body.notranslate, .stApp.notranslate {
    translate: no;
}
/* Material icon glyphs are ligatures — they must keep their icon font or they
   render as raw words ("keyboard_arrow_right"). Re-assert after the override. */
[data-testid="stIconMaterial"], [class*="material-symbols"], [class*="material-icons"] {
    font-family: 'Material Symbols Rounded', 'Material Symbols Outlined',
                 'Material Icons' !important;
}
.stApp { background: var(--aud-bg); }

/* Chrome: hide ALL Streamlit-branded chrome (white-label). Do NOT hide
   [data-testid="stHeader"] or [data-testid="stToolbar"] wholesale: since
   Streamlit ~1.5x those wrap the ENTIRE header bar and the sidebar expand
   arrow (stExpandSidebarButton) renders inside them — hiding either leaves a
   collapsed sidebar with no way to reopen. Hide each child element instead:
   hamburger menu, footer, rainbow decoration strip, toolbar actions, Deploy
   button (old + new testids), status/"Running" widget, and the hosted
   "Made with Streamlit" viewer badge. */
#MainMenu, footer, [data-testid="stDecoration"],
[data-testid="stMainMenu"], [data-testid="stToolbarActions"],
[data-testid="stAppDeployButton"], [data-testid="stDeployButton"],
[data-testid="stStatusWidget"],
[class^="viewerBadge"], [class*="viewerBadge"] { display: none !important; }
header[data-testid="stHeader"] { background: transparent; box-shadow: none; }
/* Native sidebar toggle (collapse arrow / hamburger): NEVER hide it, and
   force it on even in builds that only reveal it on hover. Covers every
   testid Streamlit has used for the control. */
[data-testid="stSidebarCollapse"], [data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarCollapsedControl"], [data-testid="stExpandSidebarButton"] {
    display: flex !important; visibility: visible !important; opacity: 1 !important;
}
/* ...and paint the arrow with the ACTIVE text color: the native (dark)
   theme renders the glyph white, which vanishes on the light canvas.
   var(--text-color) is aliased to the palette in _inject_css, so it's
   black in Light Mode and white in Dark Mode automatically. Covers both
   the Material-icon ligature and SVG builds of the control. */
[data-testid="stSidebarCollapse"] button, [data-testid="stSidebarCollapseButton"] button,
[data-testid="stSidebarCollapsedControl"] button, [data-testid="stExpandSidebarButton"] button,
header[data-testid="stHeader"] button[kind="headerNoPadding"] {
    color: var(--text-color) !important;
}
[data-testid="stSidebarCollapse"] [data-testid="stIconMaterial"],
[data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"],
[data-testid="stSidebarCollapsedControl"] [data-testid="stIconMaterial"],
[data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"],
[data-testid="stSidebarCollapse"] svg, [data-testid="stSidebarCollapseButton"] svg,
[data-testid="stSidebarCollapsedControl"] svg, [data-testid="stExpandSidebarButton"] svg {
    color: var(--text-color) !important; fill: var(--text-color) !important;
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
/* Free-trial countdown pill under the username. */
.aud-trial {
    display: inline-block; margin: -8px 0 14px; padding: 4px 10px;
    border-radius: 999px; font-size: .78rem; font-weight: 600;
    color: var(--aud-accent-text); background: rgba(0, 230, 118, .10);
    border: 1px solid rgba(0, 230, 118, .35);
}

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
.aud-kpi.loss  .aud-kpi-value { color: var(--aud-loss); }
.aud-kpi.accent { border-color: rgba(0, 230, 118, .35); }
.aud-kpi.accent:hover {
    border-color: rgba(0, 230, 118, .7);
    box-shadow: 0 8px 24px rgba(0, 230, 118, .12);
}
.aud-kpi.accent .aud-kpi-value {
    color: var(--aud-accent-text); font-size: clamp(1.3rem, 5.5vw, 1.75rem); font-weight: 700;
}
/* Color-coded flow: Έσοδα always soft green, Έξοδα always soft red. Fixed
   hexes by design — the same look in BOTH themes, so the label/value colors
   are pinned too (the theme's muted grey would wash out on these). The two
   cards are REAL st.buttons (keys qe_card_income / qe_card_expense) dressed
   as KPI cards; tapping one toggles its inline quick-entry form. The recap
   (Ανασκόπηση) totals cards reuse these EXACT rules by suffixing the same
   keys (qe_card_income_recap / qe_card_expense_recap / qe_card_net_recap):
   every selector here matches on a class*= substring, so the suffixed keys
   inherit the full treatment with zero extra CSS. The div prefix keeps
   specificity above the generic button[kind="secondary"] rules below (same
   trick as the st-key-close_/st-key-del_ ghosts). */
div[class*="st-key-qe_card_"] button {
    width: 100%; min-height: 92px; border-radius: 16px; padding: 14px 20px;
    display: flex; flex-direction: column; align-items: flex-start;
    justify-content: center; text-align: left;
    transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease;
}
div[class*="st-key-qe_card_"] button:hover { transform: translateY(-2px); }
div[class*="st-key-qe_card_"] button:active { transform: scale(.985); }
/* The button label is two-paragraph markdown: caption line, then amount. */
div[class*="st-key-qe_card_"] button p { margin: 0; line-height: 1.2; }
div[class*="st-key-qe_card_"] button p:first-of-type {
    font-size: .8rem; font-weight: 500; margin-bottom: 6px;
}
div[class*="st-key-qe_card_"] button p:last-of-type {
    font-size: clamp(1.1rem, 4.5vw, 1.5rem); font-weight: 600;
    line-height: 1.15; white-space: nowrap;
}
div[class*="st-key-qe_card_income"] button { background: #d4edda; border: 1px solid #a9d8b8; }
div[class*="st-key-qe_card_income"] button p { color: #155724; }
div[class*="st-key-qe_card_income"] button p:first-of-type { color: #1e7e34; }
div[class*="st-key-qe_card_income"] button:hover {
    background: #d4edda; border-color: #6fbf8b;
    box-shadow: 0 8px 24px rgba(21, 87, 36, .18);
}
div[class*="st-key-qe_card_expense"] button { background: #f8d7da; border: 1px solid #efb2b9; }
div[class*="st-key-qe_card_expense"] button p { color: #721c24; }
div[class*="st-key-qe_card_expense"] button p:first-of-type { color: #a71d2a; }
div[class*="st-key-qe_card_expense"] button:hover {
    background: #f8d7da; border-color: #e2848f;
    box-shadow: 0 8px 24px rgba(114, 28, 36, .18);
}
/* Χρεωστούμενα: the 4th card is ALWAYS yellow (#f1c40f) in BOTH themes —
   same clickable-KPI treatment as Έσοδα/Έξοδα (key qe_card_debt); tapping
   it opens the debts panel (entry form + outstanding list). */
div[class*="st-key-qe_card_debt"] button { background: #fcf3cf; border: 1px solid #f1c40f; }
div[class*="st-key-qe_card_debt"] button p { color: #7d6608; }
div[class*="st-key-qe_card_debt"] button p:first-of-type { color: #9a7d0a; }
div[class*="st-key-qe_card_debt"] button:hover {
    background: #fcf3cf; border-color: #f1c40f;
    box-shadow: 0 8px 24px rgba(241, 196, 15, .25);
}
/* Καθαρό stays a static (non-clickable) card in the third column; its palette
   follows the SIGN of the value — net-pos / net-neg / net-zero appended by
   _net_kind. Height/centering mirror the buttons so the row lines up. */
div[class*="st-key-qe_card_net"] .aud-kpi-row { margin: 0; }
div[class*="st-key-qe_card_net"] .aud-kpi {
    min-height: 92px; display: flex; flex-direction: column; justify-content: center;
}
/* Static KPI palettes, shared by the dashboard Καθαρό card and the recap
   (Ανασκόπηση) totals row: revenue/net-pos soft green, expense/net-neg soft
   red — the same fixed hexes as the clickable cards — net-zero neutral. */
.aud-kpi.revenue, .aud-kpi.net-pos { background: #d4edda; border-color: #a9d8b8; }
.aud-kpi.revenue:hover, .aud-kpi.net-pos:hover {
    border-color: #6fbf8b; box-shadow: 0 8px 24px rgba(21, 87, 36, .18);
}
.aud-kpi.revenue .aud-kpi-label, .aud-kpi.net-pos .aud-kpi-label { color: #1e7e34; }
.aud-kpi.revenue .aud-kpi-value, .aud-kpi.net-pos .aud-kpi-value { color: #155724; }
.aud-kpi.expense, .aud-kpi.net-neg { background: #f8d7da; border-color: #efb2b9; }
.aud-kpi.expense:hover, .aud-kpi.net-neg:hover {
    border-color: #e2848f; box-shadow: 0 8px 24px rgba(114, 28, 36, .18);
}
.aud-kpi.expense .aud-kpi-label, .aud-kpi.net-neg .aud-kpi-label { color: #a71d2a; }
.aud-kpi.expense .aud-kpi-value, .aud-kpi.net-neg .aud-kpi-value { color: #721c24; }
.aud-kpi.net-zero { background: #f8f9fa; border-color: #d9dce1; }
.aud-kpi.net-zero .aud-kpi-label { color: #5a6472; }
.aud-kpi.net-zero .aud-kpi-value { color: #2f3a48; }

/* --- Project cards (st.container key="projcard_<id>") ----------------------
   The keyed container IS the surface card, so it always stretches to the
   full row width; the category info and the inline Αρχειοθέτηση pill live
   in columns INSIDE it (they stack naturally on narrow phones). */
div[class*="st-key-projcard_"], div[class*="st-key-clientcard_"] {
    width: 100%; background: var(--aud-surface); border: 1px solid var(--aud-border);
    border-radius: 16px; padding: 16px 20px; margin-bottom: 12px;
    transition: border-color .18s ease;
}
div[class*="st-key-projcard_"]:hover,
div[class*="st-key-clientcard_"]:hover { border-color: var(--aud-border-strong); }
.aud-proj-name { color: var(--aud-text); font-weight: 600; font-size: 1.02rem; margin-bottom: 10px; }
.aud-proj-stats { display: flex; gap: 26px; flex-wrap: wrap; }
.aud-proj-value { color: var(--aud-text-soft); font-size: .98rem; font-weight: 600; }
.aud-proj-value.accent { color: var(--aud-accent-text); }
.aud-proj-value.loss { color: var(--aud-loss); }

/* --- Client accounting block (5-metric grid + income-tax bar) --------------
   Rendered inside the same projcard_/clientcard_ surface. The grid auto-fits
   as many metric tiles per row as fit (min 140px), so it reflows cleanly from
   desktop (5 across) down to a single column on narrow phones. */
.aud-client-name {
    color: var(--aud-text); font-weight: 700; font-size: 1.05rem;
    margin-bottom: 12px; display: flex; align-items: center; gap: 10px;
    flex-wrap: wrap;
}
.aud-client-badge {
    font-size: .72rem; font-weight: 600; color: var(--aud-muted);
    background: var(--aud-bg); border: 1px solid var(--aud-border);
    border-radius: 999px; padding: 2px 9px;
}
.aud-metric-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px; margin-bottom: 4px;
}
.aud-metric {
    background: var(--aud-bg); border: 1px solid var(--aud-border);
    border-radius: 12px; padding: 11px 13px; min-width: 0;
}
.aud-metric-label {
    color: var(--aud-muted); font-size: .74rem; font-weight: 600;
    letter-spacing: .01em; margin-bottom: 5px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.aud-metric-value {
    color: var(--aud-text-soft); font-size: 1.12rem; font-weight: 700;
    line-height: 1.1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.aud-metric-value.accent { color: var(--aud-accent-text); }
.aud-metric-value.loss { color: var(--aud-loss); }
.aud-metric-value.debt { color: #b7950b; }
.aud-metric-value.vat-credit { color: var(--aud-accent-text); }
.aud-metric-sub {
    color: var(--aud-muted); font-size: .74rem; font-weight: 500; margin-top: 3px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

/* --- Income-tax progress bar (foundational placeholder) -------------------- */
.aud-tax { margin-top: 14px; }
.aud-tax-head {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 6px;
}
.aud-tax-title { color: var(--aud-text-soft); font-size: .82rem; font-weight: 600; }
.aud-tax-rate {
    font-size: .74rem; font-weight: 700; border-radius: 999px; padding: 2px 10px;
}
.aud-tax-rate.low { color: #1e7e34; background: rgba(0, 230, 118, .12);
                    border: 1px solid rgba(0, 230, 118, .35); }
.aud-tax-rate.high { color: #a71d2a; background: rgba(214, 58, 34, .12);
                     border: 1px solid rgba(214, 58, 34, .40); }
.aud-tax-track {
    height: 9px; border-radius: 999px; background: var(--aud-border);
    overflow: hidden;
}
.aud-tax-fill {
    height: 100%; border-radius: 999px; transition: width .3s ease;
    background: linear-gradient(90deg, #00c853, #00e676);
}
.aud-tax-fill.high { background: linear-gradient(90deg, #e67e22, #d63a22); }
.aud-tax-meta { color: var(--aud-muted); font-size: .76rem; margin-top: 6px; }

/* --- Close / edit category controls ------------------------------------------
   Small "Αρχειοθέτηση" and "Επεξεργασία" pills rendered INSIDE each category
   card (right column, stacked, right-aligned): quiet rounded outlines that
   warm on hover — Αρχειοθέτηση to the loss red, Επεξεργασία to the accent
   green — so they belong to the card instead of floating beside it.
   (editcat_ on purpose, NOT edit_: the inline editor's edit_rev_/edit_exp_
   text inputs must never match these button rules.) */
div[class*="st-key-close_"], div[class*="st-key-editcat_"] {
    margin: 0; display: flex; justify-content: flex-end;
}
div[class*="st-key-editcat_"] { margin-top: 6px; }
div[class*="st-key-close_"] button, div[class*="st-key-editcat_"] button {
    width: auto; min-height: 0;
    background: transparent; color: var(--aud-muted);
    border: 1px solid var(--aud-border-strong); border-radius: 999px;
    font-size: .78rem; font-weight: 600; padding: .34rem .85rem;
    white-space: nowrap; transition: all .18s ease;
}
div[class*="st-key-close_"] button:hover {
    color: var(--aud-loss); border-color: rgba(255, 107, 87, .55);
    background: rgba(255, 107, 87, .08);
}
div[class*="st-key-editcat_"] button:hover {
    color: var(--aud-accent-text); border-color: rgba(0, 230, 118, .55);
    background: rgba(0, 230, 118, .08);
}

/* --- Transaction rows (recap) + ghost delete button -------------------------
   Color-coded flow, same language as the KPI cards: income rows wear a green
   left border + green amount, expense rows red — no generic yellow accent. */
.aud-txn-row {
    display: flex; justify-content: space-between; align-items: baseline;
    gap: 12px; flex-wrap: wrap; padding: 9px 2px 9px 10px;
    border-bottom: 1px solid var(--aud-border); color: var(--aud-text-soft); font-size: .92rem;
}
.aud-txn-row.income { border-left: 3px solid rgba(0, 230, 118, .55); }
.aud-txn-row.expense { border-left: 3px solid rgba(255, 107, 87, .55); }
/* Outstanding-debt rows: always the fixed Χρεωστούμενα yellow (border
   #f1c40f; the amount uses the darker #b7950b so it reads on white too). */
.aud-txn-row.debt { border-left: 3px solid #f1c40f; }
.aud-proj-value.debt { color: #b7950b; }
.aud-txn-meta { color: var(--aud-text-soft); }
div[class*="st-key-del_"] button {
    background: transparent; color: var(--aud-muted); border: 1px dashed var(--aud-border-strong);
    font-size: .8rem; font-weight: 500; padding: .3rem .7rem;
}
div[class*="st-key-del_"] button:hover {
    color: var(--aud-loss); border-color: rgba(255, 107, 87, .55); background: transparent;
}
/* Εξόφληση ghost button on each outstanding-debt row: same quiet dashed
   pill as the delete ghost, but it warms to the Χρεωστούμενα yellow. */
div[class*="st-key-paid_"] button {
    background: transparent; color: var(--aud-muted); border: 1px dashed #f1c40f;
    font-size: .8rem; font-weight: 500; padding: .3rem .7rem; white-space: nowrap;
}
div[class*="st-key-paid_"] button:hover {
    color: #b7950b; border-color: #f1c40f; background: rgba(241, 196, 15, .08);
}

/* --- Recap table ------------------------------------------------------------
   st.dataframe draws its cells on a <canvas> from Streamlit's NATIVE theme
   — CSS can't recolor a canvas, so it sat stuck on dark grey in Light
   Mode. The recap renders a plain HTML table instead (.aud-table), which
   lives entirely on the active palette. */
.aud-table-wrap {
    overflow-x: auto; border: 1px solid var(--aud-border); border-radius: 12px;
    margin: 4px 0 14px; background: var(--background-color);
}
.aud-table { width: 100%; border-collapse: collapse; font-size: .9rem; }
.aud-table th {
    background: var(--secondary-background-color); color: var(--aud-muted);
    font-weight: 600; text-align: left; padding: 10px 14px; white-space: nowrap;
    border-bottom: 1px solid var(--aud-border-strong);
}
.aud-table td {
    background: var(--background-color); color: var(--text-color);
    padding: 9px 14px; border-bottom: 1px solid var(--aud-border); white-space: nowrap;
}
.aud-table tr:last-child td { border-bottom: none; }
/* Money columns (Έσοδα/Έξοδα/Καθαρό) read best right-aligned. */
.aud-table th:nth-child(n+3), .aud-table td:nth-child(n+3) { text-align: right; }

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
/* Paywall variant: a billing gateway, not an error — mint instead of red. */
.aud-lock.aud-paywall { border-color: rgba(0, 230, 118, .35); margin-bottom: 16px; }
.aud-lock.aud-paywall .aud-lock-icon {
    background: rgba(0, 230, 118, .12); color: var(--aud-accent-text);
}
.aud-lock-price {
    color: var(--aud-accent-text); font-weight: 800; font-size: 1.35rem; margin-top: 14px;
}

/* --- Buttons (st.button, form submits, and st.link_button anchors) -------- */
.stButton > button, .stLinkButton > a, [data-testid="stFormSubmitButton"] > button {
    border-radius: 12px; font-weight: 600; padding: .62rem 1rem;
    transition: all .18s ease; text-decoration: none;
}
button[kind="secondary"], button[data-testid="stBaseButton-secondaryFormSubmit"],
a[data-testid="stBaseLinkButton-secondary"] {
    background: var(--aud-surface); color: var(--aud-text-soft); border: 1px solid var(--aud-border);
}
button[kind="secondary"]:hover, button[data-testid="stBaseButton-secondaryFormSubmit"]:hover,
a[data-testid="stBaseLinkButton-secondary"]:hover {
    border-color: var(--aud-muted); color: var(--aud-text);
}
button[kind="primary"], button[data-testid="stBaseButton-primaryFormSubmit"],
a[data-testid="stBaseLinkButton-primary"] {
    background: var(--aud-accent) !important; border: none !important;
}
button[kind="primary"], button[data-testid="stBaseButton-primaryFormSubmit"],
a[data-testid="stBaseLinkButton-primary"],
button[kind="primary"] *, button[data-testid="stBaseButton-primaryFormSubmit"] *,
a[data-testid="stBaseLinkButton-primary"] * {
    color: #121214 !important;  /* dark label on the mint fill in BOTH themes */
}
button[kind="primary"]:hover, button[data-testid="stBaseButton-primaryFormSubmit"]:hover,
a[data-testid="stBaseLinkButton-primary"]:hover {
    filter: brightness(1.06);
    box-shadow: 0 0 0 1px rgba(0, 230, 118, .45), 0 6px 22px rgba(0, 230, 118, .28);
}
button[kind="primary"]:active, a[data-testid="stBaseLinkButton-primary"]:active {
    transform: scale(.985);
}
/* Tertiary = text links ("🔑 Ξέχασα τον κωδικό μου", "📝 … Εγγραφή εδώ"):
   no fill, no border, soft text that turns mint on hover. */
button[kind="tertiary"] {
    background: transparent !important; border: none !important;
    color: var(--aud-text-soft) !important; font-weight: 500;
    box-shadow: none !important;
}
button[kind="tertiary"]:hover {
    color: var(--aud-accent) !important; background: transparent !important;
}

/* --- Script-carrier iframes ---------------------------------------------- */
/* The localStorage session helpers and the sidebar auto-collapse listener
   ride st.iframe(height=1); a blank iframe document paints WHITE against
   the dark canvas — the "glitch sliver" at the top of the page. Hide their
   whole element container: display:none iframes still load and execute
   their <script>, which is the only reason they exist. */
div[data-testid="stElementContainer"]:has(iframe[height="1"]) {
    display: none;
}
iframe[height="1"] {  /* belt-and-braces if the container testid renames */
    display: none;
}

/* --- Inputs ------------------------------------------------------------- */
div[data-baseweb="input"], div[data-baseweb="textarea"], div[data-baseweb="select"] > div {
    background: var(--background-color) !important; border-color: var(--aud-border) !important;
    border-radius: 12px !important;
}
/* The INNER nodes (base-input wrapper + the <input>/<textarea> themselves)
   carry their own background from Streamlit's NATIVE theme (pinned dark in
   config.toml) — without repainting them, Light Mode shows dark boxes
   inside white shells. var(--background-color)/var(--text-color) are
   aliased to the active palette in _inject_css, so both modes just work. */
div[data-baseweb="input"] > div, div[data-baseweb="base-input"],
div[data-baseweb="input"] input, div[data-baseweb="textarea"] textarea,
div[data-baseweb="select"] input {
    background: var(--background-color) !important;
    color: var(--text-color) !important;
}
/* Icon buttons living inside inputs (password reveal eye, clear ✕): the
   native theme paints them white — invisible on a white field. */
div[data-baseweb="input"] button, div[data-baseweb="input"] svg {
    color: var(--aud-muted) !important; fill: var(--aud-muted) !important;
}
div[data-baseweb="input"]:focus-within, div[data-baseweb="textarea"]:focus-within,
div[data-baseweb="select"] > div:focus-within {
    border-color: var(--aud-accent) !important;
}
/* Selectboxes (incl. the category picker with "➕ Δημιουργία Νέας...") are
   pickers, not text fields: baseweb's combobox ships a text I-beam over its
   inner input and arrow — force the clickable hand on every part of the
   control and on the dropdown options, so it reads as a button. */
.stSelectbox [data-baseweb="select"], .stSelectbox [data-baseweb="select"] *,
div[data-baseweb="select"], div[data-baseweb="select"] *,
div[data-baseweb="select"] input, div[data-baseweb="select"] svg,
ul[data-baseweb="menu"] li, ul[data-baseweb="menu"] li * {
    cursor: pointer !important;
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
    /* Trailing no-break space (\\a0): if a build ever renders the two
       pseudo-elements on one line, the copy still reads "…εδώ PDF…",
       never "…εδώPDF" glued together. */
    content: 'Σύρετε το αρχείο εδώ\\a0'; display: block;
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

/* --- Sidebar ------------------------------------------------------------- */
[data-testid="stSidebar"] { background: var(--aud-sidebar); border-right: 1px solid var(--aud-border); }

/* --- Sidebar navigation (st.radio key="nav_page" dressed as a menu) --------
   The radio replaces the old top st.tabs row. Each option becomes a full-
   width rounded menu item: the native radio dot is hidden, hover gets a
   surface fill, and the checked item wears the mint accent border. */
div[class*="st-key-nav_page"] div[role="radiogroup"] {
    display: flex; flex-direction: column; gap: 6px;
}
div[class*="st-key-nav_page"] label[data-baseweb="radio"] {
    width: 100%; margin: 0; padding: 11px 14px; border-radius: 12px;
    background: transparent; border: 1px solid transparent; cursor: pointer;
    display: flex; align-items: center;
    transition: background .15s ease, border-color .15s ease;
}
div[class*="st-key-nav_page"] label[data-baseweb="radio"]:hover {
    background: var(--aud-surface); border-color: var(--aud-border);
}
/* The first div inside the label is the radio dot — hide it. */
div[class*="st-key-nav_page"] label[data-baseweb="radio"] > div:first-of-type {
    display: none;
}
div[class*="st-key-nav_page"] label[data-baseweb="radio"] p {
    color: var(--aud-text-soft); font-size: .95rem; font-weight: 500;
}
div[class*="st-key-nav_page"] label[data-baseweb="radio"]:has(input:checked) {
    background: var(--aud-surface); border-color: rgba(0, 230, 118, .45);
    box-shadow: 0 2px 12px rgba(0, 230, 118, .10);
}
div[class*="st-key-nav_page"] label[data-baseweb="radio"]:has(input:checked) p {
    color: var(--aud-text); font-weight: 600;
}

/* --- Archived-categories toggle (st.button key="archived_toggle") ----------
   A large surface button matching the KPI-card aesthetic; tapping it shows/
   hides the archived list in place. */
div[class*="st-key-archived_toggle"] button {
    width: 100%; min-height: 64px; border-radius: 16px; padding: 16px 20px;
    background: var(--aud-surface); border: 1px solid var(--aud-border);
    color: var(--aud-text-soft); font-weight: 600; font-size: 1rem;
    justify-content: flex-start; text-align: left;
    transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease;
}
div[class*="st-key-archived_toggle"] button:hover {
    transform: translateY(-2px); border-color: var(--aud-border-strong);
    box-shadow: 0 8px 24px var(--aud-shadow);
    background: var(--aud-surface); color: var(--aud-text);
}
div[class*="st-key-archived_toggle"] button:active { transform: scale(.985); }

/* --- Tooltips ------------------------------------------------------------ */
/* Hover tooltips (help= on buttons/inputs, e.g. "Πατήστε για προσθήκη
   εσόδου") render in a body-level portal painted by the NATIVE theme —
   they stayed near-black with dark text in Light Mode. Re-anchor every
   layer (BaseWeb tooltip, Streamlit's testid, generic role) to the active
   palette; covers the inner markdown too. */
div[data-baseweb="tooltip"], [data-testid="stTooltipContent"], [role="tooltip"] {
    background: var(--background-color) !important;
    color: var(--text-color) !important;
    border: 1px solid var(--aud-border-strong) !important;
    border-radius: 10px !important;
    box-shadow: 0 6px 20px var(--aud-shadow) !important;
}
div[data-baseweb="tooltip"] *, [data-testid="stTooltipContent"] *,
[role="tooltip"] [data-testid="stMarkdownContainer"] p {
    color: var(--text-color) !important;
}

/* --- Alerts ------------------------------------------------------------- */
[data-testid="stAlert"] { border-radius: 12px; }

/* --- Πληρωμές (billing center) cards --------------------------------------
   st.container(key="pay_card_...") wrappers dressed as surface cards. */
div[class*="st-key-pay_card_"] {
    background: var(--aud-surface); border: 1px solid var(--aud-border);
    border-radius: 16px; padding: 22px 22px 18px;
    transition: border-color .18s ease, box-shadow .18s ease;
}
div[class*="st-key-pay_card_"]:hover {
    border-color: var(--aud-border-strong); box-shadow: 0 8px 24px var(--aud-shadow);
}
.aud-pay-title { color: var(--aud-text); font-weight: 700; font-size: 1.05rem; margin-bottom: 4px; }
.aud-pay-badge { color: var(--aud-accent-text); font-weight: 800; font-size: 1.3rem; margin-bottom: 8px; }
.aud-pay-text { color: var(--aud-muted); font-size: .9rem; line-height: 1.6; margin-bottom: 4px; }

/* --- Mobile: compact 2x2 KPI grid ------------------------------------------
   On phones Streamlit stacks st.columns into ONE long full-width column, so
   the four header cards (Έσοδα / Έξοδα / Καθαρό / Χρεωστούμενα) swallowed a
   whole screen. Scope via :has() to ONLY the horizontal block that carries
   the qe_card_ widgets and pin its columns to half width — a 2x2 grid —
   then tighten the cards' padding and type. Colors, borders and radii keep
   riding the same desktop rules (palette vars + fixed flow hexes), so
   light/dark alignment is untouched. Both stColumn/column testids covered
   for older Streamlit builds. */
@media (max-width: 768px) {
    div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-qe_card_"]) {
        flex-wrap: wrap !important; gap: 10px !important;
    }
    div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-qe_card_"]) > div[data-testid="stColumn"],
    div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-qe_card_"]) > div[data-testid="column"] {
        flex: 1 1 calc(50% - 5px) !important;
        width: calc(50% - 5px) !important;
        min-width: calc(50% - 5px) !important;
        max-width: calc(50% - 5px) !important;
    }
    /* Compact cards: shorter, tighter, smaller type — the label stays on one
       line so all four cards keep equal heights. */
    div[class*="st-key-qe_card_"] button {
        min-height: 68px; padding: 10px 12px; border-radius: 14px;
    }
    div[class*="st-key-qe_card_"] button p:first-of-type {
        font-size: .72rem; margin-bottom: 3px; white-space: nowrap;
    }
    div[class*="st-key-qe_card_"] button p:last-of-type {
        font-size: clamp(.95rem, 4.2vw, 1.2rem);
    }
    /* The static Καθαρό card mirrors the buttons' compact metrics. */
    div[class*="st-key-qe_card_net"] .aud-kpi {
        min-height: 68px; padding: 10px 12px; border-radius: 14px;
    }
    div[class*="st-key-qe_card_net"] .aud-kpi-label {
        font-size: .72rem; margin-bottom: 3px; white-space: nowrap;
    }
    div[class*="st-key-qe_card_net"] .aud-kpi-value,
    div[class*="st-key-qe_card_net"] .aud-kpi.accent .aud-kpi-value {
        font-size: clamp(.95rem, 4.2vw, 1.2rem);
    }
    /* Static KPI rows elsewhere (recap totals) tighten to match. */
    .aud-kpi-row { gap: 10px; }
    .aud-kpi { padding: 12px 14px; border-radius: 14px; }
}
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
    # Also re-point Streamlit's OWN theme variables at the active palette.
    # config.toml pins the native theme to dark (so BaseWeb popovers match
    # the default look), which left native-styled surfaces — inner input
    # nodes, tooltips, header icons — stuck dark in Light Mode. Our :root
    # block is injected after Streamlit's, so these aliases win, and every
    # rule written against var(--background-color) / var(--text-color)
    # follows the sidebar toggle.
    root += (f"--background-color: {palette['surface']};"
             f"--secondary-background-color: {palette['bg']};"
             f"--text-color: {palette['text']};")
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


# The pure financial helpers below are re-exported from the shared `finance`
# module under the app's existing private names, so every call site is
# unchanged while Streamlit and the FastAPI web backend compute from ONE source
# of truth (the VAT-summation guarantee can never drift between them). Only the
# display/HTML helpers (_money, _net_kind, the *_block renderers …) stay here.
_amount = finance.amount
_is_revenue = finance.is_revenue
DEBT_TYPE = finance.DEBT_TYPE
_is_debt = finance.is_debt
_split_debts = finance.split_debts
_sum_by_type = finance.sum_by_type
_vat_of = finance.vat_of
_vat_for_write = finance.vat_for_write
_txn_vat = finance.txn_vat
_vat_by_type = finance.vat_by_type
_debts_by_client = finance.debts_by_client
_client_metrics = finance.client_metrics
_transactions_by_category = finance.transactions_by_category
_project_financials = finance.project_financials
_closed_date = finance.closed_date
_txn_date = finance.txn_date


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
    {"", "accent", "loss", "revenue", "expense",
     "net-pos", "net-neg", "net-zero"}."""
    cards = "".join(
        f'<div class="aud-kpi {kind}">'
        f'<div class="aud-kpi-label">{html.escape(label)}</div>'
        f'<div class="aud-kpi-value">{html.escape(value)}</div>'
        f"</div>"
        for label, value, kind in items
    )
    st.markdown(f'<div class="aud-kpi-row">{cards}</div>', unsafe_allow_html=True)


def _net_kind(net):
    """Καθαρό card palette from the SIGN of the value: net-pos (soft green),
    net-neg (soft red), net-zero (neutral). Rounded to cents first so float
    dust can't miss the == 0 style."""
    net_r = round(net, 2)
    return "net-pos" if net_r > 0 else "net-neg" if net_r < 0 else "net-zero"


def _net_value_kind(net):
    """Text color for an inline Καθαρό VALUE (the category cards): green
    when positive, red when negative, and "" — the default text color —
    at exactly zero. Same round-to-cents guard as _net_kind."""
    net_r = round(net, 2)
    return "accent" if net_r > 0 else "loss" if net_r < 0 else ""


def _metric_tile(label, value, kind="", sub=None):
    """One tile in a client's metric grid: muted label, colored main value
    (kind ∈ {"", "accent", "loss", "debt", "vat-credit"}), optional muted
    subvalue line (e.g. the net-of-VAT figure under a gross one)."""
    sub_html = (f'<div class="aud-metric-sub">{html.escape(sub)}</div>'
                if sub else "")
    return (f'<div class="aud-metric">'
            f'<div class="aud-metric-label">{html.escape(label)}</div>'
            f'<div class="aud-metric-value {kind}">{html.escape(value)}</div>'
            f'{sub_html}</div>')


def _tax_progress_html(taxable):
    """Foundational per-client income-tax progress bar: net taxable income
    (net-of-VAT profit) filling toward TAX_BRACKET_LIMIT, flipping status and
    colour once it crosses from the low (22 %) bracket into the high (29 %)
    one. A deliberate placeholder for the full Greek scale."""
    limit = TAX_BRACKET_LIMIT
    low = f"{int(round(TAX_RATE_LOW * 100))}%"
    high = f"{int(round(TAX_RATE_HIGH * 100))}%"
    pct = min(max(taxable, 0.0) / limit, 1.0) * 100 if limit > 0 else 0.0
    if taxable <= 0:
        cls, rate_label = "low", low
        status = "Χωρίς φορολογητέο εισόδημα ακόμη"
    elif taxable >= limit:
        cls, rate_label = "high", high
        status = f"Υπέρβαση ορίου {_money(limit)} — κλίμακα {high}"
    else:
        cls, rate_label = "low", low
        status = f"Εντός κλίμακας {low} (όριο {_money(limit)})"
    return (
        f'<div class="aud-tax">'
        f'<div class="aud-tax-head">'
        f'<span class="aud-tax-title">Μπάρα Φόρου Εισοδήματος</span>'
        f'<span class="aud-tax-rate {cls}">{html.escape(rate_label)}</span>'
        f'</div>'
        f'<div class="aud-tax-track">'
        f'<div class="aud-tax-fill {cls}" style="width:{pct:.1f}%"></div>'
        f'</div>'
        f'<div class="aud-tax-meta">'
        f'{html.escape(_money(taxable))} / {html.escape(_money(limit))} · '
        f'{html.escape(status)}</div>'
        f'</div>'
    )


def _client_metrics_block(name, m, archived_label=None, with_name=True):
    """The full accounting block for ONE client (Dashboard & Ανασκόπηση): a
    structured grid of the 5 core metrics — Έσοδα (gross & net), Έξοδα (gross &
    net), Φ.Π.Α. (net balance), Χρεωστούμενα, Καθαρό Αποτέλεσμα — plus the
    income-tax progress bar. Card chrome lives on the keyed st.container
    (st-key-projcard_ / st-key-clientcard_).

    `with_name` prepends the client name (+ optional «Αρχειοθετήθηκε …» badge);
    the dashboard renders the name alongside its action pills instead and calls
    with with_name=False. All euro values render as magnitudes, colour-coded by
    sign, matching the rest of the app."""
    header = ""
    if with_name:
        badge = (f'<span class="aud-client-badge">{html.escape(archived_label)}</span>'
                 if archived_label else "")
        header = f'<div class="aud-client-name">{html.escape(name)}{badge}</div>'
    vat = m["net_vat"]
    vat_kind = "vat-credit" if round(vat, 2) < 0 else ""
    vat_sub = ("προς επιστροφή" if round(vat, 2) < 0
               else "προς απόδοση" if round(vat, 2) > 0 else "μηδενικό")
    tiles = "".join([
        _metric_tile("Έσοδα", _money(m["gross_rev"]), "accent",
                     sub=f"Καθαρά {_money(m['net_rev'])}"),
        _metric_tile("Έξοδα", _money(m["gross_exp"]), "loss",
                     sub=f"Καθαρά {_money(m['net_exp'])}"),
        _metric_tile("Φ.Π.Α.", _money(vat), vat_kind, sub=vat_sub),
        _metric_tile("Χρεωστούμενα", _money(m["debt"]), "debt"),
        _metric_tile("Καθαρό Αποτέλεσμα", _money(m["net_profit"]),
                     _net_value_kind(m["net_profit"])),
    ])
    st.markdown(
        f'{header}'
        f'<div class="aud-metric-grid">{tiles}</div>'
        f'{_tax_progress_html(m["taxable"])}',
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
#
# Second safety net: the token is ALSO mirrored into the browser's
# localStorage (_persist_browser_session). Rapid navigation clicks can drop
# the WebSocket; Streamlit then reconnects with a brand-new empty
# st.session_state, and if the ?session= param didn't survive the reconnect
# the user used to be dumped on the login screen. Now the login screen first
# checks localStorage (_sync_browser_session): if a token is stored there,
# a tiny script reloads the page with ?session=<token> re-attached and the
# server-side restore takes over. Explicit logout wipes BOTH stores.
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
        # The flag makes _sync_browser_session wipe the localStorage copy
        # too, otherwise it would keep reloading the page with this same
        # dead token forever.
        _session_store().pop(token, None)
        del st.query_params["session"]
        st.session_state["_browser_session_dead"] = True
        return
    entry["expires"] = now + SESSION_TTL_SECONDS  # sliding renewal
    st.session_state["username"] = entry["username"]
    st.session_state["session_token"] = token
    # The subscription verdict is NOT restored — subscription_gate re-checks
    # it once against Airtable, so a refresh can't outlive a cancelled plan.


# The localStorage key holding the session token, and a sessionStorage
# marker that stops the restore script from reload-looping on a token the
# server keeps rejecting (e.g. after a server restart emptied the store).
_LS_TOKEN_KEY = "aud_session_token"
_SS_ATTEMPT_KEY = "aud_restore_attempted"


def _persist_browser_session():
    """Mirror the live token into localStorage (runs on every logged-in
    render). st.iframe scripts only execute when the iframe MOUNTS (see
    _install_sidebar_autocollapse), which is exactly enough here: a fresh
    page load or a post-login rerun mounts it once, and re-writing the same
    token on later reruns would be a no-op anyway. Also re-arms the restore
    guard so a future reconnect may try this token."""
    token = st.session_state.get("session_token")
    if not token:
        return
    st.session_state.pop("_browser_session_dead", None)
    st.iframe(
        f"""
        <script>
        (function () {{
            try {{
                window.parent.localStorage.setItem(
                    {json.dumps(_LS_TOKEN_KEY)}, {json.dumps(token)});
                window.parent.sessionStorage.removeItem(
                    {json.dumps(_SS_ATTEMPT_KEY)});
            }} catch (e) {{}}
        }})();
        </script>
        """,
        height=1,
    )


def _clear_browser_session_script():
    st.iframe(
        f"""
        <script>
        (function () {{
            try {{
                window.parent.localStorage.removeItem({json.dumps(_LS_TOKEN_KEY)});
                window.parent.sessionStorage.removeItem({json.dumps(_SS_ATTEMPT_KEY)});
            }} catch (e) {{}}
        }})();
        </script>
        """,
        height=1,
    )


def _sync_browser_session():
    """Runs just before the login screen renders (i.e. st.session_state has
    no username). Two jobs:

    1. If the user explicitly logged out, or the URL carried a token the
       server rejected, wipe the localStorage copy so it can't resurrect
       the session.
    2. Otherwise, if the browser still remembers a token — the case where a
       rapid-click WebSocket reconnect handed us a brand-new empty
       st.session_state — reload the page with ?session=<token> attached so
       _restore_session hydrates it, instead of showing the login screen.
       The sessionStorage marker guarantees at most ONE reload attempt per
       token per tab, so a dead token can never loop."""
    if (st.session_state.pop("_clear_browser_session", None)
            or st.session_state.get("_browser_session_dead")):
        _clear_browser_session_script()
        return
    st.iframe(
        f"""
        <script>
        (function () {{
            try {{
                const root = window.parent;
                const token = root.localStorage.getItem({json.dumps(_LS_TOKEN_KEY)});
                if (!token) return;
                if (root.sessionStorage.getItem({json.dumps(_SS_ATTEMPT_KEY)}) === token) return;
                root.sessionStorage.setItem({json.dumps(_SS_ATTEMPT_KEY)}, token);
                const url = new URL(root.location.href);
                url.searchParams.set("session", token);
                root.location.replace(url.toString());
            }} catch (e) {{}}
        }})();
        </script>
        """,
        height=1,
    )


# --------------------------------------------------------------------------
# Login (tenant context + password check)
# --------------------------------------------------------------------------
def _password_matches(stored, typed):
    """Compare the Users-table Password column against the typed password.

    Two accepted cell formats:
      - pbkdf2_sha256$… hash (passwords.py) — the typed password is hashed
        with the cell's own salt/iterations and the digests are compared.
      - Legacy plaintext — direct comparison, kept so pre-hashing accounts
        still log in; login_screen rewrites the cell as a hash right after.
    Both comparisons are constant-time. The column may be Text or Number in
    Airtable; a numeric cell comes back as int/float, so it is normalized to
    a string. Comparison is case-sensitive. An empty or missing stored
    password never matches (fail closed).
    """
    if stored is None:
        return False
    if isinstance(stored, float) and stored.is_integer():
        stored = int(stored)
    stored = str(stored).strip()
    typed = typed.strip()
    if not stored:
        return False
    if passwords.is_hashed(stored):
        return passwords.verify_password(stored, typed)
    return secrets.compare_digest(stored.encode("utf-8"),
                                  typed.encode("utf-8"))


def login_screen():
    st.markdown('<div class="aud-brand">Μπακαλο<span>χαρτο</span></div>',
                unsafe_allow_html=True)
    st.markdown('<div class="aud-tagline">Οικονομικός έλεγχος για τη '
                "σύγχρονη επιχείρηση</div>", unsafe_allow_html=True)

    # "Ξέχασα τον κωδικό μου" / "Εγγραφή" replace the login form while active.
    if st.session_state.get("reset_stage"):
        _password_reset_screen()
        return
    if st.session_state.get("show_register"):
        _register_screen()
        return

    _, mid, _ = st.columns([1, 1.7, 1])
    with mid:
        # Verdict left behind by a finished reset/registration flow.
        flash = st.session_state.pop("login_flash", None)
        if flash:
            kind, text = flash
            (st.success if kind == "success" else st.error)(text)
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
        if st.button("🔑 Ξέχασα τον κωδικό μου", type="tertiary",
                     width="stretch"):
            st.session_state["reset_stage"] = "request"
            st.rerun()
        if st.button("📝 Δεν έχετε λογαριασμό; Εγγραφή εδώ", type="tertiary",
                     width="stretch"):
            st.session_state["show_register"] = True
            st.rerun()
    if submitted:
        username = username.strip()
        password = password.strip()
        with mid:
            if not username or not password:
                st.error("Παρακαλώ συμπληρώστε το Όνομα Χρήστη και τον "
                         "Κωδικό Πρόσβασης.")
                return
            try:
                user = db.find_user(username=username)
            except db.AirtableError as exc:
                print(f"[WARN] Login lookup failed for {username!r}: {exc}")
                st.error("Ο έλεγχος του λογαριασμού σας απέτυχε προσωρινά. "
                         "Παρακαλώ δοκιμάστε ξανά σε λίγο.")
                return
            record = user["fields"] if user else {}
            if user is None or not _password_matches(record.get("Password"), password):
                st.error("❌ Το Όνομα Χρήστη ή ο Κωδικός Πρόσβασης είναι "
                         "εσφαλμένα.")
                return
        # Transparent migration: a legacy plaintext Password cell that just
        # verified is rewritten as its pbkdf2_sha256 hash, so the plaintext
        # disappears from Airtable on each account's first login. Best
        # effort — a failed write (offline, Number-typed column) only logs;
        # the login proceeds and migration retries next time.
        if not passwords.is_hashed(str(record.get("Password") or "")):
            try:
                db.update_user_password(user["id"],
                                        passwords.hash_password(password))
            except db.AirtableError as exc:
                print(f"[WARN] Password hash migration failed for "
                      f"{username!r}: {exc}")
        # Credentials verified — reuse the same Users row for the
        # subscription verdict so login stays a single Airtable call, and
        # mint the refresh-survival token (see _session_store above).
        st.session_state["username"] = username
        st.session_state["session_token"] = _issue_session_token(username)
        st.session_state["subscription_verified"] = True
        st.session_state["subscription_status"] = record.get("SubscriptionStatus")
        st.session_state["trial_expiry"] = record.get("TrialExpiry")
        st.session_state["user_record_id"] = user["id"]
        st.rerun()


# --------------------------------------------------------------------------
# Password reset ("Ξέχασα τον κωδικό μου")
# --------------------------------------------------------------------------
# Two-stage flow on the login screen, backed by the Users columns ResetToken
# (6-digit code) + ResetTokenExpiry (UTC ISO). Stage "request": type the
# account Email -> a code is written to the row and emailed via smtplib
# (Gmail App Password, see config.py). Stage "verify": type the code + the
# new password -> on match-and-not-expired the Password cell gets the
# pbkdf2 hash and both reset columns are cleared. Anti-enumeration: an
# unknown email shows the exact same "code sent" message and the same
# failing verify form. Anti-brute-force: 5 wrong codes revoke the token.
_MAX_RESET_ATTEMPTS = 5
_RESET_STATE_KEYS = ("reset_stage", "reset_email", "reset_record_id",
                     "reset_attempts")


def _clear_reset_state():
    for key in _RESET_STATE_KEYS:
        st.session_state.pop(key, None)


def _send_reset_email(to_addr, code):
    """Email the 6-digit code with stdlib smtplib (implicit-TLS Gmail)."""
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        raise RuntimeError(
            "SMTP_EMAIL / SMTP_PASSWORD are not configured — password-reset "
            "emails cannot be sent (see config.py)."
        )
    msg = EmailMessage()
    msg["Subject"] = "Μπακαλοχαρτο — Επαναφορά κωδικού πρόσβασης"
    msg["From"] = SMTP_EMAIL
    msg["To"] = to_addr
    msg.set_content(
        "Γεια σας,\n\n"
        "Ζητήθηκε επαναφορά κωδικού για τον λογαριασμό σας στο "
        "Μπακαλοχαρτο.\n\n"
        f"Κωδικός επαλήθευσης: {code}\n\n"
        f"Ο κωδικός ισχύει για {RESET_CODE_TTL_MINUTES} λεπτά. Αν δεν "
        "ζητήσατε εσείς την επαναφορά, αγνοήστε αυτό το μήνυμα — ο κωδικός "
        "πρόσβασής σας δεν έχει αλλάξει.\n\n"
        "Μπακαλοχαρτο"
    )
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
        smtp.login(SMTP_EMAIL, SMTP_PASSWORD)
        smtp.send_message(msg)


def _normalize_reset_token(value):
    """Airtable may hand the stored code back as int/float (Number-typed
    column, which drops leading zeros) — zero-pad back to 6 digits."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        return str(value).zfill(6)
    return str(value).strip()


def _parse_utc_datetime(value):
    """UTC datetime from an Airtable date cell (ResetTokenExpiry,
    TrialExpiry), or None when unusable (missing/garbled). Reset treats
    None as expired (fail closed); the trial gate treats None as "no trial
    running" — a paying account simply has no expiry date."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _password_reset_screen():
    _, mid, _ = st.columns([1, 1.7, 1])
    with mid:
        if st.session_state.get("reset_stage") == "verify":
            _reset_verify_form()
        else:
            _reset_request_form()
        if st.button("← Επιστροφή στη σύνδεση", width="stretch"):
            _clear_reset_state()
            st.rerun()


def _reset_request_form():
    st.markdown("#### 🔑 Επαναφορά κωδικού")
    st.caption("Εισάγετε το email του λογαριασμού σας και θα σας στείλουμε "
               "έναν 6-ψήφιο κωδικό επαλήθευσης.")
    with st.form("reset_request"):
        email = st.text_input("Email", placeholder="you@example.com")
        submitted = st.form_submit_button("Αποστολή κωδικού επαλήθευσης",
                                          type="primary", width="stretch")
    if not submitted:
        return
    email = email.strip()
    if "@" not in email:
        st.error("Παρακαλώ εισάγετε ένα έγκυρο email.")
        return
    try:
        user = db.find_user(email=email)
    except db.AirtableError as exc:
        print(f"[WARN] Reset lookup failed for {email!r}: {exc}")
        st.error("Ο έλεγχος απέτυχε προσωρινά. Παρακαλώ δοκιμάστε ξανά "
                 "σε λίγο.")
        return
    record_id = None
    if user:
        # secrets.randbelow: crypto-grade; zero-padded so "042153" works.
        code = f"{secrets.randbelow(10**6):06d}"
        expiry = (datetime.now(timezone.utc)
                  + timedelta(minutes=RESET_CODE_TTL_MINUTES))
        try:
            db.set_reset_token(user["id"], code,
                               expiry.strftime("%Y-%m-%dT%H:%M:%SZ"))
            _send_reset_email(email, code)
        except (db.AirtableError, RuntimeError, OSError) as exc:
            # OSError covers every smtplib/socket failure. The token may
            # already be written; it just expires unused.
            print(f"[WARN] Reset code delivery failed for {email!r}: {exc}")
            st.error("Η αποστολή του email απέτυχε προσωρινά. Παρακαλώ "
                     "δοκιμάστε ξανά σε λίγο.")
            return
        record_id = user["id"]
    # Unknown email falls through to the SAME message and the SAME verify
    # form (which can only fail) — outsiders can't probe which emails have
    # accounts. Only the server-side session knows whether a row matched.
    st.session_state["reset_stage"] = "verify"
    st.session_state["reset_email"] = email
    st.session_state["reset_record_id"] = record_id
    st.session_state["reset_attempts"] = 0
    st.rerun()


def _reset_verify_form():
    email = st.session_state.get("reset_email", "")
    st.markdown("#### 🔑 Επαλήθευση")
    st.caption(f"Αν το **{email}** αντιστοιχεί σε λογαριασμό, του στείλαμε "
               f"έναν 6-ψήφιο κωδικό (ισχύει {RESET_CODE_TTL_MINUTES} "
               "λεπτά). Ελέγξτε και τον φάκελο Ανεπιθύμητα (Spam).")
    with st.form("reset_verify"):
        code = st.text_input("Κωδικός επαλήθευσης (6 ψηφία)", max_chars=6,
                             placeholder="123456")
        new_pw = st.text_input("Νέος Κωδικός Πρόσβασης", type="password",
                               placeholder="••••••••")
        submitted = st.form_submit_button("Αλλαγή Κωδικού", type="primary",
                                          width="stretch")
    if not submitted:
        return
    code = code.strip()
    new_pw = new_pw.strip()
    if not re.fullmatch(r"\d{6}", code):
        st.error("Ο κωδικός επαλήθευσης αποτελείται από 6 ψηφία.")
        return
    if len(new_pw) < 6:
        st.error("Ο νέος κωδικός πρέπει να έχει τουλάχιστον 6 χαρακτήρες.")
        return

    # Re-read the row fresh so we verify against what's in Airtable NOW —
    # never against anything cached client-side. record_id is None for an
    # unknown email; fields stays empty and verification simply fails.
    record_id = st.session_state.get("reset_record_id")
    fields = {}
    if record_id:
        try:
            user = db.find_user(email=email)
        except db.AirtableError as exc:
            print(f"[WARN] Reset verify lookup failed for {email!r}: {exc}")
            st.error("Ο έλεγχος απέτυχε προσωρινά. Παρακαλώ δοκιμάστε ξανά "
                     "σε λίγο.")
            return
        if user and user["id"] == record_id:
            fields = user["fields"]

    stored = fields.get("ResetToken")
    code_matches = stored is not None and secrets.compare_digest(
        _normalize_reset_token(stored), code)

    if code_matches:
        expiry = _parse_utc_datetime(fields.get("ResetTokenExpiry"))
        if expiry is None or expiry < datetime.now(timezone.utc):
            try:
                db.clear_reset_token(record_id)
            except db.AirtableError:
                pass  # already unusable; it stays expired either way
            st.error("Ο κωδικός επαλήθευσης έληξε. Πατήστε «Επιστροφή στη "
                     "σύνδεση» και ζητήστε νέο κωδικό.")
            return
        try:
            db.complete_password_reset(
                record_id, passwords.hash_password(new_pw))
        except db.AirtableError as exc:
            print(f"[WARN] Password reset write failed for {email!r}: {exc}")
            st.error("Η αλλαγή του κωδικού απέτυχε προσωρινά. Παρακαλώ "
                     "δοκιμάστε ξανά σε λίγο.")
            return
        _clear_reset_state()
        st.session_state["login_flash"] = (
            "success", "Ο κωδικός σας άλλαξε επιτυχώς! Μπορείτε να "
                       "συνδεθείτε.")
        st.rerun()

    # Wrong code (or unknown email): count the attempt; the 5th revokes the
    # token so the 6-digit space can't be brute-forced through the form.
    attempts = st.session_state.get("reset_attempts", 0) + 1
    st.session_state["reset_attempts"] = attempts
    if attempts >= _MAX_RESET_ATTEMPTS:
        if record_id:
            try:
                db.clear_reset_token(record_id)
            except db.AirtableError as exc:
                print(f"[WARN] Reset token revoke failed for {email!r}: {exc}")
        _clear_reset_state()
        st.session_state["login_flash"] = (
            "error", "Πολλές αποτυχημένες προσπάθειες — η επαναφορά "
                     "ακυρώθηκε. Ζητήστε νέο κωδικό επαλήθευσης.")
        st.rerun()
    st.error("❌ Λανθασμένος κωδικός επαλήθευσης "
             f"(προσπάθεια {attempts}/{_MAX_RESET_ATTEMPTS}).")


# --------------------------------------------------------------------------
# Self-registration ("Δεν έχετε λογαριασμό; Εγγραφή εδώ")
# --------------------------------------------------------------------------
def _register_screen():
    """Create a Users row from the login card. The password is hashed
    before it leaves the process (passwords.hash_password) and the account
    starts on a TRIAL_DAYS free trial (SubscriptionStatus "Active" +
    TrialExpiry); when it lapses, the subscription gate flips the row to
    "Inactive" and the paywall takes over — Stripe checkout reactivates."""
    _, mid, _ = st.columns([1, 1.7, 1])
    with mid:
        st.markdown("#### 📝 Δημιουργία Λογαριασμού")
        st.caption(f"Με την εγγραφή ξεκινά δωρεάν δοκιμή {TRIAL_DAYS} "
                   "ημερών — χωρίς κάρτα. Το email χρειάζεται για την "
                   "επαναφορά κωδικού και τις ειδοποιήσεις συνδρομής.")
        with st.form("register"):
            username = st.text_input(
                "Όνομα Χρήστη (Username)", placeholder="π.χ. mystore",
                help="3+ χαρακτήρες· λατινικά γράμματα, αριθμοί και . _ -")
            email = st.text_input("Email", placeholder="you@example.com")
            password = st.text_input(
                "Κωδικός Πρόσβασης (Password)", type="password",
                placeholder="••••••••",
                help="Τουλάχιστον 6 χαρακτήρες.")
            submitted = st.form_submit_button("Δημιουργία Λογαριασμού",
                                              type="primary",
                                              width="stretch")
        if submitted:
            _handle_registration(username, email, password)
        if st.button("⬅️ Επιστροφή στη Σύνδεση", width="stretch"):
            st.session_state.pop("show_register", None)
            st.rerun()


def _handle_registration(username, email, password):
    username = username.strip()
    email = email.strip()
    password = password.strip()
    # Username doubles as the tenant key inside Airtable formulas and the
    # Stripe client_reference_id — keep it to a formula-safe alphabet.
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,32}", username):
        st.error("Το Όνομα Χρήστη πρέπει να έχει 3–32 χαρακτήρες: λατινικά "
                 "γράμματα, αριθμούς ή . _ - (χωρίς κενά).")
        return
    if "@" not in email:
        st.error("Παρακαλώ εισάγετε ένα έγκυρο email.")
        return
    if len(password) < 6:
        st.error("Ο κωδικός πρέπει να έχει τουλάχιστον 6 χαρακτήρες.")
        return
    try:
        if db.find_user(username=username):
            st.error("Το Όνομα Χρήστη χρησιμοποιείται ήδη. Επιλέξτε άλλο.")
            return
        # One row per email, or the password-reset lookup (find_user by
        # email) would pick an arbitrary account.
        if db.find_user(email=email):
            st.error("Το email αντιστοιχεί ήδη σε λογαριασμό. Δοκιμάστε "
                     "«Ξέχασα τον κωδικό μου».")
            return
        trial_expiry = (datetime.now(timezone.utc)
                        + timedelta(days=TRIAL_DAYS))
        db.create_user(username, email, passwords.hash_password(password),
                       trial_expiry.strftime("%Y-%m-%dT%H:%M:%SZ"))
    except db.AirtableError as exc:
        print(f"[WARN] Registration failed for {username!r}: {exc}")
        st.error("Η δημιουργία λογαριασμού απέτυχε προσωρινά. Παρακαλώ "
                 "δοκιμάστε ξανά σε λίγο.")
        return
    st.session_state.pop("show_register", None)
    st.session_state["login_flash"] = (
        "success", f"Ο λογαριασμός δημιουργήθηκε επιτυχώς — η δωρεάν δοκιμή "
                   f"{TRIAL_DAYS} ημερών ξεκίνησε! Συνδεθείτε με τα "
                   "στοιχεία σας.")
    st.rerun()


def logout():
    # Revoke the refresh-survival token FIRST so a stale ?session= URL can
    # never resurrect the login after an explicit logout.
    _revoke_session_token()
    # "theme_dark" is deliberately kept so the theme survives a re-login.
    for key in ("username", "session_token", "pending_invoice",
                "processed_upload", "extraction_error",
                "subscription_verified", "subscription_status",
                "trial_expiry", "user_record_id",
                "confirm_close", "flash", "confirm_delete_txn", "flash_recap",
                "flash_toast", "flash_toast_alert", "nav_page",
                "show_archived", "show_income_form", "show_expense_form",
                "show_debt_form", "confirm_paid_debt"):
        st.session_state.pop(key, None)
    # The localStorage wipe can't happen here: an iframe injected right
    # before st.rerun() never gets to execute its script. The flag makes
    # _sync_browser_session emit the wipe on the login render that follows.
    st.session_state["_clear_browser_session"] = True
    st.rerun()


def _lock_screen(text):
    st.markdown(
        '<div class="aud-lock">'
        '<div class="aud-lock-icon">🔒</div>'
        '<div class="aud-lock-title">Μπακαλοχαρτο</div>'
        f'<div class="aud-lock-text">{html.escape(text)}</div></div>',
        unsafe_allow_html=True,
    )
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        if st.button("Αποσύνδεση", width="stretch"):
            logout()


def _paywall_screen(username):
    """Billing gateway for logged-in users whose subscription isn't Active —
    Expired, Inactive, Unpaid, blank, whatever the Users row says. Unlike
    _lock_screen (an error state), this is a polite sales screen: explain,
    show the price, hand over the Stripe payment link, and offer a one-tap
    re-check for when they come back from checkout (the verdict is cached
    per session, so without the re-check they'd have to re-login)."""
    st.markdown(
        '<div class="aud-lock aud-paywall">'
        '<div class="aud-lock-icon">💳</div>'
        '<div class="aud-lock-title">Η συνδρομή σας δεν είναι ενεργή</div>'
        '<div class="aud-lock-text">Για να συνεχίσετε στο Μπακαλοχαρτο, '
        "ενεργοποιήστε τη συνδρομή σας. Η πληρωμή γίνεται με ασφάλεια μέσω "
        "Stripe και η πρόσβασή σας ξεκλειδώνει αυτόματα μόλις ολοκληρωθεί."
        "</div>"
        '<div class="aud-lock-price">10€ / μήνα</div></div>',
        unsafe_allow_html=True,
    )
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.link_button("💳 Ενεργοποίηση Συνδρομής (10€/μήνα)",
                       _subscribe_url(username), type="primary",
                       width="stretch")
        # Checkout opens in a new tab; back here, one tap re-reads the Users
        # row — by then the Stripe webhook has flipped it to Active.
        if st.button("🔄 Πλήρωσα — Έλεγχος ενεργοποίησης", width="stretch",
                     key="paywall_recheck"):
            for key in ("subscription_verified", "subscription_status",
                        "trial_expiry", "user_record_id"):
                st.session_state.pop(key, None)
            st.rerun()
        if st.button("Αποσύνδεση", width="stretch", key="paywall_logout"):
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

    Free trial: an "Active" row whose TrialExpiry lies in the past is a
    lapsed trial — the row is flipped to "Inactive" in Airtable and the
    paywall shows. Paying accounts carry no TrialExpiry (the Stripe
    activation clears it), so they never hit the flip.
    """
    if not st.session_state.get("subscription_verified"):
        try:
            user = db.find_user(username=username)
        except db.AirtableError as exc:
            print(f"[WARN] Subscription check failed for {username!r}: {exc}")
            _lock_screen("Ο έλεγχος του λογαριασμού σας απέτυχε προσωρινά. "
                         "Παρακαλώ δοκιμάστε ξανά σε λίγο.")
            return False
        fields = user["fields"] if user else {}
        st.session_state["subscription_verified"] = True
        st.session_state["subscription_status"] = fields.get("SubscriptionStatus")
        st.session_state["trial_expiry"] = fields.get("TrialExpiry")
        st.session_state["user_record_id"] = user["id"] if user else None

    status = (st.session_state.get("subscription_status") or "").strip().lower()
    if status == "active":
        expiry = _parse_utc_datetime(st.session_state.get("trial_expiry"))
        if expiry is None or expiry > datetime.now(timezone.utc):
            return True  # paying account (no expiry) or trial still running
        # Trial lapsed: persist the verdict to Airtable (best effort — access
        # is denied this session either way, and if the write failed the next
        # session's gate re-runs the same check) and show the paywall.
        record_id = st.session_state.get("user_record_id")
        if record_id:
            try:
                db.set_subscription_status(record_id, "Inactive")
            except db.AirtableError as exc:
                print(f"[WARN] Trial-lapse flip failed for {username!r}: {exc}")
        st.session_state["subscription_status"] = "Inactive"
        st.session_state.pop("trial_expiry", None)

    # Login already required an existing Users row with a matching password,
    # so EVERY non-Active verdict here — "Expired", "Inactive", "Unpaid",
    # blank, a lapsed trial, or a row deleted mid-session — is a billing
    # situation, not an error: show the payment gateway (subscribe link +
    # re-check + logout) instead of a dead-end message. Access stays denied
    # either way, so the strict allow-list is unchanged.
    _paywall_screen(username)
    return False


def _trial_days_left():
    """Whole days of free trial remaining, or None when no badge belongs in
    the sidebar (paying account without TrialExpiry, lapsed trial, verdict
    not yet cached). Rounds UP — 4.2 days shows as 5, matching the user's
    'it still works today' intuition."""
    if (st.session_state.get("subscription_status") or "").strip().lower() != "active":
        return None
    expiry = _parse_utc_datetime(st.session_state.get("trial_expiry"))
    if expiry is None:
        return None
    remaining = (expiry - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        return None
    return int(remaining // 86400) + 1


# --------------------------------------------------------------------------
# Tab 1 — Executive dashboard
# --------------------------------------------------------------------------
def _archive_button(proj, name):
    """Small "🔒 Αρχειοθέτηση" pill inside one active category card (the
    CSS right-aligns and rounds it; no width stretch — it hugs its label).
    First tap arms the confirmation (stored in session state);
    _close_project_control renders it full-width beneath the card. Arming
    also closes an open inline editor so the two panels never stack."""
    record_id = proj["id"]
    if st.button("🔒 Αρχειοθέτηση", key=f"close_{record_id}",
                 help=f"Αρχειοθέτηση του πελάτη «{name}»"):
        st.session_state["confirm_close"] = record_id
        st.session_state.pop("edit_project", None)
        st.rerun()


def _edit_button(proj, name):
    """Small "✏️ Επεξεργασία" pill right below the Αρχειοθέτηση one (same
    ghost treatment — see st-key-editcat_ in the CSS). Tapping it opens the
    inline Έσοδα/Έξοδα editor beneath the card (_edit_project_control) and
    disarms any pending archive confirmation."""
    record_id = proj["id"]
    if st.button("✏️ Επεξεργασία", key=f"editcat_{record_id}",
                 help=f"Χειροκίνητη επεξεργασία των ποσών του πελάτη «{name}»"):
        st.session_state["edit_project"] = record_id
        st.session_state.pop("confirm_close", None)
        st.rerun()


def _close_project_control(proj, name):
    """Two-step archive confirmation for one active category: renders only
    while this category is armed (confirm_close); the explicit "Ναι" tap
    flips the row's Status and stamps ClosedDate = today."""
    record_id = proj["id"]
    if st.session_state.get("confirm_close") != record_id:
        return
    st.warning(f"Να αρχειοθετηθεί ο πελάτης «{name}»; Θα μεταφερθεί "
               "στους Αρχειοθετημένους Πελάτες.")
    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button("✅ Ναι, αρχειοθέτηση", key=f"yes_{record_id}",
                     type="primary", width="stretch"):
            try:
                db.close_project(record_id, date.today())
            except db.AirtableError as exc:
                st.error(f"Δεν ήταν δυνατή η αρχειοθέτηση του πελάτη: {exc}")
            else:
                st.session_state.pop("confirm_close", None)
                st.session_state["flash"] = (
                    f"Ο πελάτης «{name}» αρχειοθετήθηκε επιτυχώς και "
                    "μεταφέρθηκε στους Αρχειοθετημένους Πελάτες."
                )
                _invalidate_caches()
                st.rerun()
    with col_no:
        if st.button("✕ Άκυρο", key=f"no_{record_id}", width="stretch"):
            st.session_state.pop("confirm_close", None)
            st.rerun()


def _amount_text(value):
    """Prefill text for a manual amount field: "1204,50" — plain digits with
    a decimal comma, exactly the shape _parse_amount reads back."""
    return f"{float(value or 0):.2f}".replace(".", ",")


def _edit_project_control(username, proj, name, rev, exp):
    """Inline manual editor for one active category, revealed by its
    ✏️ Επεξεργασία pill: two manual amount fields (Έσοδα / Έξοδα) plus a
    READ-ONLY Καθαρό card that always shows Έσοδα − Έξοδα — the user never
    types the net; it recomputes from the fields (or the stored sums while
    a field holds unparseable text) and again on save."""
    record_id = proj["id"]
    if st.session_state.get("edit_project") != record_id:
        return
    st.info(f"Επεξεργασία ποσών του πελάτη «{name}». Το Καθαρό "
            "υπολογίζεται αυτόματα ως Έσοδα − Έξοδα.")
    col_rev, col_exp = st.columns(2)
    with col_rev:
        rev_text = st.text_input("Έσοδα (€, με Φ.Π.Α.)", value=_amount_text(rev),
                                 key=f"edit_rev_{record_id}")
    with col_exp:
        exp_text = st.text_input("Έξοδα (€, με Φ.Π.Α.)", value=_amount_text(exp),
                                 key=f"edit_exp_{record_id}")
    # The correction rows carry this ΦΠΑ rate, so the client's net VAT stays
    # consistent with the edited totals.
    vat_rate = _vat_rate_selectbox(f"edit_vat_{record_id}")
    new_rev = _parse_amount(rev_text)
    new_exp = _parse_amount(exp_text)
    net_preview = ((new_rev if new_rev is not None else rev)
                   - (new_exp if new_exp is not None else exp))
    _kpi_row([("Καθαρό (υπολογίζεται αυτόματα)", _money(net_preview),
               _net_kind(net_preview))])
    col_save, col_cancel = st.columns(2)
    with col_save:
        if st.button("💾 Αποθήκευση", key=f"saveedit_{record_id}",
                     type="primary", width="stretch"):
            _save_project_edit(username, name, rev, exp, new_rev, new_exp,
                               vat_rate)
    with col_cancel:
        if st.button("✕ Άκυρο", key=f"canceledit_{record_id}",
                     width="stretch"):
            st.session_state.pop("edit_project", None)
            st.rerun()


def _save_project_edit(username, name, rev, exp, new_rev, new_exp,
                       vat_rate=DEFAULT_VAT_RATE):
    """Persist a manual client edit. The client's Έσοδα/Έξοδα are SUMS of its
    Transactions rows — there is nothing to overwrite in Projects — so the
    edit lands as signed correction rows: the delta between each target and
    the current sum, labeled Type Έσοδο/Έξοδο with the direction in the sign
    (see _is_revenue). Each correction also carries the ΦΠΑ (at `vat_rate`) of
    its delta, so the client's net VAT tracks the edited totals. After the
    write every view — client card, dashboard header, recap — recomputes to
    exactly the typed values, and Καθαρό falls out as Έσοδα − Έξοδα."""
    if (new_rev is None or new_rev < 0 or new_exp is None or new_exp < 0):
        st.error("Παρακαλώ εισάγετε έγκυρα ποσά (0 ή μεγαλύτερα) στα πεδία "
                 "Έσοδα και Έξοδα (π.χ. 150,00).")
        return
    rev_delta = round(new_rev - rev, 2)
    exp_delta = round(new_exp - exp, 2)
    if not rev_delta and not exp_delta:
        st.session_state.pop("edit_project", None)
        st.rerun()
    written = False
    try:
        if rev_delta:
            db.create_transaction(
                username, name, rev_delta,
                description=("Χειροκίνητη διόρθωση εσόδων "
                             f"({'+' if rev_delta > 0 else '-'}"
                             f"{_money(rev_delta)})"),
                date=date.today(), type_="Έσοδο", source="Manual",
                vat_amount=_vat_for_write(rev_delta, True, vat_rate),
                vat_rate=vat_rate)
            written = True
        if exp_delta:
            db.create_transaction(
                username, name, -exp_delta,
                description=("Χειροκίνητη διόρθωση εξόδων "
                             f"({'+' if exp_delta > 0 else '-'}"
                             f"{_money(exp_delta)})"),
                date=date.today(), type_="Έξοδο", source="Manual",
                vat_amount=_vat_for_write(-exp_delta, False, vat_rate),
                vat_rate=vat_rate)
            written = True
    except db.AirtableError as exc:
        # A partial write (revenue row saved, expense row failed) must not
        # leave the stale sums as the next retry's baseline.
        if written:
            _invalidate_caches()
        st.error(f"❌ Η αποθήκευση της διόρθωσης απέτυχε: {exc}")
        return
    st.session_state.pop("edit_project", None)
    _invalidate_caches()
    st.session_state["flash"] = (
        f"Ο πελάτης «{name}» ενημερώθηκε: Έσοδα {_money(new_rev)}, "
        f"Έξοδα {_money(new_exp)}, Καθαρό {_money(new_rev - new_exp)}.")
    st.rerun()


_QUICK_ENTRY_FLAGS = ("show_income_form", "show_expense_form",
                      "show_debt_form")


def _toggle_quick_entry(which):
    """on_click for the Έσοδα/Έξοδα/Χρεωστούμενα card-buttons: toggle one
    inline panel and close the others — mutually exclusive so they never
    stack and push the dashboard down."""
    was_open = st.session_state.get(which, False)
    for flag in _QUICK_ENTRY_FLAGS:
        st.session_state[flag] = False
    st.session_state[which] = not was_open


NEW_CATEGORY_OPTION = "➕ Δημιουργία Νέου Πελάτη..."


def _vat_rate_selectbox(key, default=DEFAULT_VAT_RATE):
    """ΦΠΑ-rate dropdown (24 / 13 / 6 / 0 %) returning the chosen DECIMAL rate.
    Safe inside an st.form — the rate is only read on submit, never mid-edit."""
    rates = list(VAT_RATES)
    return st.selectbox(
        "Συντελεστής Φ.Π.Α.", rates,
        index=rates.index(default) if default in rates else 0,
        format_func=lambda r: VAT_RATE_LABELS.get(r, f"{r:.0%}"),
        key=key,
        help="Ο συντελεστής ΦΠΑ αυτής της κίνησης — αποθηκεύεται ανά παραστατικό.",
    )


def _category_picker(category_names, key, default_index=0):
    """Client dropdown with the permanent trailing "create new" option.

    Lives OUTSIDE any st.form on purpose: widgets inside a form don't rerun
    on change, so the conditional "Όνομα Νέου Πελάτη" input could never
    appear dynamically there. Returns (choice, new_name)."""
    options = list(category_names) + [NEW_CATEGORY_OPTION]
    choice = st.selectbox("Πελάτης", options,
                          index=min(default_index, len(options) - 1), key=key)
    new_name = ""
    if choice == NEW_CATEGORY_OPTION:
        new_name = st.text_input("Όνομα Νέου Πελάτη",
                                 placeholder="π.χ. Παπαδόπουλος Α.Ε.",
                                 key=f"{key}_new")
    return choice, new_name


def _resolve_category(username, choice, new_name, category_names):
    """Turn a picker result into a real category name at save time, creating
    the Projects row when the "create new" option was chosen (an existing
    active category of the same name is simply reused). Returns None — with
    the error already rendered — when the save must be aborted.

    The existence check reads `category_names` — the caller's already-loaded
    (cached) active-projects list — instead of issuing a fresh
    find_active_project API call: that list was just fetched via
    _load_active_projects moments earlier, so re-querying Airtable here would
    be a redundant read on every single transaction save."""
    if choice != NEW_CATEGORY_OPTION:
        return choice
    name = new_name.strip()
    if not name:
        st.error("Παρακαλώ εισάγετε το όνομα του νέου πελάτη.")
        return None
    already_exists = name.strip().lower() in {
        (n or "").strip().lower() for n in category_names
    }
    try:
        if not already_exists:
            db.create_project(username, name)
            _invalidate_caches()
    except db.AirtableError as exc:
        st.error(f"Δεν ήταν δυνατή η δημιουργία του πελάτη: {exc}")
        return None
    return name


def _quick_entry_form(username, category_names, entry_type):
    """Inline Amount/Date/Category/Description form for ONE transaction type,
    revealed by its clickable KPI card. The Έσοδο/Έξοδο choice is encoded as
    the SIGN of Amount; every save closes the form, clears the caches and
    reruns instantly, exactly like the invoice flow, with the confirmation
    delivered as a toast."""
    genitive = "Εσόδου" if entry_type == "Έσοδο" else "Εξόδου"
    # The category picker sits ABOVE the form (a form widget can't reveal the
    # new-category input on change), so with zero active categories the form
    # still works: the dropdown then only offers "➕ Δημιουργία Νέας...".
    choice, new_category = _category_picker(category_names,
                                            key=f"qe_category_{entry_type}")
    # Explicit keys: the two forms carry otherwise-identical widgets,
    # which would collide on Streamlit's auto-generated widget IDs.
    with st.form(f"quick_entry_{entry_type}", clear_on_submit=True):
        # Plain manual text input (no +/- steppers); parsed leniently on
        # submit — "150", "12,50" and "1.204,80" all work.
        amount_text = st.text_input("Ποσό (€, με Φ.Π.Α.)",
                                    placeholder="π.χ. 150,00",
                                    key=f"qe_amount_{entry_type}")
        vat_rate = _vat_rate_selectbox(f"qe_vat_{entry_type}")
        entry_date = st.date_input("Ημερομηνία", value=date.today(),
                                   format="DD/MM/YYYY",
                                   key=f"qe_date_{entry_type}")
        notes = st.text_area("📝 Αιτιολογία / Περιγραφή (Προαιρετικό)",
                             key=f"qe_notes_{entry_type}")
        submitted = st.form_submit_button(
            f"💾 Αποθήκευση {genitive}", type="primary", width="stretch")
    if not submitted:
        return
    amount = _parse_amount(amount_text)
    if amount is None or amount <= 0:
        st.error("Παρακαλώ εισάγετε έγκυρο ποσό μεγαλύτερο από 0 "
                 "(π.χ. 150,00).")
        return
    category = _resolve_category(username, choice, new_category, category_names)
    if category is None:
        return
    signed = amount if entry_type == "Έσοδο" else -amount
    try:
        # Type/Source are explicit labels on manual rows; FileHash
        # (and the supplier folded into Description) stay reserved
        # for the AI scanning path. VAT_Amount is derived from the gross
        # figure and stored so per-client/total ΦΠΑ stay exact.
        db.create_transaction(
            username, category, signed,
            description=notes.strip() or None,
            date=entry_date,
            type_=entry_type,
            source="Manual",
            vat_amount=_vat_for_write(signed, entry_type == "Έσοδο", vat_rate),
            vat_rate=vat_rate,
        )
    except db.AirtableError as exc:
        st.error(f"❌ Η αποθήκευση στο Airtable απέτυχε: {exc}")
        return
    # Instant analytics: same clear-and-rerun as the invoice flow.
    for flag in _QUICK_ENTRY_FLAGS:
        st.session_state[flag] = False
    _invalidate_caches()
    st.session_state["flash_toast"] = (
        f"Η κίνηση ({entry_type}) αποθηκεύτηκε επιτυχώς στον "
        f"Πελάτη «{category}» — {_money(amount)}."
    )
    st.rerun()


def _debt_entry_panel(username, category_names, debts):
    """The inline Χρεωστούμενα panel, revealed by the yellow KPI card: the
    «Ποσό προς προσθήκη»/«Πελάτης» entry form, then the list of
    outstanding debts with their Εξόφληση controls. The panel stays open
    after a save so the fresh row is immediately visible in the list."""
    choice, new_category = _category_picker(category_names,
                                            key="qe_category_debt")
    with st.form("quick_entry_debt", clear_on_submit=True):
        amount_text = st.text_input("Ποσό προς προσθήκη (€, με Φ.Π.Α.)",
                                    placeholder="π.χ. 150,00",
                                    key="qe_amount_debt")
        # The ΦΠΑ rate rides the debt row now, so Εξόφληση can stamp the exact
        # output VAT when the amount becomes realised revenue.
        vat_rate = _vat_rate_selectbox("qe_vat_debt")
        submitted = st.form_submit_button("💾 Καταχώρηση Χρεωστούμενου",
                                          type="primary", width="stretch")
    if submitted:
        amount = _parse_amount(amount_text)
        if amount is None or amount <= 0:
            st.error("Παρακαλώ εισάγετε έγκυρο ποσό μεγαλύτερο από 0 "
                     "(π.χ. 150,00).")
        else:
            category = _resolve_category(username, choice, new_category, category_names)
            if category is not None:
                try:
                    # Positive Amount + Type "Χρεωστούμενο": the row carries
                    # its client but stays OUT of the revenue/VAT math until
                    # Εξόφληση flips the Type to "Έσοδο". VAT_Rate is stored
                    # now; VAT_Amount is stamped only on resolution.
                    db.create_transaction(
                        username, category, amount,
                        date=date.today(), type_=DEBT_TYPE, source="Manual",
                        vat_rate=vat_rate)
                except db.AirtableError as exc:
                    st.error(f"❌ Η αποθήκευση στο Airtable απέτυχε: {exc}")
                else:
                    _invalidate_caches()
                    st.session_state["flash_toast"] = (
                        f"Το χρεωστούμενο καταχωρήθηκε στον Πελάτη "
                        f"«{category}» — {_money(amount)}.")
                    st.rerun()

    _section(f"Εκκρεμή Χρεωστούμενα ({len(debts)})")
    if not debts:
        _empty_state("Δεν υπάρχουν εκκρεμή χρεωστούμενα.")
        return
    for txn in sorted(debts, key=lambda t: _txn_date(t) or date.min,
                      reverse=True):
        fields = txn["fields"]
        txn_day = _txn_date(txn)
        meta = " · ".join(part for part in (
            txn_day.strftime("%d/%m/%Y") if txn_day else None,
            fields.get("Category"),
            fields.get("Description"),
        ) if part)
        col_info, col_paid = st.columns([4, 1.3], vertical_alignment="center")
        with col_info:
            st.markdown(
                f'<div class="aud-txn-row debt">'
                f'<span class="aud-txn-meta">{html.escape(meta)}</span>'
                f'<span class="aud-proj-value debt">'
                f'{html.escape(_money(fields.get("Amount")))}</span></div>',
                unsafe_allow_html=True,
            )
        with col_paid:
            if st.button("✅ Εξόφληση", key=f"paid_{txn['id']}",
                         help="Μεταφορά του ποσού στα Έσοδα του πελάτη"):
                st.session_state["confirm_paid_debt"] = txn["id"]
                st.rerun()
        _resolve_debt_control(txn)


def _resolve_debt_control(txn):
    """Two-step Εξόφληση confirmation for one outstanding debt: the explicit
    «Ναι» tap flips the row's Type to "Έσοδο" (Date -> today), so the amount
    automatically leaves Χρεωστούμενα and lands in the Έσοδα of the same
    client — no second row, nothing to reconcile. The row's stored VAT_Rate
    (default 24 %) turns into the output VAT_Amount at this moment."""
    if st.session_state.get("confirm_paid_debt") != txn["id"]:
        return
    fields = txn["fields"]
    category = fields.get("Category") or "—"
    amount = _money(fields.get("Amount"))
    st.warning(f"Εξόφληση χρεωστούμενου {amount}; Θα αφαιρεθεί από τα "
               f"Χρεωστούμενα και θα προστεθεί στα Έσοδα του πελάτη "
               f"«{category}».")
    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button("✅ Ναι, εξόφληση", key=f"yespaid_{txn['id']}",
                     type="primary", width="stretch"):
            # The debt becomes realised revenue: stamp output VAT from the
            # stored rate (its gross Amount is positive) so ΦΠΑ stays exact.
            rate = fields.get("VAT_Rate")
            rate = float(rate) if rate is not None else DEFAULT_VAT_RATE
            vat_amount = _vat_for_write(_amount(txn), True, rate)
            try:
                db.resolve_debt_transaction(
                    txn["id"], date.today(),
                    vat_amount=vat_amount, vat_rate=rate)
            except db.AirtableError as exc:
                st.error(f"Δεν ήταν δυνατή η εξόφληση: {exc}")
            else:
                st.session_state.pop("confirm_paid_debt", None)
                _invalidate_caches()
                st.session_state["flash_toast"] = (
                    f"Το χρεωστούμενο εξοφλήθηκε — {amount} προστέθηκε στα "
                    f"Έσοδα του πελάτη «{category}».")
                st.rerun()
    with col_no:
        if st.button("✕ Άκυρο", key=f"nopaid_{txn['id']}", width="stretch"):
            st.session_state.pop("confirm_paid_debt", None)
            st.rerun()


def _kpi_entry_row(username, category_names, total_rev, total_exp, net,
                   total_debt, debts):
    """The merged dashboard header: Συνολικά έσοδα / Συνολικά έξοδα as
    clickable card-buttons (tap toggles the matching inline entry form just
    below; opening one closes the others), the static Καθαρό card whose
    palette follows the sign of the value, and the ALWAYS-yellow
    Χρεωστούμενα card-button right next to it (tap toggles the debts
    panel). Equal quarters; on narrow phones the mobile media query in _CSS
    reflows the four columns into a compact 2x2 grid."""
    col_rev, col_exp, col_net, col_debt = st.columns(4)
    with col_rev:
        st.button(f"Συνολικά έσοδα\n\n{_money(total_rev)}",
                  key="qe_card_income", width="stretch",
                  help="Πατήστε για προσθήκη εσόδου",
                  on_click=_toggle_quick_entry, args=("show_income_form",))
    with col_exp:
        st.button(f"Συνολικά έξοδα\n\n{_money(total_exp)}",
                  key="qe_card_expense", width="stretch",
                  help="Πατήστε για προσθήκη εξόδου",
                  on_click=_toggle_quick_entry, args=("show_expense_form",))
    with col_net:
        with st.container(key="qe_card_net"):
            _kpi_row([("Καθαρό κέρδος", _money(net), _net_kind(net))])
    with col_debt:
        st.button(f"Χρεωστούμενα\n\n{_money(total_debt)}",
                  key="qe_card_debt", width="stretch",
                  help="Πατήστε για διαχείριση χρεωστούμενων",
                  on_click=_toggle_quick_entry, args=("show_debt_form",))
    if st.session_state.get("show_income_form"):
        _quick_entry_form(username, category_names, "Έσοδο")
    elif st.session_state.get("show_expense_form"):
        _quick_entry_form(username, category_names, "Έξοδο")
    elif st.session_state.get("show_debt_form"):
        _debt_entry_panel(username, category_names, debts)


def _vat_total_row(total_vat, label="Συνολικό Φ.Π.Α."):
    """A single KPI card for the total ΦΠΑ — the EXACT sum of the per-client
    net VATs shown on the cards below (never recomputed from aggregate
    totals). Green when it's a credit (refundable), neutral when payable."""
    v = round(total_vat, 2)
    if v < 0:
        lbl, kind = f"{label} (προς επιστροφή)", "accent"
    elif v > 0:
        lbl, kind = f"{label} (προς απόδοση)", ""
    else:
        lbl, kind = label, ""
    _kpi_row([(lbl, _money(v), kind)])


def _closed_projects_section(completed, grouped):
    """Large KPI-styled toggle button (key archived_toggle — see the CSS);
    tapping it shows/hides the archived-clients list in place via the
    show_archived session flag, so the archive never clutters the daily flow."""
    # on_click flips the flag BEFORE the rerun executes, so the arrow in the
    # label and the list below always agree within a single paint.
    arrow = "▲" if st.session_state.get("show_archived") else "▼"
    st.button(f"📂 Αρχειοθετημένοι Πελάτες ({len(completed)})  {arrow}",
              key="archived_toggle", width="stretch",
              on_click=lambda: st.session_state.update(
                  show_archived=not st.session_state.get("show_archived", False)))
    if st.session_state.get("show_archived"):
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
        # Debts never enter the revenue/expense math — they feed only the
        # yellow Χρεωστούμενα card and its panel.
        regular, debts = _split_debts(_load_transactions(username))
    except db.AirtableError as exc:
        st.error(f"Δεν ήταν δυνατή η φόρτωση των δεδομένων σας: {exc}")
        return
    grouped = _transactions_by_category(regular)

    # This client's outstanding debts feed metric 4 on each card.
    debt_by_client = _debts_by_client(debts)
    # Full accounting snapshot per active client (gross+net flows, net ΦΠΑ,
    # debts, net-of-VAT profit). _project_financials is kept only for the
    # archived-client one-liners below.
    metrics = [
        _client_metrics(p["fields"].get("Name") or "—", grouped,
                        debt_by_client.get(
                            (p["fields"].get("Name") or "").strip().lower(), 0.0))
        for p in active
    ]
    _section("Επισκόπηση")
    if not active:
        _empty_state("Δεν υπάρχουν ενεργοί πελάτες ακόμη — πατήστε "
                     "«Συνολικά έσοδα» ή «Συνολικά έξοδα» και επιλέξτε "
                     "«➕ Δημιουργία Νέου Πελάτη...» για να ξεκινήσετε.")
    # Header totals come from the transactions THEMSELVES: summing the
    # per-active-client tuples silently dropped every row whose Category
    # no longer matches an active project (archived, renamed, or free-typed),
    # leaving the cards showing only a fraction of the real money.
    total_exp, total_rev = _sum_by_type(regular)
    net = total_rev - total_exp
    total_debt = sum(_amount(d) for d in debts)
    # Merged header: the Έσοδα/Έξοδα/Χρεωστούμενα cards ARE the quick-entry
    # buttons; tapping one opens its inline panel directly beneath the row.
    # Rendered even with zero clients — the forms' client dropdown can
    # create the first one via "➕ Δημιουργία Νέου Πελάτη...".
    _kpi_entry_row(
        username, [p["fields"].get("Name") or "—" for p in active],
        total_rev, total_exp, net, total_debt, debts)

    if active:
        # Total Period VAT KPI: STRICTLY the sum of the active clients' net
        # VATs rendered on the cards below, so the two can never disagree.
        _vat_total_row(round(sum(m["net_vat"] for m in metrics), 2))

        _section("Ενεργοί πελάτες")
        for proj, m in zip(active, metrics):
            name = proj["fields"].get("Name") or "—"
            # The keyed container IS the card (full row width): the name and
            # its stacked archive/edit pills share a top row; the 5-metric
            # grid and the income-tax bar fill the full width beneath.
            with st.container(key=f"projcard_{proj['id']}"):
                col_info, col_actions = st.columns(
                    [3.4, 1.1], vertical_alignment="center")
                with col_info:
                    st.markdown(
                        f'<div class="aud-client-name">{html.escape(name)}</div>',
                        unsafe_allow_html=True)
                with col_actions:
                    _archive_button(proj, name)
                    _edit_button(proj, name)
                _client_metrics_block(name, m, with_name=False)
            _close_project_control(proj, name)
            _edit_project_control(username, proj, name,
                                  m["gross_rev"], m["gross_exp"])

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

    # The uploader key carries a suffix that is bumped after a save/discard:
    # re-keying re-instantiates the widget empty on the next run — the only
    # way to programmatically clear st.file_uploader.
    suffix = st.session_state.setdefault("uploader_key_suffix", 0)
    uploaded = st.file_uploader(
        "Φωτογραφία παραστατικού ή PDF",
        type=UPLOAD_TYPES,
        key=f"invoice_upload_{suffix}",
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
        st.error(f"Δεν ήταν δυνατή η φόρτωση των πελατών σας: {exc}")
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

    # The picker sits ABOVE the form so choosing "➕ Δημιουργία Νέου
    # Πελάτη..." can reveal its name input immediately (form widgets
    # don't rerun on change). Keying by the upload fingerprint re-applies
    # the fuzzy preselection for each new document.
    picker_key = ("invoice_category_"
                  f"{(st.session_state.get('processed_upload') or '')[:12]}")
    choice, new_category = _category_picker(category_names, key=picker_key,
                                            default_index=default_index)

    # Prefill the manual amount input with the extracted total (dot-decimal,
    # two places); _parse_amount re-reads whatever the user types.
    extracted_amount = _parse_amount(pending.get("total_amount"))
    # The AI's income/expense call ("document_type") pre-selects this toggle,
    # but the user has the final say before anything is saved — e.g. a sales
    # receipt the model missed, or a purchase invoice it misread as income.
    type_options = ["Έξοδο", "Έσοδο"]
    predicted_index = 1 if pending.get("document_type") == "income" else 0
    with st.form("confirm_invoice"):
        # The counterparty printed on the document (supplier on a purchase,
        # buyer on a sale). Labeled «Επωνυμία» — NOT «Πελάτης» — so it never
        # clashes with the client-account picker above; it rides inside
        # Description (the schema has no Provider column).
        provider = st.text_input("Προμηθευτής / Επωνυμία παραστατικού",
                                 value=pending.get("provider_name") or "")
        entry_type = st.selectbox(
            "Τύπος", type_options, index=predicted_index,
            help="Προτεινόμενο από την ανάλυση AI — ελέγξτε πριν την αποθήκευση.",
        )
        # Plain manual text input (no +/- steppers); the Τύπος selectbox above
        # carries the income/expense sign now, so this is always a magnitude.
        amount_text = st.text_input(
            "Συνολικό ποσό (€, με Φ.Π.Α.)",
            value=f"{abs(extracted_amount):.2f}" if extracted_amount is not None else "",
            placeholder="π.χ. 150,00",
        )
        # ΦΠΑ rate per document (default 24 %); the extractor gives only the
        # gross total, so VAT is derived from it and this rate.
        vat_rate = _vat_rate_selectbox("invoice_vat")
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
        save = st.form_submit_button("✅ Επιβεβαίωση & Αποθήκευση στον Πελάτη",
                                     type="primary", width="stretch")
        discard = st.form_submit_button("🗑 Απόρριψη", width="stretch")

    if discard:
        st.session_state.pop("pending_invoice", None)
        st.session_state.pop("extraction_error", None)
        st.session_state.pop("processed_upload", None)
        st.session_state["uploader_key_suffix"] += 1
        st.rerun()

    if save:
        amount = _parse_amount(amount_text)
        if amount is None or amount <= 0:
            st.error("Παρακαλώ εισάγετε έγκυρο ποσό μεγαλύτερο από 0 "
                     "(π.χ. 150,00).")
            return
        # Resolve the picker first: a "create new" choice with a blank name
        # aborts before anything touches Airtable.
        category = _resolve_category(username, choice, new_category, category_names)
        if category is None:
            return
        # Duplicate-receipt guard: the SHA-256 fingerprint computed at upload
        # time is checked against this tenant's FileHash column before the
        # save, and stored with the new row so future re-uploads are caught.
        file_hash = st.session_state.get("processed_upload")
        # The schema has no Provider column — the supplier rides inside
        # Description alongside the materials summary.
        full_description = " · ".join(
            part for part in (provider.strip(), description.strip()) if part
        ) or None
        # Τύπος selectbox (AI-predicted, user-reviewable) is now the source of
        # truth for the sign, not a heuristic on the typed amount.
        signed = amount if entry_type == "Έσοδο" else -amount
        try:
            # Reuses the cached transaction list instead of issuing a fresh
            # find_transaction_by_hash API call on every single invoice save;
            # every create_transaction below invalidates the cache right
            # after the write, so this stays exactly as fresh as the rest of
            # the app's cached reads.
            if file_hash and any(
                (t["fields"].get("FileHash") or "") == file_hash
                for t in _load_transactions(username)
            ):
                st.error("⚠️ Αυτό το παραστατικό έχει ήδη καταχωρηθεί στο σύστημα!")
                return
            db.create_transaction(
                username, category, signed,
                description=full_description,
                date=inv_date,  # date object; normalized to ISO in the client
                file_hash=file_hash,
                type_=entry_type,
                source="AI Scan",
                vat_amount=_vat_for_write(signed, entry_type == "Έσοδο", vat_rate),
                vat_rate=vat_rate,
            )
        except db.AirtableError as exc:
            st.error(f"❌ Η αποθήκευση στο Airtable απέτυχε: {exc}")
            return

        # Instant analytics: clear every cached read and rerun NOW so the
        # dashboard/recap numbers already include this row on the next paint.
        # The confirmations ride session_state as toasts because widgets
        # rendered before st.rerun() never reach the screen. The uploader is
        # cleared by bumping its key suffix, so processed_upload goes too: a
        # deliberate re-upload of the same receipt re-extracts and is then
        # stopped by the FileHash duplicate guard with a visible error.
        _invalidate_caches()
        st.session_state.pop("pending_invoice", None)
        st.session_state.pop("extraction_error", None)
        st.session_state.pop("processed_upload", None)
        st.session_state["uploader_key_suffix"] += 1
        st.session_state["flash_toast"] = (
            f"Το παραστατικό αποθηκεύτηκε επιτυχώς στον Πελάτη "
            f"«{category}» — {_money(amount)}."
        )
        if entry_type == "Έξοδο" and amount > HIGH_EXPENSE_THRESHOLD:
            st.session_state["flash_toast_alert"] = (
                f"ΠΡΟΕΙΔΟΠΟΙΗΣΗ ΥΨΗΛΟΥ ΕΞΟΔΟΥ — "
                f"{provider.strip() or 'άγνωστος προμηθευτής'}: {_money(amount)} "
                f"υπερβαίνει το όριο των {_money(HIGH_EXPENSE_THRESHOLD)}."
            )
        st.rerun()


def upload_tab(username):
    # Document-less (manual) entries live on the dashboard now: tapping the
    # Έσοδα/Έξοδα KPI cards opens their inline quick-entry forms.
    _invoice_flow(username)


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


def _recap_txn_list(title, txns):
    """One flow's transaction list under its clickable recap totals card,
    each row with its confirm-then-delete control.

    Receives an ALREADY date-filtered slice of the period scope built in
    recap_tab — the same rows the summary table and KPI totals sum — so the
    list always adds up to exactly the card that revealed it."""
    txns = sorted(txns, key=_txn_date, reverse=True)
    _section(f"{title} ({len(txns)})")
    if not txns:
        _empty_state("Δεν υπάρχουν κινήσεις στην επιλεγμένη περίοδο.")
        return
    for txn in txns:
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
            # Income rows/values green, expense rows/values red — the row
            # class paints the left border, the value class the amount (the
            # old yellow "warm" accent is gone from the recap).
            row_kind = "income" if is_revenue else "expense"
            value_kind = "accent" if is_revenue else "loss"
            st.markdown(
                f'<div class="aud-txn-row {row_kind}">'
                f'<span class="aud-txn-meta">{html.escape(meta)}</span>'
                f'<span class="aud-proj-value {value_kind}">'
                f'{html.escape(_money(fields.get("Amount")))}</span></div>',
                unsafe_allow_html=True,
            )
        with col_del:
            if st.button("🗑️ Διαγραφή", key=f"del_{txn['id']}"):
                st.session_state["confirm_delete_txn"] = txn["id"]
                st.rerun()
        _delete_txn_control(txn["id"])


_RECAP_LIST_FLAGS = ("recap_show_income", "recap_show_expense")


def _toggle_recap_list(which):
    """on_click for the recap Συνολικά έσοδα/έξοδα card-buttons: toggle one
    transaction list and close the other — mutually exclusive, exactly like
    the dashboard's quick-entry cards."""
    was_open = st.session_state.get(which, False)
    for flag in _RECAP_LIST_FLAGS:
        st.session_state[flag] = False
    st.session_state[which] = not was_open


def _recap_totals_row(total_rev, total_exp, net):
    """The recap totals row, recast in the dashboard header's mold: the
    Συνολικά έσοδα / Συνολικά έξοδα cards are REAL clickable buttons (their
    qe_card_*_recap keys ride the dashboard cards' CSS via the class*=
    substring match) that reveal the period's matching transaction list
    beneath the row; Καθαρό κέρδος stays a static sign-colored card."""
    col_rev, col_exp, col_net = st.columns(3)
    with col_rev:
        st.button(f"Συνολικά έσοδα\n\n{_money(total_rev)}",
                  key="qe_card_income_recap", width="stretch",
                  help="Πατήστε για τις κινήσεις εσόδων της περιόδου",
                  on_click=_toggle_recap_list, args=("recap_show_income",))
    with col_exp:
        st.button(f"Συνολικά έξοδα\n\n{_money(total_exp)}",
                  key="qe_card_expense_recap", width="stretch",
                  help="Πατήστε για τις κινήσεις εξόδων της περιόδου",
                  on_click=_toggle_recap_list, args=("recap_show_expense",))
    with col_net:
        with st.container(key="qe_card_net_recap"):
            _kpi_row([("Καθαρό κέρδος", _money(net), _net_kind(net))])


def recap_tab(username):
    _section("Ανασκόπηση περιόδου")
    flash = st.session_state.pop("flash_recap", None)
    if flash:
        st.success(flash)
    st.markdown(
        f'<div class="aud-upload-hint">Συγκεντρωτικά έσοδα, έξοδα και Φ.Π.Α. '
        f"ανά πελάτη για την περίοδο που επιλέγετε. Δεδομένα "
        f"παλαιότερα των {RETENTION_YEARS} ετών διαγράφονται "
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
    # Outstanding debts stay off the recap analytics: they aren't revenue
    # (yet) — once resolved, their Type flips to "Έσοδο" and they show here.
    # They still feed each client card's Χρεωστούμενα metric as CURRENT state.
    transactions, debts = _split_debts(transactions)
    debt_by_client = _debts_by_client(debts)
    # ONE period scope feeds everything below — the per-client cards, the KPI
    # totals and the per-card Κινήσεις lists — so they can never disagree. The
    # old flow summed the LIFETIME totals of clients archived INSIDE the range
    # instead: entries living in active (or long-archived) clients were
    # dropped, so the cards showed a fraction of what the list summed.
    period_txns = [t for t in transactions
                   if (d := _txn_date(t)) and start <= d <= end]
    grouped = _transactions_by_category(period_txns)
    # Latest archive date per client name, for the card's «Αρχειοθετήθηκε» badge.
    closed_by_key = {}
    for proj in completed:
        key = (proj["fields"].get("Name") or "").strip().lower()
        closed_day = _closed_date(proj)
        if closed_day and closed_day >= (closed_by_key.get(key) or date.min):
            closed_by_key[key] = closed_day

    if not period_txns:
        _empty_state(f"Δεν υπάρχουν κινήσεις μεταξύ "
                     f"{start.strftime('%d/%m/%Y')} και {end.strftime('%d/%m/%Y')}.")
        return

    total_exp, total_rev = _sum_by_type(period_txns)
    net = total_rev - total_exp
    # Same clickable header as the dashboard: tapping Συνολικά έσοδα / έξοδα
    # reveals the period's matching transactions (with their delete controls)
    # at the bottom. Recap only — the dashboard's cards toggle entry forms.
    _recap_totals_row(total_rev, total_exp, net)

    # Per-client accounting cards for the period (the plain summary table is
    # gone — these carry strictly more: gross & net flows, net ΦΠΑ, debts,
    # net-of-VAT profit and the income-tax bar). Debts are current-state.
    per_client = []
    for key in sorted(grouped):
        name = next((t["fields"].get("Category") for t in grouped[key]
                     if t["fields"].get("Category")), "—")
        per_client.append(
            (key, name,
             _client_metrics(name, grouped, debt_by_client.get(key, 0.0))))

    # Total Period VAT KPI: STRICTLY the sum of the per-client net VATs below.
    _vat_total_row(round(sum(m["net_vat"] for _, _, m in per_client), 2),
                   label="Συνολικό Φ.Π.Α. περιόδου")

    _section("Πελάτες περιόδου")
    for key, name, m in per_client:
        closed_day = closed_by_key.get(key)
        archived = (f"Αρχειοθετήθηκε {closed_day.strftime('%d/%m/%Y')}"
                    if closed_day else None)
        with st.container(key=f"clientcard_{key}"):
            _client_metrics_block(name, m, archived_label=archived)

    # Drill-down transaction lists (toggled by the totals cards above).
    if st.session_state.get("recap_show_income"):
        _recap_txn_list("Κινήσεις εσόδων περιόδου",
                        [t for t in period_txns if _is_revenue(t)])
    elif st.session_state.get("recap_show_expense"):
        _recap_txn_list("Κινήσεις εξόδων περιόδου",
                        [t for t in period_txns if not _is_revenue(t)])


# --------------------------------------------------------------------------
# Tab 4 — Πληρωμές (billing center)
# --------------------------------------------------------------------------
# Stripe no-code links (TEST mode — swap for the live-mode links at launch).
# The portal URL is the PERMANENT /p/login/ link (Stripe Dashboard →
# Settings → Billing → Customer portal), not a single-use /p/session/ one.
STRIPE_SUBSCRIBE_URL = "https://buy.stripe.com/test_00w8wR6WG1Jee5759xa3u01"
STRIPE_PORTAL_URL = "https://billing.stripe.com/p/login/test_4gM8wR6WGbjO8KN1Xla3u00"


def _subscribe_url(username):
    """Payment Link with the tenant stamped as client_reference_id, so the
    Stripe webhook (stripe_webhook.py) flips the EXACT Users row to Active
    after checkout. Stripe only accepts 1-200 chars of [A-Za-z0-9_-] there;
    any other username (Greek letters, spaces, ...) gets the bare link and
    activation falls back to matching the payer's email against the Users
    table's optional Email column."""
    if username and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", username):
        return f"{STRIPE_SUBSCRIBE_URL}?client_reference_id={username}"
    return STRIPE_SUBSCRIBE_URL


def payments_tab(username):
    """Billing center: subscribe via Stripe Payment Link, self-service
    management (card change, receipts, cancellation) via the Stripe Customer
    Portal. st.link_button always opens in a NEW browser tab, so the client
    never loses their session. After checkout, the webhook service
    (stripe_webhook.py) flips the Users row's SubscriptionStatus to Active
    automatically — matched via the client_reference_id this tab stamps on
    the Payment Link; the sidebar «Ανανέωση» re-reads the fresh verdict."""
    _section("Πληρωμές & Συνδρομή")
    col_sub, col_manage = st.columns(2)
    with col_sub, st.container(key="pay_card_subscribe"):
        st.markdown(
            '<div class="aud-pay-title">Νέα Συνδρομή</div>'
            '<div class="aud-pay-badge">10€ / μήνα</div>'
            '<div class="aud-pay-text">Πλήρης πρόσβαση στο Μπακαλοχαρτο: '
            "σκανάρισμα παραστατικών με AI, αναλυτικά στατιστικά και "
            "απεριόριστες καταχωρήσεις. Ακύρωση οποιαδήποτε στιγμή.</div>",
            unsafe_allow_html=True,
        )
        st.link_button("💳 Ενεργοποίηση Συνδρομής (10€/μήνα)",
                       _subscribe_url(username), type="primary",
                       width="stretch")
    with col_manage, st.container(key="pay_card_manage"):
        st.markdown(
            '<div class="aud-pay-title">Υπάρχουσα Συνδρομή</div>'
            '<div class="aud-pay-text">Αλλαγή κάρτας, λήψη αποδείξεων ή '
            "ακύρωση της συνδρομής σας — με ασφάλεια μέσω του Stripe "
            "Customer Portal.</div>",
            unsafe_allow_html=True,
        )
        st.link_button("⚙️ Διαχείριση Συνδρομής & Αλλαγή Κάρτας",
                       STRIPE_PORTAL_URL, width="stretch")
    st.caption("Οι πληρωμές διεκπεραιώνονται με ασφάλεια από το Stripe — "
               "τα στοιχεία της κάρτας σας δεν αποθηκεύονται ποτέ στο "
               "Μπακαλοχαρτο.")


# --------------------------------------------------------------------------
# App entry point
# --------------------------------------------------------------------------
# Sidebar navigation replaces the old top st.tabs row: one styled radio
# (key "nav_page" — see the CSS that dresses it as a menu), and only the
# active page's content is rendered in the main area.
PAGE_DASHBOARD = "📊 Πίνακας Ελέγχου"
PAGE_UPLOAD = "📸 Καταχώρηση"
PAGE_RECAP = "🗓 Ανασκόπηση"
PAGE_PAYMENTS = "💳 Πληρωμές"
PAGES = [PAGE_DASHBOARD, PAGE_UPLOAD, PAGE_RECAP, PAGE_PAYMENTS]


def _render_sidebar(username):
    """The sidebar exists on EVERY screen (login included) so the native
    collapse/expand toggle is always available; navigation and account
    controls appear only once logged in."""
    with st.sidebar:
        st.markdown('<div class="aud-brand-small">Μπακαλο<span>χαρτο</span></div>',
                    unsafe_allow_html=True)
        if username:
            st.markdown(f'<div class="aud-user">{html.escape(username)}</div>',
                        unsafe_allow_html=True)
            # Trial countdown. Reads the gate's cached verdict, so on the
            # very first render of a restored session (sidebar paints before
            # subscription_gate runs) it stays absent and appears from the
            # next rerun on.
            days = _trial_days_left()
            if days is not None:
                text = ("Απομένει 1 ημέρα δοκιμής" if days == 1
                        else f"Απομένουν {days} ημέρες δοκιμής")
                st.markdown(f'<div class="aud-trial">⏳ {text}</div>',
                            unsafe_allow_html=True)
            _section("Πλοήγηση")
            st.radio("Πλοήγηση", PAGES, key="nav_page",
                     label_visibility="collapsed")
            # Every nav tap auto-closes the sidebar on ALL devices (the
            # listener installs once per page load; see the docstring).
            _install_sidebar_autocollapse()
            _section("Ρυθμίσεις")
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
            for key in ("subscription_verified", "subscription_status",
                        "trial_expiry", "user_record_id"):
                st.session_state.pop(key, None)
            st.rerun()


def _install_notranslate_guard():
    """Stop browser/OS auto-translate engines (Samsung Internet Translate,
    Chrome's built-in translate, etc.) from rewriting text nodes inside the
    tree React/Streamlit owns.

    Those engines swap translated text into place by mutating existing DOM
    nodes; when React later reconciles the same subtree it can try to
    remove a node the extension has already detached, throwing an uncaught
    `NotFoundError: Failed to execute 'removeChild' on 'Node'` that crashes
    the whole app — observed on Samsung devices. Marking the tree
    notranslate (the <meta name="google" content="notranslate"> tag, the
    `notranslate` class, and the `translate="no"` attribute — the trio every
    major engine checks) tells them to leave it alone before they ever get a
    chance to touch it.

    Runs once per page load, unconditionally (login screen included), same
    guarded-parent-window pattern as _install_sidebar_autocollapse below:
    st.markdown strips <script>, so this rides st.iframe and reaches the
    real document via window.parent. Idempotent — safe to call on every
    rerun since the guard flag short-circuits everything after the first."""
    st.iframe(
        """
        <script>
        (function () {
            const root = window.parent;
            if (root.__audNotranslateGuard) return;  // once per page load
            root.__audNotranslateGuard = true;
            const doc = root.document;

            function mark(el) {
                if (!el) return;
                el.classList.add('notranslate');
                el.setAttribute('translate', 'no');
            }

            if (!doc.querySelector('meta[name="google"][content="notranslate"]')) {
                const meta = doc.createElement('meta');
                meta.name = 'google';
                meta.content = 'notranslate';
                doc.head.appendChild(meta);
            }
            doc.documentElement.lang = 'el';
            mark(doc.documentElement);
            mark(doc.body);
            mark(doc.querySelector('.stApp'));
        })();
        </script>
        """,
        height=1,
    )


def _install_sidebar_autocollapse():
    """Global listener: ANY tap on a nav_page menu option closes the sidebar
    on EVERY device — phones, laptops, desktops — for every page change:
    Πίνακας Ελέγχου, Καταχώρηση, Ανασκόπηση and Πληρωμές alike. (There is
    deliberately NO viewport-width gate; the user reopens the sidebar any
    time via the native expand arrow, which the CSS always keeps visible.)

    Why a persistent listener instead of a one-shot script per nav change:
    Streamlit's React frontend only executes an iframe's script when the
    iframe MOUNTS. A rerun that re-renders byte-identical HTML at the same
    tree position reuses the existing DOM node, so a "fire once after this
    rerun" snippet runs on the first navigation and then never again. This
    listener is therefore installed ONCE per browser page load (guarded by a
    flag on the parent window), delegated on the parent document — where it
    survives every rerun/re-render — and reacts to both click (capture
    phase, since Streamlit widgets stop propagation) and change (keyboard
    radio navigation) events.

    st.markdown strips <script>, so this rides st.iframe (the successor of
    components.v1.html, deprecated in Streamlit 1.58) and reaches the app
    through window.parent. The selector list covers every testid Streamlit
    has used for the collapse control (same set the CSS keeps visible)."""
    st.iframe(
        """
        <script>
        (function () {
            const root = window.parent;
            if (root.__audNavAutoCollapse) return;  // once per page load
            root.__audNavAutoCollapse = true;
            const doc = root.document;

            function collapseSidebar() {
                // No width gate: collapse on all devices, desktop included.
                setTimeout(function () {
                    const sidebar = doc.querySelector('[data-testid="stSidebar"]');
                    if (!sidebar ||
                        sidebar.getAttribute('aria-expanded') === 'false') {
                        return;
                    }
                    const btn = sidebar.querySelector(
                        '[data-testid="stSidebarCollapseButton"] button,' +
                        '[data-testid="stSidebarCollapse"] button,' +
                        '[data-testid="stSidebarHeader"] button,' +
                        'button[kind="headerNoPadding"]');
                    if (btn) { btn.click(); }
                }, 150);
            }

            function isNavOption(target) {
                return target instanceof root.Element &&
                    target.closest('[class*="st-key-nav_page"]') !== null;
            }

            // Delegated on the document: keeps working no matter how often
            // Streamlit swaps the sidebar's inner DOM between reruns.
            doc.addEventListener('click', function (ev) {
                if (isNavOption(ev.target)) { collapseSidebar(); }
            }, true);
            doc.addEventListener('change', function (ev) {
                if (isNavOption(ev.target)) { collapseSidebar(); }
            }, true);
        })();
        </script>
        """,
        height=1,
    )


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
    _install_notranslate_guard()
    _run_retention_cleanup()
    _restore_session()

    username = st.session_state.get("username")
    _render_sidebar(username)

    if not username:
        # May reload the page with ?session= restored from localStorage
        # instead of leaving the user on the login screen (rapid-click
        # WebSocket reconnects arrive here with empty session_state).
        _sync_browser_session()
        login_screen()
        return

    # Logged in: keep the localStorage mirror of the token fresh.
    _persist_browser_session()

    if not subscription_gate(username):
        return

    _flush_toasts()

    # Only the sidebar-selected page renders in the main area (the radio
    # widget in _render_sidebar owns "nav_page"; before its first paint the
    # key is absent, so default to the dashboard).
    page = st.session_state.get("nav_page", PAGE_DASHBOARD)
    if page == PAGE_UPLOAD:
        upload_tab(username)
    elif page == PAGE_RECAP:
        recap_tab(username)
    elif page == PAGE_PAYMENTS:
        payments_tab(username)
    else:
        dashboard_tab(username)


main()
