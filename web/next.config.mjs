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
};

export default nextConfig;
