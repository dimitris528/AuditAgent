import Link from "next/link";
import { FileText } from "lucide-react";
import type { PeriodInfo } from "@/lib/types";
import { clsx } from "@/lib/clsx";

/**
 * "Export PDF" — opens the printable period summary with its print dialog
 * already up, so one click produces the file.
 *
 * A Link, not a fetch: the summary is a normal server-rendered page at
 * /summary, and ?print=1 makes it call window.print() on load. Opens in a new
 * tab so printing does not throw away the dashboard the user was looking at.
 *
 * Sibling of ExportButton by design — the two sit together next to the period
 * filter, and both carry the same period, so CSV and PDF always describe the
 * same window.
 */
export function PdfExportButton({
  period,
  label = "Εξαγωγή PDF",
  title = "Εκτυπώσιμη σύνοψη της επιλεγμένης περιόδου (αποθήκευση ως PDF)",
  className,
}: {
  period?: Pick<PeriodInfo, "year" | "quarter" | "month"> | null;
  label?: string;
  title?: string;
  className?: string;
}) {
  const qs = new URLSearchParams();
  if (period?.year) qs.set("year", String(period.year));
  if (period?.quarter) qs.set("quarter", String(period.quarter));
  if (period?.month) qs.set("month", String(period.month));
  qs.set("print", "1");

  return (
    <Link
      href={`/summary?${qs}`}
      target="_blank"
      rel="noopener noreferrer"
      title={title}
      className={clsx(
        "inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-600 transition hover:border-slate-300 hover:text-slate-900",
        "dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-slate-600 dark:hover:text-white",
        className,
      )}
    >
      <FileText className="h-3.5 w-3.5" />
      {label}
    </Link>
  );
}
