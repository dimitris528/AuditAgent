import { NextResponse } from "next/server";
import { proxyUpload } from "@/lib/bff";

// BFF for the ONE-SHOT importer — upload and go, no mapping step. Still the
// path taken when the file's own headings are recognised; the two-step flow
// (analyze → process) is for the files where they are not.
//
// A few thousand rows is a few thousand duplicate-checked inserts, which is
// well past the default serverless timeout on some hosts.
export const maxDuration = 120;

// The two importers the backend actually mounts. Whitelisted rather than
// interpolated: `kind` comes from the URL, and forwarding it unchecked would
// let a caller aim this proxy at any path under /api/import/.
const KINDS = new Set(["clients", "transactions"]);

export async function POST(
  req: Request,
  { params }: { params: Promise<{ kind: string }> },
) {
  const { kind } = await params;
  if (!KINDS.has(kind)) {
    return NextResponse.json(
      { error: "Άγνωστος τύπος εισαγωγής." },
      { status: 404 },
    );
  }
  return proxyUpload(req, `/api/import/${kind}`, {
    fallback: "Αποτυχία εισαγωγής δεδομένων.",
  });
}
