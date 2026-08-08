"use client";

import { useMemo } from "react";
import { AlertTriangle, ArrowRight, Check } from "lucide-react";
import type { ImportAnalysis, ImportField, ImportMapping } from "@/lib/types";
import { clsx } from "@/lib/clsx";

/**
 * Step two of an import: which column of the file is which field of the app.
 *
 * The screen exists because column names are the one thing about a user's
 * spreadsheet that cannot be guessed reliably. Every alias table has a limit,
 * and past it the old behaviour was to refuse the file — which left the user
 * editing their export to match our vocabulary. Here the guessing is a
 * STARTING POINT instead of a gate: the server's detection arrives
 * pre-selected, and every selection is a dropdown the user can correct.
 *
 * Each row shows the sample value the chosen column actually holds. That is
 * what makes the screen checkable rather than merely fillable — "Ημερομηνία →
 * Στήλη Γ" means nothing on its own, and "Ημερομηνία → Στήλη Γ → 15/01/2026"
 * is instantly either right or wrong.
 */

const UNMAPPED = "";

/** Whether a group's requirement is met — at least one of its fields mapped. */
function groupSatisfied(
  fields: ImportField[],
  mapping: ImportMapping,
  group: string,
): boolean {
  return fields.some((f) => f.group === group && mapping[f.key] !== undefined);
}

export function missingRequirements(
  analysis: ImportAnalysis,
  mapping: ImportMapping,
): string[] {
  const missing: string[] = [];
  for (const field of analysis.fields) {
    if (field.required && mapping[field.key] === undefined) {
      missing.push(field.label);
    }
  }
  // One message per GROUP, naming the alternatives — "Πελάτης ή Α.Φ.Μ." is
  // actionable where two separate "missing field" lines are just confusing.
  const groups = new Set(
    analysis.fields.map((f) => f.group).filter(Boolean) as string[],
  );
  for (const group of groups) {
    if (groupSatisfied(analysis.fields, mapping, group)) continue;
    missing.push(
      analysis.fields
        .filter((f) => f.group === group)
        .map((f) => f.label)
        .join(" ή "),
    );
  }
  return missing;
}

export function ColumnMapping({
  analysis,
  mapping,
  onChange,
}: {
  analysis: ImportAnalysis;
  mapping: ImportMapping;
  onChange: (next: ImportMapping) => void;
}) {
  const missing = useMemo(
    () => missingRequirements(analysis, mapping),
    [analysis, mapping],
  );

  // Which columns are already spoken for, so the same one being used twice is
  // visible. Not forbidden — a file can legitimately carry one figure that is
  // both the total and the net (a zero-rated invoice) — but worth marking.
  const used = useMemo(() => {
    const counts = new Map<number, number>();
    for (const index of Object.values(mapping)) {
      counts.set(index, (counts.get(index) ?? 0) + 1);
    }
    return counts;
  }, [mapping]);

  function set(key: string, raw: string) {
    const next = { ...mapping };
    if (raw === UNMAPPED) delete next[key];
    else next[key] = Number(raw);
    onChange(next);
  }

  /** The first sample value for a column — what makes a choice checkable. */
  function preview(index: number | undefined): string {
    if (index === undefined) return "";
    for (const row of analysis.sample) {
      const value = row[index];
      if (value) return value;
    }
    return "—";
  }

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-3 dark:border-slate-800 dark:bg-slate-800/40">
        <p className="text-xs text-slate-600 dark:text-slate-300">
          Βρέθηκαν <b>{analysis.headers.length}</b> στήλες και{" "}
          <b>{analysis.rows}</b> γραμμές. Ελέγξτε την αντιστοίχιση και
          διορθώστε ό,τι χρειάζεται.
        </p>
        <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
          Όσα πεδία αφήσετε κενά υπολογίζονται αυτόματα όπου είναι δυνατόν — το
          Φ.Π.Α. και η καθαρή αξία προκύπτουν από το συνολικό ποσό.
        </p>
      </div>

      {missing.length > 0 ? (
        <div
          role="alert"
          className="flex items-start gap-2.5 rounded-xl border border-amber-300 bg-amber-50 p-3 dark:border-amber-500/40 dark:bg-amber-500/10"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
          <p className="text-xs font-medium text-amber-800 dark:text-amber-200">
            Συμπληρώστε: {missing.join(" · ")}
          </p>
        </div>
      ) : null}

      <div className="overflow-hidden rounded-xl border border-slate-200 dark:border-slate-800">
        <table className="w-full text-left text-xs">
          <thead className="border-b border-slate-100 bg-slate-50/70 text-[11px] uppercase tracking-wide text-slate-400 dark:border-slate-800 dark:bg-slate-800/40">
            <tr>
              <th className="px-3 py-2 font-medium">Πεδίο εφαρμογής</th>
              <th className="px-3 py-2 font-medium">Στήλη αρχείου</th>
              <th className="px-3 py-2 font-medium">Δείγμα</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {analysis.fields.map((field) => {
              const index = mapping[field.key];
              const mapped = index !== undefined;
              const duplicated = mapped && (used.get(index) ?? 0) > 1;
              return (
                <tr key={field.key}>
                  <td className="px-3 py-2 align-top">
                    <div className="flex items-center gap-1.5 font-medium text-slate-800 dark:text-slate-100">
                      {field.label}
                      {field.required || field.group ? (
                        <span
                          className="text-rose-500"
                          title="Απαιτείται"
                          aria-label="Απαιτείται"
                        >
                          *
                        </span>
                      ) : null}
                      {mapped ? (
                        <Check className="h-3 w-3 text-emerald-500" />
                      ) : null}
                    </div>
                    {field.hint ? (
                      <div className="mt-0.5 text-[11px] text-slate-400">
                        {field.hint}
                      </div>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 align-top">
                    <select
                      value={mapped ? String(index) : UNMAPPED}
                      onChange={(e) => set(field.key, e.target.value)}
                      aria-label={`Στήλη για ${field.label}`}
                      className={clsx(
                        "w-full rounded-lg border bg-white px-2 py-1.5 text-xs text-slate-900 outline-none transition",
                        "focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500",
                        "dark:bg-slate-800 dark:text-white",
                        mapped
                          ? "border-slate-300 dark:border-slate-600"
                          : "border-dashed border-slate-300 text-slate-400 dark:border-slate-700",
                      )}
                    >
                      <option value={UNMAPPED}>— Καμία —</option>
                      {analysis.headers.map((heading, i) => (
                        <option key={i} value={String(i)}>
                          {heading}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-3 py-2 align-top">
                    <div className="max-w-[9rem] truncate text-slate-500 dark:text-slate-400">
                      {preview(index) || "—"}
                    </div>
                    {duplicated ? (
                      <div className="mt-0.5 text-[11px] text-amber-600 dark:text-amber-400">
                        Ίδια στήλη σε δύο πεδία
                      </div>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="flex items-center gap-1.5 text-[11px] text-slate-400">
        <ArrowRight className="h-3 w-3" />
        Τα πεδία με <span className="text-rose-500">*</span> είναι απαραίτητα.
      </p>
    </div>
  );
}
