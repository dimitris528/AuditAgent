import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/constants";
import { backendFetch } from "@/lib/backend";
import { backendUnavailable } from "@/lib/bff";

interface RegisterBody {
  company_name?: string;
  full_name?: string;
  email?: string;
  password?: string;
  accept_terms?: boolean;
  terms_version?: string;
}

// BFF: forward the signup to FastAPI, then stash the returned JWT in an
// httpOnly cookie exactly as the login route does — registering signs you in,
// so the browser never needs to see the token or make a second round trip.
//
// The fields are forwarded EXPLICITLY rather than by spreading the parsed body.
// A pass-through would let a caller post anything the API's model happens to
// accept — `username`, and any field added to it later — from a page that never
// offered it, which is how a public endpoint quietly grows a surface nobody
// designed.
export async function POST(req: Request) {
  let body: RegisterBody;
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
        company_name: body.company_name,
        full_name: body.full_name,
        email: body.email,
        password: body.password,
        accept_terms: body.accept_terms === true,
        terms_version: body.terms_version,
      }),
      cache: "no-store",
      // The password is hashed at 600 000 PBKDF2 iterations server-side, which
      // is not instant on a free-tier container. Retrying a request that may
      // already have created the tenant would produce a confusing 409 on a
      // signup that actually worked.
      retries: 0,
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

  // The token is deliberately NOT echoed: it is in the httpOnly cookie above,
  // and returning it here would put it somewhere the browser's JavaScript can
  // read — which is the whole thing this architecture avoids.
  return NextResponse.json({
    ok: true,
    username: data.username,
    email: data.email,
    company_name: data.company_name,
    full_name: data.full_name,
    role: data.role,
  });
}
