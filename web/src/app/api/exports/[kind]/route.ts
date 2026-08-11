import { proxyDownload } from "@/lib/bff";
import { EXPORT_KINDS, type ExportKind } from "@/lib/exports";

// BFF for the CSV exports — one handler for all three tables, because they
// differ only in the backend path. The browser hits this same-origin URL
// directly from an <a download>, so the file has to arrive as bytes with its
// Content-Disposition intact — see proxyDownload.
//
// Only the whitelisted filter params are forwarded: these URLs are user-visible
// and shareable, and passing the query string through wholesale would let
// anything appended to it reach the backend.
const FORWARDED = ["year", "quarter", "month", "client_id", "dialect"] as const;

function isExportKind(value: string): value is ExportKind {
  return (EXPORT_KINDS as string[]).includes(value);
}

export async function GET(
  req: Request,
  // params is a Promise in Next 15.
  { params }: { params: Promise<{ kind: string }> },
) {
  const { kind } = await params;
  // Rejected here rather than forwarded: the segment lands in a URL path, and
  // an unchecked one would let a crafted link reach any backend GET.
  if (!isExportKind(kind)) {
    return new Response("Άγνωστος τύπος εξαγωγής.", { status: 404 });
  }

  const incoming = new URL(req.url).searchParams;
  const qs = new URLSearchParams();
  for (const key of FORWARDED) {
    const value = incoming.get(key);
    if (value) qs.set(key, value);
  }
  const suffix = qs.toString() ? `?${qs}` : "";
  return proxyDownload(`/api/v1/exports/${kind}.csv${suffix}`, {
    fallback: "Αποτυχία εξαγωγής.",
    filename: `${kind}.csv`,
  });
}
