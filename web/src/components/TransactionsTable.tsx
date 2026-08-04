"use client";

import { useMemo, useState } from "react";
import { Receipt, Search, X } from "lucide-react";
import type { TransactionRow } from "@/lib/types";
import { money, moneyAbs } from "@/lib/format";
import { searchHaystack, searchKey } from "@/lib/text";
import { clsx } from "@/lib/clsx";
import { Badge } from "./ui/Badge";
import { useClientDrawer } from "./ClientDrawerProvider";

// The bottom row of the dashboard: every transaction in the period, searchable.
//
// Filtering is CLIENT-side and deliberately so. The rows are already in the
// dashboard payload, so searching them costs nothing and responds on the
// keystroke; a server round trip per character would be slower and would put
// load on a free-tier box for a list this size. If the book ever outgrows that,
// the fix is pagination on the endpoint, not a debounce here.
//
// Renders as a TABLE on desktop and as CARDS below `md`. A horizontally
// scrolling eight-column table on a phone is technically responsive and
// practically unusable.

type Filter = "all" | "revenue" | "expense" | "debt";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "Όλες" },
  { key: "revenue", label: "Έσοδα" },
  { key: "expense", label: "Έξοδα" },
  { key: "debt", label: "Χρεωστούμενα" },
];

/** The status badge for one row — same rule as the CSV and the statement. */
function StatusBadge({ t }: { t: TransactionRow }) {
  if (!t.is_debt) return <Badge tone="success">Εξοφλημένο</Badge>;
  if (t.status === "overdue") {
    return (
      <Badge tone="warning">
        Ληξιπρόθεσμο {t.days_overdue ? `· ${t.days_overdue} ημ.` : ""}
      </Badge>
    );
  }
  if ((t.paid ?? 0) > 0) return <Badge tone="debt">Μερική εξόφληση</Badge>;
  return <Badge tone="debt">Ανεξόφλητο</Badge>;
}

function amountClass(t: TransactionRow): string {
  return t.is_debt
    ? "text-amber-600 dark:text-amber-400"
    : t.is_revenue
      ? "text-emerald-600 dark:text-emerald-400"
      : "text-rose-600 dark:text-rose-400";
}

function matches(t: TransactionRow, needle: string): boolean {
  if (!needle) return true;
  // Accent- and case-insensitive — see lib/text.
  return searchHaystack([
    t.client,
    t.description,
    t.doc_number,
    t.doc_type,
    t.type,
    t.date,
  ]).includes(needle);
}

export function TransactionsTable({
  rows,
  clientIds,
}: {
  rows: TransactionRow[];
  /** name-key -> client id, so a row can open the drawer. Rows whose client
   *  has no row (a name-only legacy transaction) simply are not clickable. */
  clientIds: Record<string, number>;
}) {
  const { openClient } = useClientDrawer();

  const idFor = (name: string | null): number | undefined =>
    name ? clientIds[name.trim().toLowerCase()] : undefined;
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  const needle = useMemo(() => searchKey(query), [query]);

  const visible = useMemo(() => {
    return rows.filter((t) => {
      if (filter === "revenue" && !t.is_revenue) return false;
      if (filter === "expense" && (t.is_revenue || t.is_debt)) return false;
      if (filter === "debt" && !t.is_debt) return false;
      return matches(t, needle);
    });
  }, [rows, needle, filter]);

  return (
    <section className="rounded-2xl border border-slate-200 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark">
      {/* Toolbar */}
      <div className="flex flex-col gap-3 border-b border-slate-100 p-4 sm:flex-row sm:items-center sm:justify-between dark:border-slate-800">
        <div className="flex items-center gap-2">
          <Receipt className="h-4 w-4 text-slate-400" />
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Κινήσεις
          </h2>
          <span className="text-xs text-slate-400">
            {visible.length}
            {visible.length !== rows.length ? ` / ${rows.length}` : ""}
          </span>
        </div>

        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="flex rounded-lg border border-slate-200 p-0.5 dark:border-slate-700">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                onClick={() => setFilter(f.key)}
                className={clsx(
                  "rounded-md px-2.5 py-1 text-xs font-medium transition",
                  filter === f.key
                    ? "bg-indigo-600 text-white"
                    : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200",
                )}
              >
                {f.label}
              </button>
            ))}
          </div>

          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Αναζήτηση…"
              aria-label="Αναζήτηση κινήσεων"
              className="w-full rounded-lg border border-slate-200 bg-white py-1.5 pl-8 pr-7 text-xs text-slate-900 outline-none transition focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 sm:w-48 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
            />
            {query ? (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label="Καθαρισμός αναζήτησης"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </div>
        </div>
      </div>

      {visible.length === 0 ? (
        <p className="p-10 text-center text-sm text-slate-500 dark:text-slate-400">
          {rows.length === 0
            ? "Καμία κίνηση στην επιλεγμένη περίοδο."
            : "Καμία κίνηση δεν ταιριάζει με την αναζήτηση."}
        </p>
      ) : (
        <>
          {/* Desktop / tablet: a real table. */}
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-slate-100 text-[11px] uppercase tracking-wide text-slate-400 dark:border-slate-800">
                <tr>
                  <th className="px-4 py-2 font-medium">Ημ/νία</th>
                  <th className="px-4 py-2 font-medium">Πελάτης</th>
                  <th className="px-4 py-2 font-medium">Παραστατικό</th>
                  <th className="px-4 py-2 font-medium">Περιγραφή</th>
                  <th className="px-4 py-2 text-right font-medium">Φ.Π.Α.</th>
                  <th className="px-4 py-2 text-right font-medium">Ποσό</th>
                  <th className="px-4 py-2 font-medium">Κατάσταση</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {visible.map((t) => (
                  <tr
                    key={t.id ?? `${t.client}-${t.date}-${t.amount}`}
                    // A tinted hover rather than a grey one, plus a 3px inset
                    // rule down the left edge: on a dense table the tint alone
                    // is easy to lose track of when the eye is on the amount
                    // column, and the rule marks the row start.
                    className="transition-colors hover:bg-indigo-50/70 hover:[box-shadow:inset_3px_0_0_0_#6366f1] dark:hover:bg-indigo-500/[0.07]"
                  >
                    <td className="whitespace-nowrap px-4 py-2.5 tabular-nums text-slate-500 dark:text-slate-400">
                      {t.date ?? "—"}
                    </td>
                    <td className="px-4 py-2.5 font-medium text-slate-800 dark:text-slate-100">
                      {idFor(t.client) ? (
                        <button
                          type="button"
                          onClick={() => openClient(idFor(t.client)!)}
                          className="text-left hover:text-indigo-600 hover:underline dark:hover:text-indigo-400"
                        >
                          {t.client}
                        </button>
                      ) : (
                        (t.client ?? "—")
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-slate-500 dark:text-slate-400">
                      {t.doc_type ? <div>{t.doc_type}</div> : null}
                      {t.doc_number ? (
                        <div className="text-[11px]">{t.doc_number}</div>
                      ) : null}
                      {!t.doc_type && !t.doc_number ? "—" : null}
                    </td>
                    <td className="max-w-[16rem] truncate px-4 py-2.5 text-slate-600 dark:text-slate-300">
                      {t.description?.trim() || "—"}
                    </td>
                    <td className="whitespace-nowrap px-4 py-2.5 text-right tabular-nums text-slate-500 dark:text-slate-400">
                      {t.vat_amount != null ? moneyAbs(t.vat_amount) : "—"}
                    </td>
                    <td
                      className={clsx(
                        "whitespace-nowrap px-4 py-2.5 text-right font-semibold tabular-nums",
                        amountClass(t),
                      )}
                    >
                      {moneyAbs(t.amount)}
                    </td>
                    <td className="px-4 py-2.5">
                      <StatusBadge t={t} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile: cards. A seven-column table on a phone is unusable. */}
          <ul className="divide-y divide-slate-100 md:hidden dark:divide-slate-800">
            {visible.map((t) => (
              <li
                key={t.id ?? `${t.client}-${t.date}-${t.amount}`}
                className="p-4 transition-colors hover:bg-indigo-50/70 dark:hover:bg-indigo-500/[0.07]"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    {idFor(t.client) ? (
                      <button
                        type="button"
                        onClick={() => openClient(idFor(t.client)!)}
                        className="truncate text-sm font-semibold text-slate-900 dark:text-white"
                      >
                        {t.client}
                      </button>
                    ) : (
                      <div className="truncate text-sm font-semibold text-slate-900 dark:text-white">
                        {t.client ?? "—"}
                      </div>
                    )}
                    <div className="mt-0.5 truncate text-[11px] text-slate-500 dark:text-slate-400">
                      {t.date ?? "—"}
                      {t.doc_number ? ` · ${t.doc_number}` : ""}
                    </div>
                    {t.description?.trim() ? (
                      <div className="mt-0.5 truncate text-[11px] text-slate-600 dark:text-slate-300">
                        {t.description}
                      </div>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    <span
                      className={clsx(
                        "text-sm font-bold tabular-nums",
                        amountClass(t),
                      )}
                    >
                      {moneyAbs(t.amount)}
                    </span>
                    <StatusBadge t={t} />
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      {/* A running total of what is on screen, so filtering answers a question
          rather than just hiding rows. */}
      {visible.length > 0 ? (
        <div className="flex items-center justify-between border-t border-slate-100 px-4 py-2.5 text-xs dark:border-slate-800">
          <span className="text-slate-500 dark:text-slate-400">
            Σύνολο εμφανιζόμενων
          </span>
          <span className="font-semibold tabular-nums text-slate-900 dark:text-white">
            {money(visible.reduce((sum, t) => sum + Math.abs(t.amount), 0))}
          </span>
        </div>
      ) : null}
    </section>
  );
}
