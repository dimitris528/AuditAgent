import { redirect } from "next/navigation";
import Link from "next/link";
import { AlertTriangle, ArrowLeft } from "lucide-react";
import { getDashboard } from "@/lib/server-api";
import { ApiError } from "@/lib/errors";
import type { DashboardData } from "@/lib/types";
import { money, moneyAbs } from "@/lib/format";
import { PrintButton } from "@/components/PrintButton";
import { ExportButton } from "@/components/ExportButton";

// The period summary, as a printable document — the "Export PDF" the dashboard
// toolbar offers. Same approach as the client statement: the page IS the PDF,
// because the whole thing is Greek and no server-side PDF library available
// here ships a font with Greek glyphs (see components/PrintButton.tsx).
//
// Reads the SAME /api/dashboard payload the dashboard does, so the printed
// figures cannot drift from the ones on screen — there is no second
// calculation to disagree.
export const dynamic = "force-dynamic";

function intParam(value: string | string[] | undefined): number | null {
  const raw = Array.isArray(value) ? value[0] : value;
  const n = Number(raw);
  return raw && Number.isFinite(n) && n > 0 ? Math.trunc(n) : null;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("el-GR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(d);
}

const MONTHS = [
  "Ιανουάριος", "Φεβρουάριος", "Μάρτιος", "Απρίλιος", "Μάιος", "Ιούνιος",
  "Ιούλιος", "Αύγουστος", "Σεπτέμβριος", "Οκτώβριος", "Νοέμβριος", "Δεκέμβριος",
];

function periodLabel(y: number | null, q: number | null, m: number | null): string {
  if (!y && !q && !m) return "Όλες οι περίοδοι";
  const parts: string[] = [];
  if (m) parts.push(MONTHS[m - 1] ?? `Μήνας ${m}`);
  if (q) parts.push(`${q}ο Τρίμηνο`);
  if (y) parts.push(String(y));
  return parts.join(" · ");
}

function Figure({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: string;
  tone?: "revenue" | "expense" | "debt" | "vat";
  hint?: string;
}) {
  const toneClass =
    tone === "revenue"
      ? "text-emerald-700"
      : tone === "expense"
        ? "text-rose-700"
        : tone === "debt"
          ? "text-amber-700"
          : tone === "vat"
            ? "text-violet-700"
            : "text-slate-900";
  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className={`mt-0.5 text-lg font-bold tabular-nums ${toneClass}`}>
        {value}
      </div>
      {hint ? <div className="text-[10px] text-slate-500">{hint}</div> : null}
    </div>
  );
}

export default async function SummaryPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const period = {
    year: intParam(sp.year),
    quarter: intParam(sp.quarter),
    month: intParam(sp.month),
  };
  const autoPrint = (Array.isArray(sp.print) ? sp.print[0] : sp.print) === "1";

  let data: DashboardData;
  try {
    data = await getDashboard(period);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) redirect("/login");
    return (
      <div className="mx-auto mt-10 max-w-xl rounded-2xl border border-rose-200 bg-rose-50 p-6 text-center">
        <AlertTriangle className="mx-auto h-8 w-8 text-rose-500" />
        <h2 className="mt-3 text-lg font-semibold text-rose-800">
          Δεν ήταν δυνατή η φόρτωση της σύνοψης
        </h2>
        <p className="mt-1 text-sm text-rose-700">
          {err instanceof Error ? err.message : "Άγνωστο σφάλμα."}
        </p>
      </div>
    );
  }

  const h = data.header;
  const alerts = data.debt_alerts;
  // Biggest contributors first — a summary is read top-down and the first rows
  // are the ones that get looked at.
  const clients = [...data.clients].sort(
    (a, b) => b.metrics.gross_rev - a.metrics.gross_rev,
  );

  return (
    <div className="mx-auto max-w-4xl bg-white text-slate-900 print:max-w-none">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3 print:hidden">
        <Link
          href="/"
          className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 transition hover:border-slate-300 hover:text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
        >
          <ArrowLeft className="h-4 w-4" />
          Πίνακας ελέγχου
        </Link>
        <div className="flex items-center gap-2">
          <ExportButton period={period} />
          <PrintButton auto={autoPrint} />
        </div>
      </div>

      <article className="rounded-2xl border border-slate-200 p-8 print:rounded-none print:border-0 print:p-0">
        <header className="flex items-start justify-between gap-6 border-b-2 border-slate-900 pb-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">
              Οικονομική Σύνοψη
            </h1>
            <p className="mt-0.5 text-sm text-slate-600">
              {periodLabel(period.year, period.quarter, period.month)}
            </p>
          </div>
          <div className="text-right text-xs text-slate-600">
            <div className="text-sm font-bold text-slate-900">{data.username}</div>
            <div>Ημερομηνία έκδοσης: {formatDate(new Date().toISOString())}</div>
            <div>
              {data.counts.active_clients} ενεργοί πελάτες ·{" "}
              {data.period.transactions_in_period} κινήσεις
            </div>
          </div>
        </header>

        {/* Totals */}
        <section className="mt-5 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          <Figure
            label="Συνολικά Έσοδα"
            value={money(h.total_gross_rev)}
            tone="revenue"
            hint={`Καθαρά ${money(h.total_net_rev)}`}
          />
          <Figure
            label="Συνολικά Έξοδα"
            value={moneyAbs(h.total_gross_exp)}
            tone="expense"
            hint={`Καθαρά ${moneyAbs(h.total_net_exp)}`}
          />
          <Figure
            label="Ανεξόφλητα"
            value={money(h.total_debt)}
            tone="debt"
            hint={
              alerts.overdue_count > 0
                ? `${alerts.overdue_count} ληξιπρόθεσμα`
                : "Καμία καθυστέρηση"
            }
          />
          <Figure
            label="Καθαρό Φ.Π.Α."
            value={moneyAbs(h.total_vat)}
            tone="vat"
            hint={
              h.vat_status === "refund"
                ? "Προς επιστροφή"
                : h.vat_status === "payable"
                  ? "Προς απόδοση"
                  : "Μηδενικό"
            }
          />
        </section>

        <div className="mt-3 rounded-lg bg-slate-100 px-4 py-2.5 text-sm">
          <span className="text-slate-600">Καθαρό αποτέλεσμα περιόδου: </span>
          <span
            className={`font-bold tabular-nums ${
              h.total_net_profit >= 0 ? "text-emerald-700" : "text-rose-700"
            }`}
          >
            {money(h.total_net_profit)}
          </span>
        </div>

        {/* Per client */}
        <section className="mt-6">
          <h2 className="mb-2 text-sm font-bold uppercase tracking-wide">
            Ανάλυση ανά Πελάτη
          </h2>
          {clients.length === 0 ? (
            <p className="rounded border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
              Καμία κίνηση στην επιλεγμένη περίοδο.
            </p>
          ) : (
            <table className="w-full table-fixed border-collapse text-[11px]">
              <colgroup>
                <col className="w-[28%]" />
                <col className="w-[15%]" />
                <col className="w-[15%]" />
                <col className="w-[14%]" />
                <col className="w-[14%]" />
                <col className="w-[14%]" />
              </colgroup>
              <thead>
                <tr className="border-y border-slate-300 text-left align-bottom">
                  <th className="py-1.5 pr-2 font-semibold">Πελάτης</th>
                  <th className="py-1.5 pr-2 text-right font-semibold">Έσοδα</th>
                  <th className="py-1.5 pr-2 text-right font-semibold">Έξοδα</th>
                  <th className="py-1.5 pr-2 text-right font-semibold">Φ.Π.Α.</th>
                  <th className="py-1.5 pr-2 text-right font-semibold">
                    Ανεξόφλητα
                  </th>
                  <th className="py-1.5 text-right font-semibold">Αποτέλεσμα</th>
                </tr>
              </thead>
              <tbody>
                {clients.map((c) => (
                  <tr key={c.key} className="border-b border-slate-200">
                    <td className="py-1.5 pr-2 align-top break-words font-medium">
                      {c.name}
                    </td>
                    <td className="py-1.5 pr-2 text-right align-top tabular-nums text-emerald-700">
                      {money(c.metrics.gross_rev)}
                    </td>
                    <td className="py-1.5 pr-2 text-right align-top tabular-nums text-rose-700">
                      {moneyAbs(c.metrics.gross_exp)}
                    </td>
                    <td className="py-1.5 pr-2 text-right align-top tabular-nums text-violet-700">
                      {moneyAbs(c.metrics.net_vat)}
                    </td>
                    <td className="py-1.5 pr-2 text-right align-top tabular-nums text-amber-700">
                      {c.metrics.debt > 0 ? money(c.metrics.debt) : "—"}
                    </td>
                    <td
                      className={`py-1.5 text-right align-top font-semibold tabular-nums ${
                        c.metrics.net_profit >= 0
                          ? "text-emerald-700"
                          : "text-rose-700"
                      }`}
                    >
                      {money(c.metrics.net_profit)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t-2 border-slate-900 font-bold">
                  <td className="py-2 pr-2">Σύνολο</td>
                  <td className="py-2 pr-2 text-right tabular-nums">
                    {money(h.total_gross_rev)}
                  </td>
                  <td className="py-2 pr-2 text-right tabular-nums">
                    {moneyAbs(h.total_gross_exp)}
                  </td>
                  <td className="py-2 pr-2 text-right tabular-nums">
                    {moneyAbs(h.total_vat)}
                  </td>
                  <td className="py-2 pr-2 text-right tabular-nums">
                    {money(h.total_debt)}
                  </td>
                  <td className="py-2 text-right tabular-nums">
                    {money(h.total_net_profit)}
                  </td>
                </tr>
              </tfoot>
            </table>
          )}
        </section>

        {/* Overdue — all-time, matching the dashboard's own caveat. */}
        {alerts.clients.length > 0 ? (
          <section className="mt-6 break-inside-avoid">
            <h2 className="mb-1 text-sm font-bold uppercase tracking-wide">
              Ανεξόφλητα Υπόλοιπα
            </h2>
            <p className="mb-1.5 text-[10px] text-slate-500">
              Υπολογίζονται σε ΟΛΟ το βιβλίο, ανεξάρτητα από την επιλεγμένη
              περίοδο — μια ληξιπρόθεσμη οφειλή δεν παύει να υπάρχει επειδή
              κοιτάτε άλλο τρίμηνο.
            </p>
            <table className="w-full table-fixed border-collapse text-[11px]">
              <colgroup>
                <col className="w-[40%]" />
                <col className="w-[15%]" />
                <col className="w-[20%]" />
                <col className="w-[25%]" />
              </colgroup>
              <thead>
                <tr className="border-y border-slate-300 text-left align-bottom">
                  <th className="py-1.5 pr-2 font-semibold">Πελάτης</th>
                  <th className="py-1.5 pr-2 text-right font-semibold">Πλήθος</th>
                  <th className="py-1.5 pr-2 text-right font-semibold">Σύνολο</th>
                  <th className="py-1.5 text-right font-semibold">Ληξιπρόθεσμα</th>
                </tr>
              </thead>
              <tbody>
                {alerts.clients.map((c) => (
                  <tr key={c.key} className="border-b border-slate-200">
                    <td className="py-1.5 pr-2 align-top break-words">{c.name}</td>
                    <td className="py-1.5 pr-2 text-right align-top tabular-nums">
                      {c.count}
                    </td>
                    <td className="py-1.5 pr-2 text-right align-top tabular-nums">
                      {money(c.total)}
                    </td>
                    <td className="py-1.5 text-right align-top font-semibold tabular-nums text-rose-700">
                      {c.overdue > 0 ? money(c.overdue) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        ) : null}

        <footer className="mt-8 border-t border-slate-300 pt-3 text-[10px] text-slate-500">
          Τα ποσά εμφανίζονται ως απόλυτες τιμές. Το καθαρό αποτέλεσμα είναι
          έσοδα μείον έξοδα, μετά την αφαίρεση του Φ.Π.Α.
        </footer>
      </article>
    </div>
  );
}
