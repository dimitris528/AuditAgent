import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/constants";
import { backendFetch } from "@/lib/backend";
import { backendUnavailable } from "@/lib/bff";

// BFF: forward the signup to FastAPI, then stash the returned JWT in an
// httpOnly cookie exactly as the login route does — registering signs you in,
// so the browser never needs to see the token or make a second round trip.
export async function POST(req: Request) {
  let body: { username?: string; email?: string; password?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Μη έγκυρο αίτημα." }, { status: 400 });
  }

  let res: Response;
  try {
    res = await backendFetch(`/api/v1/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: body.username,
        email: body.email,
        password: body.password,
      }),
      cache: "no-store",
    });
  } catch (err) {
    return backendUnavailable(err);
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    // FastAPI reports validation failures as a list of objects; flatten to one
    // readable line rather than rendering "[object Object]" in the form.
    const detail = Array.isArray(data?.detail)
      ? data.detail.map((d: { msg?: string }) => d?.msg).filter(Boolean).join(" · ")
      : data?.detail;
    return NextResponse.json(
      { error: detail || "Αποτυχία εγγραφής." },
      { status: res.status },
    );
  }

  const maxAge = (data.expires_hours ?? 12) * 3600;
  (await cookies()).set(SESSION_COOKIE, data.access_token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge,
  });

  return NextResponse.json({
    ok: true,
    username: data.username,
    email: data.email,
  });
}
