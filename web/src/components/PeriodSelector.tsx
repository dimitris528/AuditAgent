"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { CalendarRange, X } from "lucide-react";
import type { PeriodInfo } from "@/lib/types";
import { clsx } from "@/lib/clsx";

const MONTHS_EL = [
  "Ιανουάριος", "Φεβρουάριος", "Μάρτιος", "Απρίλιος", "Μάιος", "Ιούνιος",
  "Ιούλιος", "Αύγουστος", "Σεπτέμβριος", "Οκτώβριος", "Νοέμβριος", "Δεκέμβριος",
];

const QUARTERS = [
  { value: 1, label: "Α΄ τρίμηνο (Ιαν–Μαρ)" },
  { value: 2, label: "Β΄ τρίμηνο (Απρ–Ιουν)" },
  { value: 3, label: "Γ΄ τρίμηνο (Ιουλ–Σεπ)" },
  { value: 4, label: "Δ΄ τρίμηνο (Οκτ–Δεκ)" },
];

const select =
  "rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 outline-none transition focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200";

/**
 * Period filter for the dashboard.
 *
 * State lives in the URL rather than in React state: the page is a Server
 * Component that refetches per period, so the query string IS the source of
 * truth — which also makes a filtered view shareable and survivable across a
 * refresh.
 */
export function PeriodSelector({ period }: { period: PeriodInfo }) {
  const router = useRouter();
  const params = useSearchParams();

  const year = period.year;
  const quarter = period.quarter;
  const month = period.month;

  // Always offer the current year, even with no transactions in it yet.
  const years = Array.from(
    new Set([...(period.available_years ?? []), new Date().getFullYear()]),
  ).sort((a, b) => b - a);

  function apply(next: { year?: number | null; quarter?: number | null; month?: number | null }) {
    const qs = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === undefined || value === 0) qs.delete(key);
      else qs.set(key, String(value));
    }
    // Quarter and month are mutually exclusive; keeping both would be
    // ambiguous, and the server would silently prefer the month.
    if (next.month) qs.delete("quarter");
    if (next.quarter) qs.delete("month");
    // Neither means anything without a year.
    if (!qs.get("year")) {
      qs.delete("quarter");
      qs.delete("month");
    }
    router.push(qs.toString() ? `/?${qs}` : "/");
  }

  const filtered = Boolean(year);

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="flex items-center gap-1 text-xs font-medium text-slate-500 dark:text-slate-400">
        <CalendarRange className="h-3.5 w-3.5" />
        Περίοδος
      </span>

      <select
        className={select}
        value={year ?? ""}
        onChange={(e) =>
          apply({
            year: e.target.value ? Number(e.target.value) : null,
            quarter: null,
            month: null,
          })
        }
        aria-label="Έτος"
      >
        <option value="">Όλα τα έτη</option>
        {years.map((y) => (
          <option key={y} value={y}>
            {y}
          </option>
        ))}
      </select>

      <select
        className={select}
        value={quarter ?? ""}
        disabled={!filtered}
        onChange={(e) =>
          apply({ quarter: e.target.value ? Number(e.target.value) : null, month: null })
        }
        aria-label="Τρίμηνο"
      >
        <option value="">Όλο το έτος</option>
        {QUARTERS.map((q) => (
          <option key={q.value} value={q.value}>
            {q.label}
          </option>
        ))}
      </select>

      <select
        className={select}
        value={month ?? ""}
        disabled={!filtered}
        onChange={(e) =>
          apply({ month: e.target.value ? Number(e.target.value) : null, quarter: null })
        }
        aria-label="Μήνας"
      >
        <option value="">Όλοι οι μήνες</option>
        {MONTHS_EL.map((label, i) => (
          <option key={label} value={i + 1}>
            {label}
          </option>
        ))}
      </select>

      {filtered ? (
        <button
          type="button"
          onClick={() => apply({ year: null, quarter: null, month: null })}
          className={clsx(
            "inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2 py-1.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50",
            "dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800",
          )}
        >
          <X className="h-3 w-3" />
          Καθαρισμός
        </button>
      ) : null}

      <span className="text-[11px] text-slate-400 dark:text-slate-500">
        {filtered
          ? `${period.transactions_in_period} από ${period.transactions_total} κινήσεις`
          : `${period.transactions_total} κινήσεις συνολικά`}
      </span>
    </div>
  );
}
