import { proxyJson } from "@/lib/bff";

// Static segment, so it wins over ../[id] — which has no POST handler and
// would otherwise 405.
export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  return proxyJson("/api/v1/clients/check-duplicate", {
    method: "POST",
    body,
    fallback: "Αποτυχία ελέγχου διπλοεγγραφής.",
  });
}
