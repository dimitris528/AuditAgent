import { redirect } from "next/navigation";
import Link from "next/link";
import { AlertTriangle, ArrowLeft } from "lucide-react";
import { getClientStatement } from "@/lib/server-api";
import { ApiError } from "@/lib/errors";
import type { ClientDetailPayload, TransactionRow } from "@/lib/types";
import { money, moneyAbs } from "@/lib/format";
import { PrintButton } from "@/components/PrintButton";
import { ExportButton } from "@/components/ExportButton";

// Καρτέλα Πελάτη — the printable client statement.
//
// A full page rather than a modal because it is a DOCUMENT: it gets printed,
// saved as PDF and emailed to the client, so it needs its own URL, its own
// print stylesheet, and none of the app chrome around it. `print:` utilities
// below are what strip the rest away; the app header hides itself the same way
// (see app/layout.tsx).
export const dynamic = "force-dynamic";

function intParam(value: string | string[] | undefined): number | null {
  const raw = Array.isArray(value) ? value[0] : value;
  const n = Number(raw);
  return raw && Number.isFinite(n) && n > 0 ? Math.trunc(n) : null;
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("el-GR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(d);
}

/** The period, in words, for the statement header. */
function periodLabel(year: number | null, quarter: number | null,
                     month: number | null): string {
  if (!year && !quarter && !month) return "Όλες οι περίοδοι";
  const parts: string[] = [];
  if (month) parts.push(`Μήνας ${String(month).padStart(2, "0")}`);
  if (quarter) parts.push(`Τρίμηνο ${quarter}`);
  if (year) parts.push(String(year));
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
      <div className={`mt-0.5 text-base font-bold tabular-nums ${toneClass}`}>
        {value}
      </div>
      {hint ? <div className="text-[10px] text-slate-500">{hint}</div> : null}
    </div>
  );
}

/** Κατάσταση πληρωμής for one row — the same rule the CSV export applies. */
function paymentStatus(t: TransactionRow): string {
  if (!t.is_debt) return "Εξοφλημένο";
  if (t.status === "overdue") return `Ληξιπρόθεσμο ${t.days_overdue} ημ.`;
  return (t.paid ?? 0) > 0 ? "Μερικώς εξοφλημένο" : "Ανεξόφλητο";
}

export default async function StatementPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const clientId = Number(id);
  const period = {
    year: intParam(sp.year),
    quarter: intParam(sp.quarter),
    month: intParam(sp.month),
  };
  const autoPrint = (Array.isArray(sp.print) ? sp.print[0] : sp.print) === "1";

  if (!Number.isFinite(clientId) || clientId <= 0) redirect("/");

  let data: ClientDetailPayload;
  try {
    data = await getClientStatement(clientId, period);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) redirect("/login");
    return (
      <div className="mx-auto mt-10 max-w-xl rounded-2xl border border-rose-200 bg-rose-50 p-6 text-center">
        <AlertTriangle className="mx-auto h-8 w-8 text-rose-500" />
        <h2 className="mt-3 text-lg font-semibold text-rose-800">
          Δεν ήταν δυνατή η φόρτωση της καρτέλας
        </h2>
        <p className="mt-1 text-sm text-rose-700">
          {err instanceof Error ? err.message : "Άγνωστο σφάλμα."}
        </p>
      </div>
    );
  }

  const { client, summary, payments, issuer } = data;
  const vatRefund = summary.net_vat < 0;
  // OLDEST first. The API sorts newest-first for the dashboard's activity
  // list, but a statement is a ledger: it is read forwards, and it has to
  // agree with the CSV export, which is also chronological.
  const transactions = [...data.transactions].sort((a, b) =>
    (a.date ?? "").localeCompare(b.date ?? ""),
  );

  return (
    // Forced to a light palette even in dark mode: this is printed, and a dark
    // statement either burns a cartridge or comes out unreadable.
    <div className="mx-auto max-w-4xl bg-white text-slate-900 print:max-w-none">
      {/* Toolbar — screen only */}
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3 print:hidden">
        <Link
          href="/"
          className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 transition hover:border-slate-300 hover:text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
        >
          <ArrowLeft className="h-4 w-4" />
          Πίνακας ελέγχου
        </Link>
        <div className="flex items-center gap-2">
          <ExportButton
            period={period}
            clientId={client.id}
            label="Εξαγωγή CSV"
            title="Λήψη των κινήσεων αυτού του πελάτη σε CSV"
          />
          <PrintButton auto={autoPrint} />
        </div>
      </div>

      {/* The document itself */}
      <article className="rounded-2xl border border-slate-200 p-8 print:rounded-none print:border-0 print:p-0">
        <header className="flex items-start justify-between gap-6 border-b-2 border-slate-900 pb-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Καρτέλα Πελάτη</h1>
            <p className="mt-0.5 text-sm text-slate-600">
              {periodLabel(period.year, period.quarter, period.month)}
            </p>
          </div>
          <div className="text-right text-xs text-slate-600">
            <div className="text-sm font-bold text-slate-900">
              {issuer?.name || "ΛογιστήριοPro"}
            </div>
            {issuer?.email ? <div>{issuer.email}</div> : null}
            <div>Ημερομηνία έκδοσης: {formatDate(new Date().toISOString())}</div>
          </div>
        </header>

        {/* Client identity */}
        <section className="mt-5 grid grid-cols-2 gap-x-8 gap-y-1.5 text-sm">
          <div className="col-span-2 text-lg font-semibold">
            {client.name}
            {client.archived ? (
              <span className="ml-2 rounded border border-amber-300 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-700">
                Αρχειοθετημένος
              </span>
            ) : null}
          </div>
          <div>
            <span className="text-slate-500">Α.Φ.Μ.: </span>
            {client.afm || "—"}
          </div>
          <div>
            <span className="text-slate-500">Επικοινωνία: </span>
            {client.contact || "—"}
          </div>
          <div>
            <span className="text-slate-500">Κατάσταση: </span>
            {client.archived ? "Αρχειοθετημένος" : "Ενεργός"}
          </div>
          <div>
            <span className="text-slate-500">Κινήσεις περιόδου: </span>
            {summary.count}
          </div>
          {client.notes ? (
            <div className="col-span-2 mt-1">
              <span className="text-slate-500">Σημειώσεις: </span>
              {client.notes}
            </div>
          ) : null}
        </section>

        {/* Financial summary */}
        <section className="mt-5 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          <Figure
            label="Σύνολο Εσόδων"
            value={money(summary.gross_rev)}
            tone="revenue"
            hint={`Καθαρά ${money(summary.net_rev)}`}
          />
          <Figure
            label="Σύνολο Εξόδων"
            value={money(summary.gross_exp)}
            tone="expense"
            hint={`Καθαρά ${money(summary.net_exp)}`}
          />
          <Figure
            label="Ανεξόφλητο Υπόλοιπο"
            value={money(summary.debt)}
            tone="debt"
            hint={
              summary.open_debts > 0
                ? `${summary.open_debts} ανοιχτά χρεωστούμενα`
                : "Καμία οφειλή"
            }
          />
          <Figure
            label="Καθαρό Φ.Π.Α."
            value={moneyAbs(summary.net_vat)}
            tone="vat"
            hint={
              vatRefund
                ? "Προς επιστροφή"
                : summary.net_vat > 0
                  ? "Προς απόδοση"
                  : "Μηδενικό"
            }
          />
        </section>

        <div className="mt-3 rounded-lg bg-slate-100 px-4 py-2.5 text-sm">
          <span className="text-slate-600">Καθαρό αποτέλεσμα περιόδου: </span>
          <span
            className={`font-bold tabular-nums ${
              summary.net_profit >= 0 ? "text-emerald-700" : "text-rose-700"
            }`}
          >
            {money(summary.net_profit)}
          </span>
        </div>

        {/* Itemised transactions */}
        <section className="mt-6">
          <h2 className="mb-2 text-sm font-bold uppercase tracking-wide">
            Αναλυτικές Κινήσεις
          </h2>
          {transactions.length === 0 ? (
            <p className="rounded border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
              Καμία κίνηση στην επιλεγμένη περίοδο.
            </p>
          ) : (
            <div className="overflow-x-auto">
              {/* table-fixed + an explicit colgroup. Without them the browser
                  auto-sizes from content, and on a narrow A4 print the last two
                  columns collided — "Κατάσταση" ran straight into "Σύνολο".
                  Fixed widths also stop a long description starving the
                  document-number and amount columns. */}
              <table className="w-full table-fixed border-collapse text-[11px]">
                <colgroup>
                  <col className="w-[9%]" />
                  <col className="w-[16%]" />
                  <col className="w-[17%]" />
                  {/* Wide enough for "Χρεωστούμενο" on one line — at 10% it
                      broke mid-word, which reads as a rendering fault. */}
                  <col className="w-[13%]" />
                  <col className="w-[10%]" />
                  <col className="w-[9%]" />
                  <col className="w-[11%]" />
                  <col className="w-[15%]" />
                </colgroup>
                <thead>
                  {/* Repeated on every printed page — a three-page statement
                      whose columns are only labelled on page one is unreadable. */}
                  <tr className="border-y border-slate-300 text-left align-bottom">
                    <th className="py-1.5 pr-2 font-semibold">Ημ/νία</th>
                    <th className="py-1.5 pr-2 font-semibold">Παραστατικό</th>
                    <th className="py-1.5 pr-2 font-semibold">Περιγραφή</th>
                    <th className="py-1.5 pr-2 font-semibold">Είδος</th>
                    <th className="py-1.5 pr-2 text-right font-semibold">Καθαρή</th>
                    <th className="py-1.5 pr-2 text-right font-semibold">Φ.Π.Α.</th>
                    <th className="py-1.5 pr-3 text-right font-semibold">Σύνολο</th>
                    <th className="py-1.5 pl-1 font-semibold">Κατάσταση</th>
                  </tr>
                </thead>
                <tbody>
                  {transactions.map((t) => (
                    // Kept whole across a page break.
                    <tr
                      key={t.id}
                      className="border-b border-slate-200 break-inside-avoid"
                    >
                      <td className="py-1.5 pr-2 align-top tabular-nums">
                        {formatDate(t.date)}
                      </td>
                      {/* Type above number: joined onto one line in a
                          fixed-width column, the document number was what got
                          truncated — the field an audit needs most. */}
                      <td className="py-1.5 pr-2 align-top break-words">
                        {t.doc_type ? <div>{t.doc_type}</div> : null}
                        {t.doc_number ? (
                          <div className="text-slate-500">{t.doc_number}</div>
                        ) : null}
                        {!t.doc_type && !t.doc_number ? "—" : null}
                      </td>
                      <td className="py-1.5 pr-2 align-top break-words">
                        {t.description?.trim() || "—"}
                      </td>
                      <td className="py-1.5 pr-2 align-top break-words">
                        {t.type || "—"}
                      </td>
                      <td className="py-1.5 pr-2 text-right align-top tabular-nums">
                        {t.net_amount != null ? moneyAbs(t.net_amount) : "—"}
                      </td>
                      <td className="py-1.5 pr-2 text-right align-top tabular-nums">
                        {t.vat_amount != null ? moneyAbs(t.vat_amount) : "—"}
                      </td>
                      <td
                        className={`py-1.5 pr-3 text-right align-top font-semibold tabular-nums ${
                          t.is_debt
                            ? "text-amber-700"
                            : t.is_revenue
                              ? "text-emerald-700"
                              : "text-rose-700"
                        }`}
                      >
                        {moneyAbs(t.amount)}
                      </td>
                      <td className="py-1.5 pl-1 align-top break-words">
                        {paymentStatus(t)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Settlement history — NOT period-scoped, matching the drawer: a debt
            raised last quarter and paid this one still has to show what was
            paid against it. */}
        {payments.length > 0 ? (
          <section className="mt-6 break-inside-avoid">
            <h2 className="mb-2 text-sm font-bold uppercase tracking-wide">
              Ιστορικό Εξοφλήσεων
            </h2>
            <p className="mb-1.5 text-[10px] text-slate-500">
              Πλήρες ιστορικό, ανεξάρτητα από την επιλεγμένη περίοδο.
            </p>
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="border-y border-slate-300 text-left">
                  <th className="py-1.5 pr-2 font-semibold">Ημ/νία</th>
                  <th className="py-1.5 pr-2 font-semibold">Είδος</th>
                  <th className="py-1.5 pr-2 font-semibold">Σημείωση</th>
                  <th className="py-1.5 pr-2 text-right font-semibold">Ποσό</th>
                  <th className="py-1.5 text-right font-semibold">Υπόλοιπο</th>
                </tr>
              </thead>
              <tbody>
                {payments.map((p) => (
                  <tr key={p.id} className="border-b border-slate-200">
                    <td className="py-1.5 pr-2 whitespace-nowrap tabular-nums">
                      {formatDate(p.paid_date)}
                    </td>
                    <td className="py-1.5 pr-2">
                      {p.kind === "full" ? "Πλήρης" : "Μερική"}
                    </td>
                    <td className="py-1.5 pr-2">{p.note?.trim() || "—"}</td>
                    <td className="py-1.5 pr-2 text-right font-semibold tabular-nums text-emerald-700">
                      {money(p.amount)}
                    </td>
                    <td className="py-1.5 text-right tabular-nums">
                      {money(p.remaining)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        ) : null}

        <footer className="mt-8 border-t border-slate-300 pt-3 text-[10px] text-slate-500">
          Τα ποσά εμφανίζονται ως απόλυτες τιμές· η στήλη «Είδος» δηλώνει την
          κατεύθυνση (Έσοδο / Έξοδο / Χρεωστούμενο). Η καρτέλα αφορά αποκλειστικά
          τον παραπάνω πελάτη.
        </footer>
      </article>
    </div>
  );
}
