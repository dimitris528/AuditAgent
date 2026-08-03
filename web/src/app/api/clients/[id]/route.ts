import { NextResponse } from "next/server";
import { getToken, API_BASE_URL } from "@/lib/session";

// BFF for one client: the drawer payload (GET) and edits/archive (PUT).
// `params` is a Promise in Next 15.
type Ctx = { params: Promise<{ id: string }> };

export async function GET(req: Request, { params }: Ctx) {
  const token = await getToken();
  if (!token) {
    return NextResponse.json({ error: "Απαιτείται σύνδεση." }, { status: 401 });
  }
  const { id } = await params;
  // Forward the period so the drawer shows the same window as the dashboard.
  const src = new URL(req.url).searchParams;
  const qs = new URLSearchParams();
  for (const key of ["year", "quarter", "month"]) {
    const value = src.get(key);
    if (value) qs.set(key, value);
  }
  const suffix = qs.toString() ? `?${qs}` : "";

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/api/v1/clients/${id}${suffix}`, {
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
      { error: data?.detail || "Ο πελάτης δεν βρέθηκε." },
      { status: res.status },
    );
  }
  return NextResponse.json(data);
}

export async function PUT(req: Request, { params }: Ctx) {
  const token = await getToken();
  if (!token) {
    return NextResponse.json({ error: "Απαιτείται σύνδεση." }, { status: 401 });
  }
  const { id } = await params;
  const body = await req.json().catch(() => ({}));

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/api/v1/clients/${id}`, {
      method: "PUT",
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
    const detail = Array.isArray(data?.detail)
      ? data.detail.map((d: { msg?: string }) => d?.msg).filter(Boolean).join(" · ")
      : data?.detail;
    return NextResponse.json(
      { error: detail || "Αποτυχία αποθήκευσης." },
      { status: res.status },
    );
  }
  return NextResponse.json(data);
}
