"use client";

import { useEffect } from "react";
import { Printer } from "lucide-react";

/**
 * Opens the browser's print dialog, from which "Save as PDF" produces the
 * statement file.
 *
 * Why the browser rather than a server-side PDF: the entire document is Greek,
 * and reportlab ships no font with Greek glyphs (its bundled Vera.ttf has
 * none), so a server-rendered PDF would need a Unicode TTF vendored into the
 * repo and would render every character as an empty box the day that font went
 * missing. The browser already has Greek fonts and its print-to-PDF is
 * lossless, so the page IS the document.
 *
 * `auto` fires the dialog once on load, for the drawer's "open the statement
 * and print it" link. Guarded by a ref-free effect with an empty dependency
 * list so a re-render cannot reopen the dialog behind the user.
 */
export function PrintButton({
  auto = false,
  label = "Λήψη PDF",
}: {
  auto?: boolean;
  label?: string;
}) {
  useEffect(() => {
    if (!auto) return;
    // A frame's grace so fonts and layout settle — printing mid-layout is how
    // you get a statement with its table split across the wrong pages.
    const timer = window.setTimeout(() => window.print(), 400);
    return () => window.clearTimeout(timer);
  }, [auto]);

  return (
    <button
      type="button"
      onClick={() => window.print()}
      // Hidden on the printed page: a screenshot of a button on a statement
      // that was sent to a client looks like a mistake, because it is one.
      className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-indigo-600 px-3 text-xs font-semibold text-white transition hover:bg-indigo-500 print:hidden"
    >
      <Printer className="h-4 w-4" />
      {label}
    </button>
  );
}
