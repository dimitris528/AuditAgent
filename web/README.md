# Λογιστήριο Pro — Accounting Dashboard (Next.js)

A modern, enterprise-grade accounting dashboard for accountants, built on top of
the **existing Python/Airtable backend** (nothing was rewritten — the web app
reads/writes through it).

## Architecture

```
┌──────────────────────────┐      HTTP/JSON       ┌──────────────────────────────┐
│  Next.js 14 (App Router)  │ ───────────────────▶ │  FastAPI  (server/main.py)    │
│  web/  — TS + Tailwind     │  /api/dashboard …    │  thin glue, no new logic      │
│  Recharts + Lucide         │ ◀─────────────────── │                              │
└──────────────────────────┘                       │  finance.py  ← VAT / metrics  │
                                                    │  airtable_client.py ← Airtable │
                                                    └──────────────────────────────┘
```

- **`finance.py`** (repo root) is the single source of truth for all financial
  math, so `total VAT == Σ per-client VATs` by construction.
- **`server/`** is a thin FastAPI layer that shapes JSON; all Airtable I/O stays
  in `airtable_client.py`.
- **`web/`** is this Next.js dashboard.

## Features

- **Executive header** — Total Revenue, Total Expenses, Net Profit (net of VAT),
  and the Global Net VAT balance, computed as the exact sum of the client cards.
- **Rich client cards** — per client: Έσοδα (gross & net), Έξοδα (gross & net),
  Φ.Π.Α. (net balance with Προς απόδοση / Προς επιστροφή badges), Χρεωστούμενα,
  Καθαρό Αποτέλεσμα.
- **Income-tax progress bar** per client (Μπάρα Φόρου Εισοδήματος) that fills to
  100 % and turns warning-red with an alert badge when net taxable income crosses
  the scale limit (22 % → 29 %).
- **Analytics** — Revenue vs Expenses (per client), Net VAT breakdown, and a
  monthly revenue/expense trend.
- **Dark / light** corporate theme (persisted, no flash), **New Transaction**
  quick-add (write-back to Airtable).

## Run it (two processes)

**1. Backend** (from the repo root `C:\AuditAgent`):

```powershell
python -m uvicorn server.main:app --reload --port 8000
```

**2. Frontend** (from `C:\AuditAgent\web`):

```powershell
npm run dev
```

Open **http://localhost:3000**.

## Data source

- With **no Airtable configured**, the API serves a rich **demo dataset**
  (a `DEMO — χωρίς Airtable` badge appears) so the UI renders immediately.
- To use **real data**, put your Airtable credentials in `C:\AuditAgent\.env`
  (see `.env.example`) and set `DASHBOARD_USERNAME` to the tenant whose books you
  want. The API auto-uses live data once Airtable responds. Set `DASHBOARD_DEMO=0`
  to hard-disable the demo fallback.

> ℹ️ For VAT to persist, add two optional Number columns to the Airtable
> **Transactions** table: `VAT_Amount` and `VAT_Rate`. Until then, writes still
> succeed (VAT just isn't stored) and VAT is derived at 24 %.

## Env

| Var (frontend `web/.env.local`) | Default | Meaning |
| --- | --- | --- |
| `API_BASE_URL` | `http://localhost:8000` | Backend URL used by Server Components |
| `NEXT_PUBLIC_API_BASE` | `http://localhost:8000` | Backend URL used by the browser (writes) |

Backend env (repo-root `.env`): `DASHBOARD_USERNAME`, `DASHBOARD_DEMO`,
`FRONTEND_ORIGINS`, `DASHBOARD_TREND_MONTHS`, plus the existing `AIRTABLE_*`.
