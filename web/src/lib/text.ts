// Text normalisation for the client-side search boxes.
//
// The mirror of server/text.py's key helpers, for the searches that never
// reach the backend: the dashboard payload already holds the rows, so filtering
// happens on the keystroke rather than over the wire.

/**
 * Fold a string to a comparable search key: accents stripped, lower-cased.
 *
 * The app's data is Greek, and someone typing "νησιδα" expects to find
 * "Νησίδα" — matching the raw strings would not find it. Both the needle and
 * the haystack go through here so the two always agree.
 */
export function searchKey(value: string | null | undefined): string {
  return (value ?? "")
    .trim()
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase();
}

/** The same fold over several fields at once, joined into one haystack. */
export function searchHaystack(
  parts: (string | null | undefined)[],
): string {
  return searchKey(parts.filter(Boolean).join(" "));
}
