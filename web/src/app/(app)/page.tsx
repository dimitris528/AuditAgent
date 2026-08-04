import { redirect } from "next/navigation";
import { AlertTriangle } from "lucide-react";
import { getDashboard } from "@/lib/server-api";
import { apiBase } from "@/lib/backend";
import { ApiError } from "@/lib/errors";
import type { DashboardData } from "@/lib/types";
import { ClientGrid } from "@/components/ClientGrid";
import { ClientDrawerProvider } from "@/components/ClientDrawerProvider";
import { DebtAlerts } from "@/components/DebtAlerts";
import { AnalyticsSection } from "@/components/AnalyticsSection";
import { MetricCards } from "@/components/MetricCards";
import { TransactionsTable } from "@/components/TransactionsTable";
import { QuickAddTransaction } from "@/components/QuickAddTransaction";
import { PeriodSelector } from "@/components/PeriodSelector";
import { TrialBanner } from "@/components/TrialBanner";
import { ExportButton } from "@/components/ExportButton";
import { PdfExportButton } from "@/components/PdfExportButton";

// Always render fresh — figures reflect the latest database state.
export const dynamic = "force-dynamic";

/** Read one positive integer from a search param, or null. */
function intParam(value: string | string[] | undefined): number | null {
  const raw = Array.isArray(value) ? value[0] : value;
  const n = Number(raw);
  return raw && Number.isFinite(n) && n > 0 ? Math.trunc(n) : null;
}

function BackendDown({ message }: { message: string }) {
  // The URL actually used is the single most useful fact here and used to be
  // invisible: this page once said "backend unavailable" while the backend was
  // healthy, because the deployed bundle had http://localhost:8000 baked into
  // it. Showing the resolved base makes that class of failure self-evident.
  const base = apiBase();
  const local = base.includes("localhost") || base.includes("127.0.0.1");
  return (
    <div className="mx-auto mt-10 max-w-xl rounded-2xl border border-rose-200 bg-rose-50 p-6 text-center dark:border-rose-500/30 dark:bg-rose-500/10">
      <AlertTriangle className="mx-auto h-8 w-8 text-rose-500" />
      <h2 className="mt-3 text-lg font-semibold text-rose-800 dark:text-rose-300">
        Δεν είναι διαθέσιμο το backend
      </h2>
      <p className="mt-1 text-sm text-rose-700 dark:text-rose-300/80">{message}</p>
      <p className="mt-4 rounded-lg bg-white/70 px-3 py-2 text-left text-xs text-slate-700 dark:bg-slate-900/60 dark:text-slate-300">
        <span className="text-slate-500 dark:text-slate-400">API_BASE_URL: </span>
        <span className="font-mono">{base}</span>
      </p>
      {local && process.env.NODE_ENV === "production" ? (
        <p className="mt-2 rounded-lg bg-amber-100 px-3 py-2 text-left text-xs text-amber-900 dark:bg-amber-500/15 dark:text-amber-200">
          Ο διακομιστής δείχνει σε localhost ενώ τρέχει σε production — ορίστε
          το <span className="font-mono">API_BASE_URL</span> στην υπηρεσία web.
        </p>
      ) : null}
    </div>
  );
}

export default async function Page({
  searchParams,
}: {
  // searchParams is a Promise in Next 15.
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const period = {
    year: intParam(sp.year),
    quarter: intParam(sp.quarter),
    month: intParam(sp.month),
  };

  let data: DashboardData;
  try {
    data = await getDashboard(period);
  } catch (err) {
    // Unauthenticated / expired session → back to login.
    if (err instanceof ApiError && err.status === 401) {
      redirect("/login");
    }
    return (
      <BackendDown
        message={
          err instanceof Error
            ? `${err.message}. Ξεκινήστε τον FastAPI server στο :8000.`
            : "Άγνωστο σφάλμα."
        }
      />
    );
  }

  // The paywall, at the page level. The backend already refuses an expired
  // tenant's writes with 402, but letting them sit on a dashboard whose every
  // control fails is a worse experience than sending them somewhere that
  // explains why. Outside the try block on purpose: redirect() works by
  // throwing, and a catch would swallow it.
  if (!data.subscription.allows_writes) {
    redirect("/billing");
  }

  // name-key -> id, so a transaction row can open its client's drawer. Built
  // here because the dashboard payload is the only place both are present.
  // Archived clients included: their transactions are still in the table, and
  // a row that silently refuses to open reads as a bug rather than a policy.
  const clientIds: Record<string, number> = {};
  for (const c of [...data.clients, ...data.archived_clients]) {
    if (c.id) clientIds[c.key] = Number(c.id);
  }

  return (
    // One drawer for the whole page, so an alert row and a client card cannot
    // each open their own on top of the other. It wraps the grid rather than
    // sitting inside it: the drawer is position:fixed, and a stray margin would
    // push its full-screen backdrop off the top.
    <ClientDrawerProvider period={period} vatRates={data.vat_rates}>
      {/* The 12-column grid. Everything below places itself on it, so the
          breakpoints are declared once here rather than per section:
            mobile  — one column, everything stacked in reading order
            lg      — 12 columns, charts 7 / alerts 5
          Sections that are always full width just span 12. */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        {/* --- Toolbar ------------------------------------------------- */}
        <div className="lg:col-span-12">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h1 className="text-xl font-bold tracking-tight text-slate-900 dark:text-white">
                Πίνακας Ελέγχου
              </h1>
              <p className="text-sm text-slate-500 dark:text-slate-400">
                {data.counts.active_clients} ενεργοί πελάτες ·{" "}
                {data.counts.transactions} κινήσεις
                {data.counts.open_debts > 0
                  ? ` · ${data.counts.open_debts} χρεωστούμενα`
                  : ""}
              </p>
            </div>
            <QuickAddTransaction
              vatRates={data.vat_rates}
              docTypes={data.doc_types}
              defaultVatRate={0.24}
              scanEnabled={data.scan_enabled}
            />
          </div>
        </div>

        <div className="lg:col-span-12">
          <TrialBanner subscription={data.subscription} />
        </div>

        {/* --- Period filter + exports ---------------------------------- */}
        {/* The exports sit WITH the filter, not in the page header, because
            what they export is whatever the filter is showing — putting them
            side by side is what makes that obvious without a tooltip. */}
        <div className="lg:col-span-12">
          <div className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between dark:border-slate-800 dark:bg-slate-900">
            <PeriodSelector period={data.period} />
            <div className="flex shrink-0 items-center gap-2">
              <ExportButton period={period} />
              <PdfExportButton period={period} />
            </div>
          </div>
        </div>

        {/* --- Top row: the four metrics -------------------------------- */}
        <div className="lg:col-span-12">
          <MetricCards header={data.header} alerts={data.debt_alerts} />
        </div>

        {/* --- Middle: charts left (7), alerts + clients right (5) ------- */}
        <div className="lg:col-span-7">
          <AnalyticsSection analytics={data.analytics} />
        </div>

        {/* The right rail is a BOUNDED, self-scrolling panel.

            It used to be an ordinary grid item, so its height was the sum of
            every alert and every client card — which meant the row grew with
            the client list and left a widening blank column under the charts.
            One tenant with forty clients turned the dashboard into a metre of
            whitespace.

            The fix, in two halves:
              lg:absolute lg:inset-0  takes the rail's content OUT of flow, so
                                      it can no longer drive the row height. The
                                      row is now exactly as tall as the charts,
                                      and the rail fills it precisely — no gap
                                      on either side, whatever the client count.
              lg:min-h-[500px]        a floor for the opposite case: a book with
                                      almost no analytics would otherwise
                                      squeeze the rail to nothing.

            Everything below `lg` is untouched — on a phone the rail is a normal
            stacked section, and a nested scroll area inside a scrolling page is
            the last thing a small screen needs. */}
        <div className="lg:relative lg:col-span-5 lg:min-h-[500px]">
          <div className="rail-scroll flex flex-col gap-4 lg:absolute lg:inset-0 lg:overflow-y-auto lg:pr-1">
            <DebtAlerts alerts={data.debt_alerts} />
            <ClientGrid
              clients={data.clients}
              archivedClients={data.archived_clients}
              alerts={data.debt_alerts}
              compact
            />
          </div>
        </div>

        {/* --- Bottom: the full-width transactions table ----------------- */}
        <div className="lg:col-span-12">
          <TransactionsTable rows={data.transactions} clientIds={clientIds} />
        </div>
      </div>
    </ClientDrawerProvider>
  );
}
