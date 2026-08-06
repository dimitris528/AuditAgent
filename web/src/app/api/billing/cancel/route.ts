import { proxyJson } from "@/lib/bff";

// BFF for POST /api/v1/billing/cancel. Like the checkout and portal routes it
// sends nothing: WHICH subscription is cancelled is decided server-side from
// the session cookie's JWT. A subscription id in the body would be a way to
// cancel someone else's.
export async function POST() {
  return proxyJson("/api/v1/billing/cancel", {
    method: "POST",
    body: {},
    fallback: "Αποτυχία ακύρωσης συνδρομής.",
  });
}
