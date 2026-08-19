// Shared Sentry policy for all three Next.js runtimes (browser, Node server,
// Edge middleware). Imported by sentry.server.config.ts, sentry.edge.config.ts
// and src/instrumentation-client.ts so the three inits cannot drift into
// disagreeing about what is worth reporting or what must never leave the
// process.
//
// Nothing here imports the Sentry SDK: these are plain values and functions the
// caller hands to `Sentry.init`. That keeps the module importable from the Edge
// runtime and from tests without dragging the SDK in.
import type { ErrorEvent, EventHint } from "@sentry/nextjs";

/**
 * The DSN, read from NEXT_PUBLIC_SENTRY_DSN.
 *
 * NEXT_PUBLIC_ is required for the browser half — the client bundle can only
 * see variables inlined at build time, and this is one of the few values that
 * genuinely has to be. It is not a secret: a DSN is a write-only ingest key and
 * ships in the JavaScript of every site that uses Sentry.
 *
 * IMPORTANT (and the reason next.config.mjs has no `env` block): NEXT_PUBLIC_
 * variables are baked in when `next build` runs, NOT when the server starts. On
 * Render the build and the run are separate steps, so this must be set on the
 * service BEFORE the build — setting it afterwards leaves an undefined DSN in
 * the bundle and the browser reports nothing, silently.
 *
 * Unset => Sentry is never initialised and the app behaves exactly as it did
 * before this module existed. That is the intended state for local work.
 */
export const SENTRY_DSN = process.env.NEXT_PUBLIC_SENTRY_DSN?.trim() || "";

export const SENTRY_ENABLED = Boolean(SENTRY_DSN);

/** "production" | "development" | whatever a deployment names its stage. */
export const SENTRY_ENVIRONMENT =
  process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT?.trim() ||
  process.env.SENTRY_ENVIRONMENT?.trim() ||
  (process.env.NODE_ENV === "production" ? "production" : "development");

/**
 * Performance sampling. Traces are the expensive half of Sentry's quota, and a
 * dashboard makes several requests per page — 10% in production is enough to
 * see a latency regression and cheap enough to leave on forever. Everything is
 * traced in development, where the volume is one developer.
 */
export const TRACES_SAMPLE_RATE = (() => {
  const configured = Number(process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE);
  if (Number.isFinite(configured) && configured >= 0 && configured <= 1) {
    return configured;
  }
  return SENTRY_ENVIRONMENT === "production" ? 0.1 : 1.0;
})();

/** Release tag, so a spike can be tied to the deploy that caused it. Render
 *  exposes the commit as RENDER_GIT_COMMIT at build time. */
export const SENTRY_RELEASE =
  process.env.NEXT_PUBLIC_SENTRY_RELEASE?.trim() ||
  process.env.RENDER_GIT_COMMIT?.trim() ||
  undefined;

/**
 * Noise that is not a bug in this application.
 *
 * Browser error reporting is mostly other people's software: extensions
 * injecting scripts, network hiccups mid-navigation, and the two ResizeObserver
 * warnings every charting library produces. Left unfiltered they bury the real
 * exceptions, which is the failure mode that makes teams stop reading Sentry.
 */
export const IGNORE_ERRORS = [
  // A navigation or a component unmount cancelled an in-flight fetch. Normal.
  "AbortError",
  "The user aborted a request",
  "The operation was aborted",
  // Offline, a dropped connection, or an ad-blocker refusing a request.
  "Failed to fetch",
  "NetworkError when attempting to fetch resource",
  "Load failed",
  // Benign, and emitted by Recharts on every responsive resize.
  "ResizeObserver loop limit exceeded",
  "ResizeObserver loop completed with undelivered notifications",
  // Next.js aborts rendering deliberately for redirect()/notFound().
  "NEXT_REDIRECT",
  "NEXT_NOT_FOUND",
];

/** Errors thrown by code that is not ours and cannot be fixed by us. */
export const DENY_URLS = [
  /extensions\//i,
  /^chrome:\/\//i,
  /^chrome-extension:\/\//i,
  /^moz-extension:\/\//i,
  /^safari-web-extension:\/\//i,
];

// --------------------------------------------------------------------------
// Scrubbing
// --------------------------------------------------------------------------
// This is an accounting product: an error payload can carry a client's ΑΦΜ, a
// counterparty's name, a login email or — worst — the password field of the
// form that just failed. None of it is needed to fix a stack trace, and all of
// it would be personal data leaving the EU boundary the privacy policy
// promises. So the event is walked and anything that looks sensitive is
// replaced before the SDK is allowed to send it.

/** Keys whose VALUE is dropped outright, matched case-insensitively anywhere in
 *  the key (so "user_password" and "X-Api-Key" both match). */
const SENSITIVE_KEY = /pass|secret|token|auth|cookie|session|jwt|otp|mfa|api[-_]?key|dsn|credential|signature|afm/i;

/** Keys that are personal data rather than credentials: kept as a shape, not a
 *  value, because "the email was malformed" is a useful thing to know and the
 *  address itself is not. */
const PII_KEY = /email|full[-_]?name|phone|contact|address|iban/i;

const REDACTED = "[redacted]";

const EMAIL_PATTERN = /[\w.+-]+@[\w-]+\.[\w.-]+/g;
/** A bearer token, a JWT, or any long opaque blob that reads like a credential. */
const TOKEN_PATTERN = /\b(?:ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*|[A-Za-z0-9_-]{40,})\b/g;

/** Mask what a free-text value may have swallowed — a URL with a reset token in
 *  it, an exception message quoting the address that failed. */
export function scrubString(value: string): string {
  return value
    .replace(EMAIL_PATTERN, "[email]")
    .replace(TOKEN_PATTERN, "[token]");
}

/**
 * Recursively redact an event payload in place-safe fashion (returns a new
 * value; the input is never mutated).
 *
 * Depth-bounded: a Sentry event is a tree of unknown depth built from user
 * data, and an unbounded walk over a cyclic structure would hang the browser
 * inside an error handler — the one place a hang is hardest to diagnose.
 */
export function scrub(value: unknown, depth = 0): unknown {
  if (depth > 8) return REDACTED;
  if (typeof value === "string") return scrubString(value);
  if (Array.isArray(value)) return value.map((item) => scrub(item, depth + 1));
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (SENSITIVE_KEY.test(key)) {
        out[key] = REDACTED;
      } else if (PII_KEY.test(key)) {
        // Typed as "there was a value here, of this kind" — enough to tell a
        // missing field from a rejected one without carrying the person.
        out[key] = typeof item === "string" ? REDACTED : scrub(item, depth + 1);
      } else {
        out[key] = scrub(item, depth + 1);
      }
    }
    return out;
  }
  return value;
}

/**
 * True when the error is one this application raises ON PURPOSE and has already
 * handled — an expired session (401), a row that belongs to somebody else
 * (404), a lapsed subscription (402), a duplicate invoice (409).
 *
 * Every one of those is the product working correctly. Reporting them would
 * turn Sentry into a log of ordinary user behaviour, and the 401s alone (every
 * session expiry, every logged-out visitor) would drown the crashes that matter.
 * Anything 5xx is ours and is always reported.
 */
export function isExpectedClientError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const status = (error as { status?: unknown }).status;
  return typeof status === "number" && status >= 400 && status < 500;
}

/**
 * The `beforeSend` every runtime installs: drop the expected, scrub the rest.
 *
 * Returning null discards the event entirely — the SDK sends nothing, so this
 * is a real filter and not merely a display preference.
 */
export function beforeSend(
  event: ErrorEvent,
  hint: EventHint,
): ErrorEvent | null {
  if (isExpectedClientError(hint?.originalException)) return null;

  // Cookies carry the httpOnly session JWT; on the server half of Next they are
  // attached to every event by default. Dropped wholesale rather than filtered:
  // there is nothing in them worth a bug report.
  if (event.request) {
    delete event.request.cookies;
    if (event.request.headers) {
      event.request.headers = scrub(event.request.headers) as Record<string, string>;
    }
    if (event.request.query_string) {
      event.request.query_string = scrub(event.request.query_string) as typeof event.request.query_string;
    }
    if (event.request.data !== undefined) {
      event.request.data = scrub(event.request.data);
    }
    if (typeof event.request.url === "string") {
      event.request.url = scrubString(event.request.url);
    }
  }

  // Identify the tenant by their opaque id only — never by email or name.
  if (event.user) {
    event.user = { id: event.user.id };
  }

  if (event.extra) event.extra = scrub(event.extra) as Record<string, unknown>;
  if (event.contexts) {
    event.contexts = scrub(event.contexts) as NonNullable<ErrorEvent["contexts"]>;
  }
  if (event.breadcrumbs) {
    event.breadcrumbs = event.breadcrumbs.map((crumb) => ({
      ...crumb,
      message: crumb.message ? scrubString(crumb.message) : crumb.message,
      data: crumb.data ? (scrub(crumb.data) as Record<string, unknown>) : crumb.data,
    }));
  }
  if (event.exception?.values) {
    event.exception.values = event.exception.values.map((value) => ({
      ...value,
      value: value.value ? scrubString(value.value) : value.value,
    }));
  }
  if (event.message) event.message = scrubString(event.message);

  return event;
}

/** Everything `Sentry.init` needs, shared by the three runtimes. */
export const baseSentryOptions = {
  dsn: SENTRY_DSN,
  environment: SENTRY_ENVIRONMENT,
  release: SENTRY_RELEASE,
  tracesSampleRate: TRACES_SAMPLE_RATE,
  // The SDK's own PII switch. Off means no IP addresses, no request bodies and
  // no cookies are attached in the first place — `beforeSend` above is the
  // second line, for the data our own code puts on an event.
  sendDefaultPii: false,
  ignoreErrors: IGNORE_ERRORS,
  denyUrls: DENY_URLS,
  beforeSend,
} as const;
