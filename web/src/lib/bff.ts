// Server-only helpers shared by the route handlers that proxy to FastAPI.
//
// Every data route does the same three things: read the httpOnly session
// cookie, forward to the backend with a Bearer token, and flatten whatever
// FastAPI returned into the { error } shape the client fetchers expect. The
// browser never sees the token.
import { NextResponse } from "next/server";
import { getToken } from "./session";
import { BackendUnavailableError, backendFetch } from "./backend";

/**
 * Strip anything that reads as machinery rather than as a problem.
 *
 * The last line of defence, and deliberately blunt. server/errors.py already
 * makes sure a database failure never returns its own text, but this function
 * also sees messages nothing on our side wrote — a validation error naming a
 * type, a proxy's HTML, a third-party client — and a user should never be
 * shown a dict, a SQL statement or a stack frame whatever produced it.
 *
 * Replaced wholesale rather than patched: a half-scrubbed dump is still a dump,
 * and the user is no better off reading half of one.
 */
const TECHNICAL =
  /[{}]|\btraceback\b|\bfile "[^"]+", line \d+|\b(select|insert into|update|delete from|alter table)\b|\bpsycopg2\b|\bsqlalchemy\b|https?:\/\/|\b\w+error\b/i;

export function sanitizeMessage(text: string, fallback: string): string {
  if (!text) return fallback;
  // SQLAlchemy appends the statement and the bound parameters in brackets;
  // the parameters are a dict, which is where the braces come from.
  const trimmed = text
    .replace(/\[(SQL|parameters|Background on this error)[^\]]*\]?[\s\S]*/gi, "")
    .split(/\s+/)
    .join(" ")
    .trim();
  if (!trimmed || TECHNICAL.test(trimmed)) return fallback;
  return trimmed;
}

/**
 * Flatten a FastAPI error body into one message.
 *
 * `detail` arrives in three shapes: a plain string (HTTPException), an array of
 * {msg} objects (request validation), or an object carrying a message plus
 * structured data (the duplicate guard). All three have to read as something.
 */
export function errorMessage(data: unknown, fallback: string): string {
  const detail = (data as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return sanitizeMessage(detail, fallback);
  if (Array.isArray(detail)) {
    const joined = detail
      .map((d) => (d as { msg?: string })?.msg)
      .filter(Boolean)
      .join(" · ");
    return sanitizeMessage(joined, fallback);
  }
  const message = (detail as { message?: string } | null)?.message;
  return sanitizeMessage(message ?? "", fallback);
}

/**
 * The 502 returned when the backend could not be reached.
 *
 * Shared so every route answers identically. The message carries the RESOLVED
 * base URL, which is the one fact that makes this failure diagnosable — the
 * outage this was written for showed a generic "backend unavailable" while the
 * backend was healthy and the frontend was calling localhost.
 */
export function backendUnavailable(err: unknown): NextResponse {
  return NextResponse.json(
    {
      error:
        err instanceof BackendUnavailableError
          ? err.message
          : "Το backend δεν είναι διαθέσιμο.",
    },
    { status: 502 },
  );
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
    res = await backendFetch(path, {
      method: options.method ?? "GET",
      headers: {
        Authorization: `Bearer ${token}`,
        ...(hasBody ? { "Content-Type": "application/json" } : {}),
      },
      // Serialised to a string, so a retry can replay it safely.
      body: hasBody ? JSON.stringify(options.body) : undefined,
    });
  } catch (err) {
    return NextResponse.json(
      {
        error:
          err instanceof BackendUnavailableError
            ? err.message
            : "Το backend δεν είναι διαθέσιμο.",
      },
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

/**
 * Proxy a FILE download from the backend.
 *
 * Separate from proxyJson because the response is not JSON and must not be
 * re-serialised: the CSV carries a UTF-8 BOM that Excel needs, and round
 * -tripping it through NextResponse.json() would both corrupt those bytes and
 * throw away the Content-Disposition that makes the browser save it.
 *
 * This exists at all because a plain <a download> cannot send the bearer
 * token — the session lives in an httpOnly cookie, so the download has to be
 * proxied server-side exactly like every other data call.
 */
export async function proxyDownload(
  path: string,
  options: { fallback: string; filename?: string },
): Promise<Response> {
  const token = await getToken();
  if (!token) {
    return NextResponse.json({ error: "Απαιτείται σύνδεση." }, { status: 401 });
  }

  let res: Response;
  try {
    res = await backendFetch(path, {
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch (err) {
    return NextResponse.json(
      {
        error:
          err instanceof BackendUnavailableError
            ? err.message
            : "Το backend δεν είναι διαθέσιμο.",
      },
      { status: 502 },
    );
  }

  if (!res.ok) {
    // An error IS json — surface it the normal way so the UI can show it.
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(
      { error: errorMessage(data, options.fallback) },
      { status: res.status },
    );
  }

  // Streamed straight through, bytes untouched.
  return new Response(res.body, {
    status: 200,
    headers: {
      "Content-Type": res.headers.get("Content-Type") ?? "text/csv; charset=utf-8",
      "Content-Disposition":
        res.headers.get("Content-Disposition") ??
        `attachment; filename="${options.filename ?? "export.csv"}"`,
      "Cache-Control": "no-store",
    },
  });
}
