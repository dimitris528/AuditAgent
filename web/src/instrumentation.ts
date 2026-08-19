// Next.js calls `register()` once per server process, before the first request
// is handled. It is the only hook that runs early enough to instrument the
// runtime, which is why the Sentry server/edge inits are reached from here
// rather than imported by a layout.
//
// The imports are DYNAMIC and branched on the runtime: the Node config pulls in
// modules the Edge runtime does not have (and vice versa), and a static import
// of both would fail the build for whichever bundle it was wrong for.
import * as Sentry from "@sentry/nextjs";

export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("../sentry.server.config");
  }
  if (process.env.NEXT_RUNTIME === "edge") {
    await import("../sentry.edge.config");
  }
}

// Server-side React errors (a failed server component, a route handler that
// threw during streaming) reach Sentry through this hook only — they never
// touch the browser, so the client SDK cannot see them.
export const onRequestError = Sentry.captureRequestError;
