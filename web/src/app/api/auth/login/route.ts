import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { SESSION_COOKIE, apiBase } from "@/lib/constants";

const API_BASE_URL = apiBase();

// BFF: forward credentials to FastAPI, then stash the returned JWT in an
// httpOnly cookie so browser JS can never read the token.
export async function POST(req: Request) {
  let body: { username?: string; password?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Μη έγκυρο αίτημα." }, { status: 400 });
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: body.username, password: body.password }),
      cache: "no-store",
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
      { error: data?.detail || "Αποτυχία σύνδεσης." },
      { status: res.status },
    );
  }

  const maxAge = (data.expires_hours ?? 12) * 3600;
  cookies().set(SESSION_COOKIE, data.access_token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge,
  });

  return NextResponse.json({
    ok: true,
    username: data.username,
    demo: data.demo,
  });
}
