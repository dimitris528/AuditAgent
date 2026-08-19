// Sentry for the EDGE runtime — src/middleware.ts, which decides whether a
// request has a session cookie and where to send it if not.
//
// A separate init because the Edge runtime is not Node: it has no filesystem,
// no async_hooks and a different set of Sentry integrations. Sharing one config
// object across both is what produces "module not found" at build time.
import * as Sentry from "@sentry/nextjs";

import { SENTRY_ENABLED, baseSentryOptions } from "@/lib/monitoring";

if (SENTRY_ENABLED) {
  Sentry.init({
    ...baseSentryOptions,
    // Middleware runs on EVERY request that is not a static asset, so tracing
    // it at the shared rate would multiply the trace volume by the number of
    // navigations for very little insight — it does one cookie read.
    tracesSampleRate: 0,
  });
}
