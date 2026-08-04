import { proxyDownload } from "@/lib/bff";

// BFF for the CSV export. The browser hits this same-origin URL directly from
// an <a download>, so the file has to arrive as bytes with its
// Content-Disposition intact — see proxyDownload.
//
// Only the whitelisted filter params are forwarded: this URL is user-visible
// and shareable, and passing the query string through wholesale would let
// anything appended to it reach the backend.
const FORWARDED = ["year", "quarter", "month", "client_id", "dialect"] as const;

export async function GET(req: Request) {
  const incoming = new URL(req.url).searchParams;
  const qs = new URLSearchParams();
  for (const key of FORWARDED) {
    const value = incoming.get(key);
    if (value) qs.set(key, value);
  }
  const suffix = qs.toString() ? `?${qs}` : "";
  return proxyDownload(`/api/v1/exports/transactions.csv${suffix}`, {
    fallback: "Αποτυχία εξαγωγής.",
    filename: "transactions.csv",
  });
}
