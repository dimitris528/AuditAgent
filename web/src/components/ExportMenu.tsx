"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, Download, FileSpreadsheet } from "lucide-react";
import {
  EXPORT_HINTS,
  EXPORT_KINDS,
  EXPORT_LABELS,
  exportUrl,
  type ExportDialect,
  type ExportPeriod,
} from "@/lib/exports";
import { clsx } from "@/lib/clsx";

/**
 * "Εξαγωγή" — one control for all three downloadable tables.
 *
 * A menu rather than three buttons in the toolbar: the tables are alternatives,
 * not a set of actions to perform in turn, and three near-identical download
 * buttons side by side make the user read all three labels every time to find
 * the one they wanted.
 *
 * The items are plain <a download> links, so each click is a native browser
 * download — no fetch, no Blob, no spinner that can get stuck. The dialect
 * toggle at the foot rewrites their hrefs, which is the whole of its
 * implementation.
 */
export function ExportMenu({
  period,
  className,
}: {
  period?: ExportPeriod | null;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  // Excel by default — see lib/exports. The plain option exists for anyone
  // feeding the file to pandas or another system rather than to Excel.
  const [dialect, setDialect] = useState<ExportDialect>("excel");
  const root = useRef<HTMLDivElement>(null);

  // Close on an outside click or Escape. Both listeners are only attached while
  // the menu is open, so a closed menu costs nothing.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: MouseEvent) {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={root} className={clsx("relative", className)}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Λήψη πινάκων σε CSV / Excel"
        className={clsx(
          "inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-600 transition hover:border-slate-300 hover:text-slate-900",
          "dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-slate-600 dark:hover:text-white",
          open && "border-slate-300 text-slate-900 dark:border-slate-600 dark:text-white",
        )}
      >
        <Download className="h-3.5 w-3.5" />
        Εξαγωγή CSV
        <ChevronDown
          className={clsx("h-3 w-3 transition-transform", open && "rotate-180")}
        />
      </button>

      {open ? (
        // right-0: the menu hangs off the RIGHT edge of its trigger, which sits
        // at the right end of the toolbar — anchored left it would run off the
        // viewport on a laptop.
        <div
          role="menu"
          className="absolute right-0 z-40 mt-1 w-72 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-lg dark:border-slate-700 dark:bg-slate-900"
        >
          <ul className="py-1">
            {EXPORT_KINDS.map((kind) => (
              <li key={kind}>
                <a
                  href={exportUrl(kind, { period, dialect })}
                  download
                  role="menuitem"
                  onClick={() => setOpen(false)}
                  className="flex items-start gap-2.5 px-3 py-2 transition hover:bg-slate-50 dark:hover:bg-slate-800"
                >
                  <FileSpreadsheet className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
                  <span className="min-w-0">
                    <span className="block text-xs font-semibold text-slate-800 dark:text-slate-100">
                      {EXPORT_LABELS[kind]}
                    </span>
                    <span className="block text-[11px] leading-snug text-slate-500 dark:text-slate-400">
                      {EXPORT_HINTS[kind]}
                    </span>
                  </span>
                </a>
              </li>
            ))}
          </ul>

          <div className="flex items-center justify-between gap-2 border-t border-slate-100 px-3 py-2 dark:border-slate-800">
            <span className="text-[11px] text-slate-500 dark:text-slate-400">
              Μορφή
            </span>
            <div className="flex rounded-lg border border-slate-200 p-0.5 dark:border-slate-700">
              {(
                [
                  ["excel", "Excel (EL)"],
                  ["iso", "Απλό CSV"],
                ] as [ExportDialect, string][]
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setDialect(value)}
                  aria-pressed={dialect === value}
                  className={clsx(
                    "rounded-md px-2 py-0.5 text-[11px] font-medium transition",
                    dialect === value
                      ? "bg-indigo-600 text-white"
                      : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
