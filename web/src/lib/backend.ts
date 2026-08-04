// SERVER-ONLY. How the Next server reaches FastAPI: where it is, and how to
// survive a cold start getting there.
//
// Architecture note, because it decides everything below: the browser NEVER
// calls FastAPI. It calls same-origin Next route handlers (/api/...), which
// read the httpOnly session cookie and proxy onward with a Bearer token. That
// is why there is no NEXT_PUBLIC_* API URL — a public variable is inlined into
// the CLIENT bundle, and the only reason the browser would need the API's
// address is if it were talking to it directly, which would mean handing the
// browser the JWT and giving up the httpOnly cookie. It is also why CORS is
// not on the critical path in production: every browser request is
// same-origin.

/** Where the API lives when nothing is configured and we are NOT in dev.
 *
 *  A last resort, not the normal path: render.yaml wires API_BASE_URL from the
 *  API service. It exists so a missing variable degrades to "probably right"
 *  instead of to localhost, which is unreachable from a deployed container and
 *  produced exactly the failure this module was written to fix.
 *
 *  Override with API_BASE_URL (or API_BASE_URL_FALLBACK) if the service was
 *  renamed — Render mints host names from the service name and sometimes adds
 *  a suffix. */
const PRODUCTION_FALLBACK = "https://accounting-api.onrender.com";

const DEV_DEFAULT = "http://localhost:8000";

/** Render's free tier spins a service down after ~15 minutes idle and takes
 *  30–60s to wake. A single fetch with Node's default behaviour either hangs
 *  or fails, and the user sees "backend unavailable" for a backend that is
 *  merely asleep. */
const COLD_START_TIMEOUT_MS = 30_000;
const DEFAULT_TIMEOUT_MS = 15_000;
const DEFAULT_RETRIES = 2;

/** Status codes worth trying again. 5xx from a proxy in front of a waking
 *  service, not from the application — a 500 means our own code raised and
 *  retrying just raises it twice. */
const RETRYABLE_STATUS = new Set([502, 503, 504]);

let warnedAboutFallback = false;

/**
 * Resolve the backend base URL, at CALL time.
 *
 * Deliberately a function rather than a module-level constant: a constant is
 * evaluated when the module is first imported, and `next build` imports server
 * modules while collecting page data — so a constant can capture a build-time
 * value and freeze it, which is a subtler version of the bug that broke
 * production. Reading per call costs nothing and cannot go stale.
 *
 * Accepts a bare host (Render's `fromService` injects just the hostname) and
 * upgrades it to https.
 */
export function apiBase(): string {
  const configured =
    process.env.API_BASE_URL?.trim() ||
    // Accepted as an alias so a deployment that set the more familiar name
    // still works. Read SERVER-side only; nothing here reaches the browser.
    process.env.NEXT_PUBLIC_API_URL?.trim() ||
    process.env.API_BASE_URL_FALLBACK?.trim();

  if (configured) return normalise(configured);

  // Nothing configured. In development that means "run the API locally"; in
  // production it means someone forgot, and localhost is guaranteed wrong.
  if (process.env.NODE_ENV === "production") {
    if (!warnedAboutFallback) {
      warnedAboutFallback = true;
      console.error(
        "[backend] API_BASE_URL is not set. Falling back to " +
          `${PRODUCTION_FALLBACK}. Set API_BASE_URL on the web service ` +
          "(render.yaml wires it from the accounting-api service).",
      );
    }
    return PRODUCTION_FALLBACK;
  }
  return DEV_DEFAULT;
}

function normalise(raw: string): string {
  const withScheme = /^https?:\/\//.test(raw) ? raw : `https://${raw}`;
  // A trailing slash would produce "//api/dashboard", which some proxies
  // normalise and others 404 on.
  return withScheme.replace(/\/+$/, "");
}

/** The backend could not be reached at all — as opposed to answering with an
 *  error, which is a normal response the caller should surface. */
export class BackendUnavailableError extends Error {
  constructor(
    message: string,
    readonly baseUrl: string,
    readonly attempts: number,
  ) {
    super(message);
    this.name = "BackendUnavailableError";
  }
}

export interface BackendFetchOptions extends Omit<RequestInit, "signal"> {
  /** Extra attempts after the first. 0 disables retrying — required when the
   *  body is a stream or FormData, which cannot be replayed. */
  retries?: number;
  timeoutMs?: number;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Fetch from the backend with a timeout and cold-start retries.
 *
 * `path` is absolute-from-root ("/api/dashboard"); the base is resolved per
 * call. Retries cover the two things a sleeping Render service does — refuse
 * the connection, or sit behind a 502/503 while it boots — with a widening
 * backoff and a longer timeout on the first attempt, because waking is exactly
 * when the wait is longest.
 *
 * A response is RETURNED even when it carries an error status: only an
 * unreachable backend throws. That keeps "the API said 409" on the normal path
 * and reserves the exception for "there was no API".
 */
export async function backendFetch(
  path: string,
  options: BackendFetchOptions = {},
): Promise<Response> {
  const { retries = DEFAULT_RETRIES, timeoutMs, ...init } = options;
  const base = apiBase();
  const url = `${base}${path}`;
  const attempts = Math.max(1, retries + 1);

  let lastError: unknown;
  for (let attempt = 0; attempt < attempts; attempt++) {
    // The first attempt gets the long window: if the service is asleep this is
    // the request that pays for waking it.
    const budget =
      timeoutMs ?? (attempt === 0 ? COLD_START_TIMEOUT_MS : DEFAULT_TIMEOUT_MS);
    try {
      const res = await fetch(url, {
        ...init,
        cache: "no-store",
        signal: AbortSignal.timeout(budget),
      });
      if (RETRYABLE_STATUS.has(res.status) && attempt < attempts - 1) {
        await sleep(backoffMs(attempt));
        continue;
      }
      return res;
    } catch (err) {
      // AbortError (our timeout) and TypeError (connection refused / DNS) both
      // land here and are both worth another go.
      lastError = err;
      if (attempt < attempts - 1) await sleep(backoffMs(attempt));
    }
  }

  const reason = lastError instanceof Error ? lastError.message : "unknown";
  console.error(
    `[backend] ${init.method ?? "GET"} ${url} failed after ${attempts} ` +
      `attempt(s): ${reason}`,
  );
  throw new BackendUnavailableError(
    `Το backend δεν είναι διαθέσιμο (${base}).`,
    base,
    attempts,
  );
}

/** 400ms, 1200ms, … — long enough to matter on a cold start, short enough not
 *  to blow the platform's own request timeout. */
function backoffMs(attempt: number): number {
  return 400 * 3 ** attempt;
}
