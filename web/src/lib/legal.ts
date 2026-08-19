// The legal documents' identity, mirrored from server/legal.py.
//
// Duplicated rather than fetched, and deliberately: /terms and /privacy are
// static pages that must render for a visitor with no session and no reachable
// backend, so they cannot depend on an API call to know what version they are.
// The signup form sends CONSENT_VERSION back with the registration, and the API
// records what it was told — so if these two ever drift, the stored consent
// still says which text the user actually saw, which is the only thing the
// record has to be right about.
//
// Keep in step with server/legal.py. Its values are also published on
// GET /api/meta, so the drift is observable rather than silent.

/** Terms of Service (Όροι Χρήσης). */
export const TERMS_VERSION = "2026-08-20";

/** Privacy Policy (Πολιτική Απορρήτου). Versioned separately because the two
 *  change for different reasons — a pricing change touches the Terms, a new
 *  sub-processor touches the Privacy Policy. */
export const PRIVACY_VERSION = "2026-08-20";

/** What gets stored on the user row. One string covering both documents,
 *  because a single checkbox accepts them together. */
export const CONSENT_VERSION = `terms:${TERMS_VERSION}|privacy:${PRIVACY_VERSION}`;

/** Rendered as "Τελευταία ενημέρωση: 20 Αυγούστου 2026". */
export function formatVersionDate(version: string): string {
  const [year, month, day] = version.split("-").map(Number);
  if (!year || !month || !day) return version;
  return new Intl.DateTimeFormat("el-GR", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(new Date(Date.UTC(year, month - 1, day)));
}

/** The company behind the service, as it appears in both documents.
 *
 * Placeholders where a real legal entity's details belong. They are marked
 * with a visible «…» rather than invented, because a privacy policy naming a
 * fictitious data controller is worse than one that is obviously incomplete:
 * the first is a false statement to a supervisory authority, the second is a
 * to-do. Fill these in before launch. */
export const CONTROLLER = {
  productName: "ΛογιστήριοPro",
  legalName: "«… επωνυμία εταιρείας …»",
  address: "«… έδρα …»",
  vatNumber: "«… Α.Φ.Μ. …»",
  email: "privacy@example.com",
  supportEmail: "support@example.com",
} as const;
