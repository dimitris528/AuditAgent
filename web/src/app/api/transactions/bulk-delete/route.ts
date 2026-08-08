import { proxyJson } from "@/lib/bff";

// BFF for bulk transaction deletion. Static segment, so it resolves ahead of
// the sibling [id] routes.
export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  return proxyJson(`/api/transactions/bulk-delete`, {
    method: "POST",
    body,
    fallback: "Αποτυχία διαγραφής κινήσεων.",
  });
}
