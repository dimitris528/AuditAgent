import { redirect } from "next/navigation";
import { AlertTriangle, FlaskConical } from "lucide-react";
import { getDashboard } from "@/lib/server-api";
import { ApiError } from "@/lib/errors";
import type { DashboardData } from "@/lib/types";
import { ExecutiveHeader } from "@/components/ExecutiveHeader";
import { ClientGrid } from "@/components/ClientGrid";
import { AnalyticsSection } from "@/components/AnalyticsSection";
import { QuickAddTransaction } from "@/components/QuickAddTransaction";
import { Badge } from "@/components/ui/Badge";

// Always render fresh — figures reflect the latest Airtable state.
export const dynamic = "force-dynamic";

function BackendDown({ message }: { message: string }) {
  return (
    <div className="mx-auto mt-10 max-w-xl rounded-2xl border border-rose-200 bg-rose-50 p-6 text-center dark:border-rose-500/30 dark:bg-rose-500/10">
      <AlertTriangle className="mx-auto h-8 w-8 text-rose-500" />
      <h2 className="mt-3 text-lg font-semibold text-rose-800 dark:text-rose-300">
        Δεν είναι διαθέσιμο το backend
      </h2>
      <p className="mt-1 text-sm text-rose-700 dark:text-rose-300/80">{message}</p>
      <p className="mt-4 rounded-lg bg-white/70 px-3 py-2 text-left font-mono text-xs text-slate-700 dark:bg-slate-900/60 dark:text-slate-300">
        uvicorn server.main:app --reload --port 8000
      </p>
    </div>
  );
}

export default async function Page() {
  let data: DashboardData;
  try {
    data = await getDashboard();
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

  return (
    <div className="space-y-6">
      {/* Page toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-900 dark:text-white">
            Πίνακας Ελέγχου
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {data.counts.active_clients} ενεργοί πελάτες · {data.counts.transactions} κινήσεις
            {data.counts.open_debts > 0 ? ` · ${data.counts.open_debts} χρεωστούμενα` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {data.demo ? (
            <Badge tone="warning" icon={<FlaskConical className="h-3 w-3" />}>
              DEMO — χωρίς Airtable
            </Badge>
          ) : null}
          <QuickAddTransaction vatRates={data.vat_rates} defaultVatRate={0.24} />
        </div>
      </div>

      <ExecutiveHeader header={data.header} />
      <ClientGrid clients={data.clients} />
      <AnalyticsSection analytics={data.analytics} />
    </div>
  );
}
