import { NextResponse } from "next/server";
import { proxyJson } from "@/lib/bff";

// BFF for the three 2FA management calls. One dynamic route rather than three
// files, with the actions WHITELISTED — `action` comes from the URL, and
// interpolating it unchecked would let a caller aim this proxy at any path
// under /api/v1/auth/.
//
// The password sent to `disable` passes straight through and is never logged
// or stored here; this hop exists only to attach the bearer token the browser
// is deliberately not given.
const ACTIONS: Record<string, string> = {
  setup: "Αποτυχία έναρξης ρύθμισης.",
  enable: "Αποτυχία ενεργοποίησης.",
  disable: "Αποτυχία απενεργοποίησης.",
};

export async function POST(
  req: Request,
  { params }: { params: Promise<{ action: string }> },
) {
  const { action } = await params;
  const fallback = ACTIONS[action];
  if (!fallback) {
    return NextResponse.json({ error: "Άγνωστη ενέργεια." }, { status: 404 });
  }
  // `setup` takes no body; the others do. An absent body is sent as {} rather
  // than omitted, so FastAPI sees a JSON object either way.
  const body = await req.json().catch(() => ({}));
  return proxyJson(`/api/v1/auth/mfa/${action}`, {
    method: "POST",
    body,
    fallback,
  });
}
