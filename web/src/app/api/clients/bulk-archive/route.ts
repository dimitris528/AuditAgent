import { proxyJson } from "@/lib/bff";

// BFF for bulk client archive/restore. `archived: false` in the body restores;
// the backend owns that flag, so nothing here needs to know the direction.
export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  return proxyJson(`/api/clients/bulk-archive`, {
    method: "POST",
    body,
    fallback: "Αποτυχία αρχειοθέτησης πελατών.",
  });
}
