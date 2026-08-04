import { Download } from "lucide-react";
import type { PeriodInfo } from "@/lib/types";
import { clsx } from "@/lib/clsx";

/**
 * "Export CSV" — a plain link, not a fetch.
 *
 * A server component on purpose: the download is a same-origin GET that the
 * browser handles natively, so there is nothing to hydrate. Letting the anchor
 * do the work also means the file streams straight to disk instead of being
 * buffered into a Blob in memory first.
 */
export function ExportButton({
  period,
  clientId,
  label = "Εξαγωγή CSV",
  title = "Λήψη των κινήσεων της επιλεγμένης περιόδου σε CSV (Excel)",
  className,
}: {
  /** The period on screen — the export honours it, so the file matches. */
  period?: Pick<PeriodInfo, "year" | "quarter" | "month"> | null;
  /** Set to export one client instead of the whole book. */
  clientId?: number;
  label?: string;
  title?: string;
  className?: string;
}) {
  const qs = new URLSearchParams();
  if (period?.year) qs.set("year", String(period.year));
  if (period?.quarter) qs.set("quarter", String(period.quarter));
  if (period?.month) qs.set("month", String(period.month));
  if (clientId != null) qs.set("client_id", String(clientId));

  return (
    <a
      href={`/api/exports/transactions${qs.toString() ? `?${qs}` : ""}`}
      // The server sends Content-Disposition with a period-stamped name; the
      // bare attribute opts into downloading without overriding that name.
      download
      title={title}
      className={clsx(
        "inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 transition hover:border-slate-300 hover:text-slate-900",
        "dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-slate-600 dark:hover:text-white",
        className,
      )}
    >
      <Download className="h-4 w-4" />
      {label}
    </a>
  );
}
