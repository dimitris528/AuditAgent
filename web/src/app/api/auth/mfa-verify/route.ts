import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/constants";
import { backendFetch } from "@/lib/backend";
import { backendUnavailable } from "@/lib/bff";

// BFF for leg two of a 2FA login. Public like /api/auth/login, and for the
// same reason: it carries the challenge from leg one, which is itself the
// proof that the password was already given.
//
// Two httpOnly cookies come out of a successful verification:
//
//   session       the access token, exactly as an ordinary login sets it;
//   device_trust  only when "trust this device" was ticked. Set HERE rather
//                 than forwarded from the backend's own Set-Cookie, because
//                 that response belongs to a server-to-server hop the browser
//                 never sees — the cookie has to be minted on the response
//                 that actually reaches it.
//
// Neither is readable by page script, so a 2FA bypass cannot be lifted by an
// XSS that can already read anything in `document.cookie`.
const DEVICE_COOKIE = "device_trust";
const TRUST_DAYS = 30;

export async function POST(req: Request) {
  let body: { challenge?: string; code?: string; trust_device?: boolean };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Μη έγκυρο αίτημα." }, { status: 400 });
  }

  let res: Response;
  try {
    res = await backendFetch(`/api/v1/auth/mfa/verify`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        // The BROWSER's user-agent, forwarded deliberately. Without it the
        // backend sees this hop's own agent and every trusted device is
        // labelled "node" — which defeats the only reason the label exists,
        // namely letting someone tell one device from another before revoking
        // it. Forwarded, not fingerprinted: it names a browser, it does not
        // identify a machine.
        "X-Device-Agent": req.headers.get("user-agent") ?? "",
      },
      body: JSON.stringify({
        challenge: body.challenge,
        code: body.code,
        trust_device: Boolean(body.trust_device),
      }),
      cache: "no-store",
    });
  } catch (err) {
    return backendUnavailable(err);
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    return NextResponse.json(
      { error: data?.detail || "Η επαλήθευση απέτυχε." },
      { status: res.status },
    );
  }

  const secure = process.env.NODE_ENV === "production";
  const jar = await cookies();
  jar.set(SESSION_COOKIE, data.access_token, {
    httpOnly: true,
    sameSite: "lax",
    secure,
    path: "/",
    maxAge: (data.expires_hours ?? 12) * 3600,
  });

  if (data.device_token) {
    jar.set(DEVICE_COOKIE, data.device_token, {
      httpOnly: true,
      sameSite: "lax",
      secure,
      path: "/",
      maxAge: TRUST_DAYS * 24 * 3600,
    });
  }

  return NextResponse.json({ ok: true, username: data.username });
}
