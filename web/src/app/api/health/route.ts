import { NextResponse } from "next/server";
import { apiBase, backendFetch } from "@/lib/backend";

// Deployment diagnostics for the WEB service: which backend URL did this
// container actually resolve, and can it reach it?
//
// This exists because the failure it diagnoses was invisible. The site showed
// "backend unavailable" while the API logged healthy 200s, and nothing exposed
// the one fact that explained it — the deployed bundle had localhost:8000
// compiled in. Hitting /api/health on the web service now answers that in one
// request, without a redeploy or a log dig.
//
// Public and unauthenticated, so it is reachable when auth is the thing that
// is broken. It therefore reveals only the base URL and a reachability
// boolean: no tokens, no database details, no upstream error bodies.
export const dynamic = "force-dynamic";

export async function GET() {
  const base = apiBase();
  const configured = Boolean(
    process.env.API_BASE_URL?.trim() || process.env.NEXT_PUBLIC_API_URL?.trim(),
  );
  // Only a problem when nobody ASKED for loopback. Someone running the
  // production build against a local API has set the variable deliberately and
  // does not need to be told they are wrong; the bug this catches is the
  // opposite case — production, loopback, and nothing configured.
  const pointsAtLocalhost =
    !configured &&
    (base.includes("localhost") || base.includes("127.0.0.1"));

  let backend: "ok" | "unreachable" | "error" = "unreachable";
  let status: number | null = null;
  const started = Date.now();
  try {
    // One quick attempt: this endpoint reports the CURRENT state, and burning
    // 30s of cold-start retries would make a monitor time out instead of
    // telling it the service is asleep.
    const res = await backendFetch("/api/health", {
      retries: 0,
      timeoutMs: 5_000,
    });
    status = res.status;
    backend = res.ok ? "ok" : "error";
  } catch {
    backend = "unreachable";
  }

  return NextResponse.json(
    {
      web: "ok",
      api_base_url: base,
      api_base_url_configured: configured,
      // The signature of the bug this endpoint was added for.
      misconfigured:
        pointsAtLocalhost && process.env.NODE_ENV === "production",
      backend,
      backend_status: status,
      elapsed_ms: Date.now() - started,
    },
    // 503 when the backend cannot be reached, so an uptime check notices
    // without parsing the body.
    { status: backend === "ok" ? 200 : 503 },
  );
}
