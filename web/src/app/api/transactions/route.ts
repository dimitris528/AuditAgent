import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/constants";
import { backendFetch } from "@/lib/backend";
import { backendUnavailable } from "@/lib/bff";

// BFF proxy for writes: read the httpOnly session cookie and forward to FastAPI
// with the Bearer token. The tenant is derived server-side from the token, so
// the client cannot spoof another user.
export async function POST(req: Request) {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) {
    return NextResponse.json({ error: "Απαιτείται σύνδεση." }, { status: 401 });
  }

  const body = await req.json().catch(() => ({}));
  let res: Response;
  try {
    res = await backendFetch(`/api/transactions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch (err) {
    return backendUnavailable(err);
  }

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
