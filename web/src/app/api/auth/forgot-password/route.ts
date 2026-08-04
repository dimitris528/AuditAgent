import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/backend";
import { backendUnavailable, errorMessage } from "@/lib/bff";

// BFF for the reset request. Unauthenticated by design — the whole point is
// that the caller cannot log in.
//
// Not routed through proxyJson because that requires a session cookie. The
// response is passed through verbatim so the UI cannot accidentally start
// distinguishing a known address from an unknown one.
export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));

  let res: Response;
  try {
    res = await backendFetch("/api/v1/auth/forgot-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: body?.email }),
    });
  } catch (err) {
    return backendUnavailable(err);
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    return NextResponse.json(
      { error: errorMessage(data, "Αποτυχία αποστολής συνδέσμου.") },
      { status: res.status },
    );
  }
  return NextResponse.json(data);
}
