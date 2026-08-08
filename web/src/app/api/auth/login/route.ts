import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/constants";
import { backendFetch } from "@/lib/backend";
import { backendUnavailable } from "@/lib/bff";

// BFF: forward credentials to FastAPI, then stash the returned JWT in an
// httpOnly cookie so browser JS can never read the token.
//
// Since 2FA, a correct password does not always end in a session. When the
// account has a second factor and this browser is not trusted, the backend
// answers with a CHALLENGE instead, and the browser goes on to
// /api/auth/mfa-verify. The challenge is passed through to the client — it is
// not a session and opens nothing, so it does not belong in the httpOnly
// cookie the way an access token does.
const DEVICE_COOKIE = "device_trust";

export async function POST(req: Request) {
  let body: { username?: string; password?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Μη έγκυρο αίτημα." }, { status: 400 });
  }

  const jar = await cookies();
  let res: Response;
  try {
    res = await backendFetch(`/api/auth/login`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        // The device-trust cookie is forwarded explicitly. The browser sends
        // it to THIS origin; the backend is a separate server-to-server hop
        // that would otherwise never see it, and without it every login from
        // a trusted device would still be challenged.
        ...(jar.get(DEVICE_COOKIE)
          ? { Cookie: `${DEVICE_COOKIE}=${jar.get(DEVICE_COOKIE)!.value}` }
          : {}),
      },
      body: JSON.stringify({ username: body.username, password: body.password }),
      cache: "no-store",
    });
  } catch (err) {
    return backendUnavailable(err);
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    return NextResponse.json(
      { error: data?.detail || "Αποτυχία σύνδεσης." },
      { status: res.status },
    );
  }

  if (data.mfa_required) {
    // No session cookie is set: the password alone must not leave anything
    // behind that could read a book.
    return NextResponse.json({
      ok: false,
      mfa_required: true,
      challenge: data.challenge,
      trust_days: data.trust_days,
    });
  }

  const maxAge = (data.expires_hours ?? 12) * 3600;
  jar.set(SESSION_COOKIE, data.access_token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge,
  });

  return NextResponse.json({
    ok: true,
    username: data.username,
  });
}
