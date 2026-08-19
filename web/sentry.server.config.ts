// Sentry for the Next.js NODE runtime — the route handlers in src/app/api/*
// and the server components that render the dashboard.
//
// Loaded by src/instrumentation.ts, which Next calls once per server process
// before any request is served. It must live at the project root under this
// exact name: the Sentry build plugin looks for it there.
//
// Note this is NOT the API. FastAPI has its own Sentry init (server/monitoring.py)
// and reports the accounting logic; this half reports the BFF — a route handler
// that threw, a cookie that could not be read, a proxy hop that failed in a way
// backendFetch did not expect.
import * as Sentry from "@sentry/nextjs";

import { SENTRY_ENABLED, baseSentryOptions } from "@/lib/monitoring";

// Guarded rather than relying on the SDK's own "no DSN, no-op" behaviour, so a
// deployment without Sentry pays nothing at all: no instrumentation is patched
// in and no integrations are constructed.
if (SENTRY_ENABLED) {
  Sentry.init({
    ...baseSentryOptions,
    // Quiet by default; set SENTRY_DEBUG=1 when a deployment is reporting
    // nothing and you need the SDK to say why.
    debug: process.env.SENTRY_DEBUG === "1",
  });
}
