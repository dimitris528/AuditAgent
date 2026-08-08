import { proxyJson } from "@/lib/bff";

// BFF for bulk client deletion. A static segment, so it resolves ahead of the
// sibling [id] route rather than being read as a client called "bulk-delete".
//
// The body is forwarded untouched: the backend validates the id list (shape,
// type and length) and is the only place that decides which clients may be
// deleted at all — one that still holds transactions is refused there, with
// its name and row count, and that answer has to reach the UI intact.
export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  return proxyJson(`/api/clients/bulk-delete`, {
    method: "POST",
    body,
    fallback: "Αποτυχία διαγραφής πελατών.",
  });
}
