import { proxyJson } from "@/lib/bff";

// `params` is a Promise in Next 15.
type Ctx = { params: Promise<{ id: string }> };

export async function POST(req: Request, { params }: Ctx) {
  const { id } = await params;
  const body = await req.json().catch(() => ({}));
  return proxyJson(`/api/v1/transactions/${encodeURIComponent(id)}/settle`, {
    method: "POST",
    body,
    fallback: "Αποτυχία εξόφλησης.",
  });
}
