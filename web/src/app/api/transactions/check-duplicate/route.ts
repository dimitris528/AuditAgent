import { proxyJson } from "@/lib/bff";

// Static segment, so it wins over ../[id] and a POST here never lands on the
// per-transaction routes.
export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  return proxyJson("/api/v1/transactions/check-duplicate", {
    method: "POST",
    body,
    fallback: "Αποτυχία ελέγχου διπλοεγγραφής.",
  });
}
