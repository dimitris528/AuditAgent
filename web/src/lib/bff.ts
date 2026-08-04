// Server-only helpers shared by the route handlers that proxy to FastAPI.
//
// Every data route does the same three things: read the httpOnly session
// cookie, forward to the backend with a Bearer token, and flatten whatever
// FastAPI returned into the { error } shape the client fetchers expect. The
// browser never sees the token.
import { NextResponse } from "next/server";
import { API_BASE_URL, getToken } from "./session";

/**
 * Flatten a FastAPI error body into one message.
 *
 * `detail` arrives in three shapes: a plain string (HTTPException), an array of
 * {msg} objects (request validation), or an object carrying a message plus
 * structured data (the duplicate guard). All three have to read as something.
 */
export function errorMessage(data: unknown, fallback: string): string {
  const detail = (data as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail || fallback;
  if (Array.isArray(detail)) {
    const joined = detail
      .map((d) => (d as { msg?: string })?.msg)
      .filter(Boolean)
      .join(" · ");
    return joined || fallback;
  }
  const message = (detail as { message?: string } | null)?.message;
  return message || fallback;
}

/**
 * Proxy a JSON request to the backend.
 *
 * On failure the ORIGINAL body is passed through alongside the flattened
 * message: the duplicate guard's 409 carries the row it matched, and the form
 * needs that row to show the user what they collided with.
 */
export async function proxyJson(
  path: string,
  options: { method?: string; body?: unknown; fallback: string },
): Promise<NextResponse> {
  const token = await getToken();
  if (!token) {
    return NextResponse.json({ error: "Απαιτείται σύνδεση." }, { status: 401 });
  }

  const hasBody = options.body !== undefined;
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method: options.method ?? "GET",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${token}`,
        ...(hasBody ? { "Content-Type": "application/json" } : {}),
      },
      body: hasBody ? JSON.stringify(options.body) : undefined,
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
      { ...(data as object), error: errorMessage(data, options.fallback) },
      { status: res.status },
    );
  }
  return NextResponse.json(data);
}
