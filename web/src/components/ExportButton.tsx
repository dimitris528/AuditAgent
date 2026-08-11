import { Download } from "lucide-react";
import {
  EXPORT_LABELS,
  exportUrl,
  type ExportKind,
  type ExportPeriod,
} from "@/lib/exports";
import { clsx } from "@/lib/clsx";

/**
 * "Export CSV" for ONE table — a plain link, not a fetch.
 *
 * A server component on purpose: the download is a same-origin GET that the
 * browser handles natively, so there is nothing to hydrate. Letting the anchor
 * do the work also means the file streams straight to disk instead of being
 * buffered into a Blob in memory first.
 *
 * Use this where the export is unambiguous (a client's own ledger, the table
 * you are standing in). Where the user is choosing BETWEEN tables, use
 * ExportMenu, which is built on the same URL builder.
 */
export function ExportButton({
  kind = "transactions",
  period,
  clientId,
  label = "Εξαγωγή CSV",
  title,
  compact = false,
  className,
}: {
  /** Which table to download. Defaults to the transactions ledger. */
  kind?: ExportKind;
  /** The period on screen — the export honours it, so the file matches. */
  period?: ExportPeriod | null;
  /** Set to export one client instead of the whole book. */
  clientId?: number;
  /** Empty renders the icon alone — the accessible name then comes from
   *  `title`, which is always set. */
  label?: string;
  title?: string;
  /** Shorter and borderless-tight, for a panel toolbar rather than a page one. */
  compact?: boolean;
  className?: string;
}) {
  const hint =
    title ??
    `Λήψη σε CSV (Excel): ${EXPORT_LABELS[kind].toLowerCase()} της επιλεγμένης περιόδου`;
  return (
    <a
      href={exportUrl(kind, { period, clientId })}
      // The server sends Content-Disposition with a period-stamped name; the
      // bare attribute opts into downloading without overriding that name.
      download
      title={hint}
      aria-label={hint}
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white font-medium text-slate-600 transition hover:border-slate-300 hover:text-slate-900",
        "dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-slate-600 dark:hover:text-white",
        compact ? "h-7 px-2 text-[11px]" : "h-8 px-2.5 text-xs",
        className,
      )}
    >
      <Download className={compact ? "h-3.5 w-3.5" : "h-4 w-4"} />
      {label || null}
    </a>
  );
}
