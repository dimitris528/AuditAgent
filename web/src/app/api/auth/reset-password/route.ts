import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/backend";
import { backendUnavailable, errorMessage } from "@/lib/bff";

// BFF for spending a reset token. Unauthenticated, like forgot-password.
//
// Deliberately does NOT set a session cookie on success: the backend issues no
// token, and the user logs in with the password they just chose — which is what
// proves it is the one they think they set.
export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));

  let res: Response;
  try {
    res = await backendFetch("/api/v1/auth/reset-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: body?.token, password: body?.password }),
    });
  } catch (err) {
    return backendUnavailable(err);
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    return NextResponse.json(
      { error: errorMessage(data, "Αποτυχία επαναφοράς κωδικού.") },
      { status: res.status },
    );
  }
  return NextResponse.json(data);
}
