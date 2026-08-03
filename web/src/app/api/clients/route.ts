import { NextResponse } from "next/server";
import { getToken, API_BASE_URL } from "@/lib/session";

// BFF for the client list + inline creation. Reads the httpOnly session cookie
// and forwards to FastAPI with the Bearer token, so the browser never sees it.
export async function GET(req: Request) {
  const token = await getToken();
  if (!token) {
    return NextResponse.json({ error: "Απαιτείται σύνδεση." }, { status: 401 });
  }
  const includeArchived = new URL(req.url).searchParams.get("include_archived");
  const qs = includeArchived === null ? "" : `?include_archived=${includeArchived}`;

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/api/v1/clients${qs}`, {
      cache: "no-store",
      headers: { Authorization: `Bearer ${token}` },
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
      { error: data?.detail || "Αποτυχία φόρτωσης πελατών." },
      { status: res.status },
    );
  }
  return NextResponse.json(data);
}

export async function POST(req: Request) {
  const token = await getToken();
  if (!token) {
    return NextResponse.json({ error: "Απαιτείται σύνδεση." }, { status: 401 });
  }
  const body = await req.json().catch(() => ({}));

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/api/v1/clients`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
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
      { error: data?.detail || "Αποτυχία δημιουργίας πελάτη." },
      { status: res.status },
    );
  }
  return NextResponse.json(data, { status: 201 });
}
