import { NextResponse } from "next/server";
import { errorMessage, backendUnavailable } from "@/lib/bff";
import { getToken } from "@/lib/session";
import { backendFetch } from "@/lib/backend";

// BFF for the bulk importer. Multipart rather than JSON, so it does not go
// through proxyJson: the upload is re-assembled with req.formData() and posted
// on with a fresh boundary. undici sets the Content-Type (boundary included)
// from the FormData itself, so it must NOT be set by hand here — a copied
// header would name the boundary of the request we already consumed.
//
// A few thousand rows is a few thousand duplicate-checked inserts, which is
// well past the default serverless timeout on some hosts.
export const maxDuration = 120;

const IMPORT_TIMEOUT_MS = 90_000;

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

  const token = await getToken();
  if (!token) {
    return NextResponse.json({ error: "Απαιτείται σύνδεση." }, { status: 401 });
  }

  let form: FormData;
  try {
    form = await req.formData();
  } catch {
    return NextResponse.json({ error: "Μη έγκυρο αρχείο." }, { status: 400 });
  }
  if (!(form.get("file") instanceof File)) {
    return NextResponse.json({ error: "Δεν στάλθηκε αρχείο." }, { status: 400 });
  }

  let res: Response;
  try {
    res = await backendFetch(`/api/import/${kind}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: form,
      // NO retry: a FormData body is consumed by the first attempt and cannot
      // be replayed, so a second try would post an empty upload — and a retry
      // that DID replay would risk importing the same file twice.
      retries: 0,
      timeoutMs: IMPORT_TIMEOUT_MS,
    });
  } catch (err) {
    return backendUnavailable(err);
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    return NextResponse.json(
      { error: errorMessage(data, "Αποτυχία εισαγωγής δεδομένων.") },
      { status: res.status },
    );
  }
  return NextResponse.json(data);
}
