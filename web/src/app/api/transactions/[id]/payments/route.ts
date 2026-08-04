import { proxyJson } from "@/lib/bff";

// `params` is a Promise in Next 15.
type Ctx = { params: Promise<{ id: string }> };

export async function GET(_req: Request, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/api/v1/transactions/${encodeURIComponent(id)}/payments`, {
    fallback: "Αποτυχία φόρτωσης ιστορικού πληρωμών.",
  });
}
