# AuditAgent — Multi-Tenant Financial & Accounting SaaS

[![CI Pipeline](https://github.com/dimitris528/AuditAgent/actions/workflows/ci.yml/badge.svg)](https://github.com/dimitris528/AuditAgent/actions)
![Python Version](https://img.shields.io/badge/Python-3.11-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-Production%20Ready-009688?logo=fastapi)
![Next.js](https://img.shields.io/badge/Next.js-15-black?logo=next.js)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Supabase%20RLS-336791?logo=postgresql)
![Tests](https://img.shields.io/badge/Tests-838%20Passed-brightgreen?logo=pytest)

A production-grade, multi-tenant B2B financial platform designed for high data density, real-time client ledger auditing, and zero-trust security. Built with a desktop-like UX focus, automated CI/CD pipelines, and enterprise-grade tenant isolation.

---

## 🚀 Live Demo Environment

- **Live Application:** [https://accounting-web-oeh3.onrender.com](https://accounting-web-oeh3.onrender.com)
- **1-Click Demo Login:** use the **1-Click Demo Login** button directly on the login screen — no typing required.
- **Demo Credentials** (shared, public sandbox account):

  | Field    | Value                |
  | -------- | -------------------- |
  | Email    | `demo@auditagent.io` |
  | Password | `DemoPass2026!`      |

> **Note:** The UI is localized in Greek, tailored for Greek accounting & VAT compliance standards. Browser auto-translation (e.g. Google Chrome translate) works seamlessly for exploring the dashboard workflows.

---

## 🏛 Architecture & Tech Stack

- **Backend:** Python 3.11, FastAPI, Pydantic, SQLAlchemy.
- **Database & Multi-Tenancy:** PostgreSQL on Supabase protected by **Row-Level Security (RLS)** ensuring isolated tenant partitions.
- **Frontend:** Next.js 15, React 19, TailwindCSS, custom horizontal data visualisations.
- **Security & Caching:** Redis rate limiter (sliding window), TOTP 2FA, salted PBKDF2 hashing, context-aware password policy engine.
- **Observability & Ops:** Sentry error tracking (FastAPI + Next.js) with PII scrubbing, Resend API for transactional email, GitHub Actions CI/CD with **838 automated backend tests**.
- **Compliance:** GDPR privacy & terms pages with recorded, timestamped terms acceptance per account.

---

## ⚡ Key Highlights & Features

1. **Automated Multi-Tenant Onboarding:** Atomic database provisioning of tenant workspaces, admin accounts, and trial metadata via `/register`.
2. **Dense Financial Ledger:** Real-time running balance calculation, horizontal bar charts, and client ledger exports (PDF and Excel-ready CSV).
3. **Enterprise Defense-in-Depth:**
   - Multi-tenant isolation enforced directly at the SQL level (Supabase RLS).
   - Dedicated registration rate limits (`5/hour/IP`) against automated abuse.
   - Sentry tracing with strict regex-based PII/secret scrubbing.
4. **Resilient Testing Suite:** 838 passing backend unit and integration tests covering business edge cases, security exploits, concurrency collisions, and demo-account sandboxing.

---

## 🛠 Local Development Setup

```bash
# 1. Clone repository
git clone https://github.com/dimitris528/AuditAgent.git
cd AuditAgent

# 2. Setup Virtual Environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

# 3. Configure environment (fill in your own values)
cp .env.example .env
cp web/.env.local.example web/.env.local

# 4. Run Test Suite
pytest

# 5. Run the API (http://localhost:8000)
python -m uvicorn server.main:app --reload --port 8000

# 6. Run the web app in a second terminal (http://localhost:3000)
cd web && npm install && npm run dev
```
