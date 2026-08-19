// Sentry in the BROWSER — the half that catches what the user actually hits: a
// component that threw mid-render, an unhandled promise rejection in a form
// submit, a chart that crashed on a shape of data nobody anticipated.
//
// Next.js loads this file automatically on the client (it replaced the old
// sentry.client.config.ts convention in SDK v9). Anything imported here lands
// in the shared bundle, so it stays deliberately thin.
import * as Sentry from "@sentry/nextjs";

import { SENTRY_ENABLED, baseSentryOptions } from "@/lib/monitoring";

if (SENTRY_ENABLED) {
  Sentry.init({
    ...baseSentryOptions,
    // Session Replay is deliberately NOT enabled. It records the DOM, and this
    // product's DOM is somebody's book: client names, ΑΦΜ, invoice amounts and
    // debts. Replaying it to a third party is precisely what the privacy policy
    // says we do not do, and no masking configuration makes a ledger safe to
    // ship elsewhere.
    integrations: [Sentry.browserTracingIntegration()],
    // Which requests get a trace header attached. Same-origin only: the browser
    // never calls FastAPI directly (it goes through the /api BFF), so a wider
    // pattern would only add headers to third-party requests.
    tracePropagationTargets: [/^\//],
  });
}

// Instruments client-side navigations so a route change that throws is
// attributed to the route it was going to, not the one it left.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
