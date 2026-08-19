import { withSentryConfig } from "@sentry/nextjs";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // There is deliberately NO `env` block here.
  //
  // Next's `env` config INLINES values at BUILD time: it rewrites every
  // `process.env.X` reference into a string literal in the bundle. This file
  // used to carry
  //
  //     env: { API_BASE_URL: process.env.API_BASE_URL || "http://localhost:8000" }
  //
  // and that is exactly what broke production. Render builds and runs in two
  // separate steps, and `API_BASE_URL` is supplied at RUNTIME (render.yaml wires
  // it with `fromService`). At build time it was unset, so the fallback got
  // baked in and the deployed server called http://localhost:8000 forever —
  // while the API service itself logged perfectly healthy 200s from Render's
  // health checks. The symptom ("backend unavailable" with a green backend) was
  // a build artefact, not a network or CORS problem.
  //
  // Server-side code reads process.env at runtime in Node with no config at
  // all. Adding it back here would silently re-break the deploy, so don't —
  // see web/src/lib/backend.ts, which resolves the URL per call.
  //
  // NEXT_PUBLIC_SENTRY_DSN is the one variable that genuinely IS build-time
  // (the browser bundle can see nothing else), which is why it carries that
  // prefix and why web/src/lib/monitoring.ts spells out that it must be set
  // before the build rather than after it.
};

// Wraps the config with the Sentry build plugin. Everything below is off unless
// the deployment opts in:
//
//   * source maps are uploaded only when SENTRY_AUTH_TOKEN, SENTRY_ORG and
//     SENTRY_PROJECT are all set. Without them the plugin logs one line and
//     skips the upload, so `npm ci && npm run build` in CI needs no secrets and
//     a fork's pull request still builds.
//   * the tunnel route is opt-in for the same reason it exists: it proxies
//     browser events through this origin so an ad-blocker cannot silently drop
//     them, at the cost of putting that traffic on our own service.
export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,

  // The plugin is chatty on every build otherwise, including the builds that
  // are deliberately not uploading anything.
  silent: !process.env.CI,

  // Uploaded maps are DELETED from the build output afterwards, so the stack
  // traces are readable in Sentry and the original sources are not served to
  // the public from our own domain.
  sourcemaps: { deleteSourcemapsAfterUpload: true },

  // Strips the SDK's own debug logging from the production bundle. Under
  // `webpack.treeshake` rather than the older top-level `disableLogger`, which
  // this SDK version warns about on every build.
  webpack: { treeshake: { removeDebugLogging: true } },

  // Off by default: see above. Set SENTRY_TUNNEL_ROUTE=/monitoring to enable.
  tunnelRoute: process.env.SENTRY_TUNNEL_ROUTE || undefined,
});
