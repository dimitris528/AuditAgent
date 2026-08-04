import { proxyJson } from "@/lib/bff";

// BFF for POST /api/v1/billing/portal. Like the checkout route, it sends
// nothing: which Stripe customer's portal opens is decided server-side from the
// session cookie's JWT. A customer id in the body would be an invitation to
// read someone else's invoices.
export async function POST() {
  return proxyJson("/api/v1/billing/portal", {
    method: "POST",
    body: {},
    fallback: "Αποτυχία ανοίγματος διαχείρισης συνδρομής.",
  });
}
