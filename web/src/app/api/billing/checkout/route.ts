import { proxyJson } from "@/lib/bff";

// BFF for POST /api/v1/billing/checkout. Nothing to send — the tenant comes
// from the session cookie's JWT, and the price is fixed server-side, so the
// browser cannot ask to be charged for something other than the one plan.
//
// `body: {}` rather than no body: proxyJson only sets Content-Type when a body
// is present, and FastAPI is happy either way here, but an explicit empty
// object keeps this a normal JSON POST.
export async function POST() {
  return proxyJson("/api/v1/billing/checkout", {
    method: "POST",
    body: {},
    fallback: "Αποτυχία έναρξης πληρωμής.",
  });
}
