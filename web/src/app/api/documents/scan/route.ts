import { NextResponse } from "next/server";
import { errorMessage } from "@/lib/bff";
import { API_BASE_URL, getToken } from "@/lib/session";

// BFF for invoice OCR. Multipart rather than JSON, so it does not go through
// proxyJson: the upload is re-assembled with req.formData() and posted on with
// a fresh boundary. undici sets the Content-Type (boundary included) from the
// FormData itself, so it must NOT be set by hand here — a copied header would
// name the boundary of the request we already consumed.
//
// A scan is one Claude call over a whole document and routinely takes 20-40s,
// well past the default serverless timeout on some hosts.
export const maxDuration = 120;

export async function POST(req: Request) {
  const token = await getToken();
  if (!token) {
    return NextResponse.json({ error: "Απαιτείται σύνδεση." }, { status: 401 });
  }

  let form: FormData;
  try {
    form = await req.formData();
  } catch {
    return NextResponse.json(
      { error: "Μη έγκυρο αρχείο." },
      { status: 400 },
    );
  }
  if (!(form.get("file") instanceof File)) {
    return NextResponse.json(
      { error: "Δεν στάλθηκε αρχείο." },
      { status: 400 },
    );
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/api/v1/documents/scan`, {
      method: "POST",
      cache: "no-store",
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    });
  } catch {
    return NextResponse.json(
      { error: "Το backend δεν είναι διαθέσιμο." },
      { status: 502 },
    );
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    return NextResponse.json(
      { error: errorMessage(data, "Αποτυχία σάρωσης παραστατικού.") },
      { status: res.status },
    );
  }
  return NextResponse.json(data);
}
