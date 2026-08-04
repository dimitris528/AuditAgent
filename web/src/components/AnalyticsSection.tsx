import { BarChart3, LineChart, Landmark } from "lucide-react";
import type { Analytics, PeriodInfo } from "@/lib/types";
import { Card, CardHeader } from "./ui/Card";
import { ExportButton } from "./ExportButton";
import { RevenueExpenseChart } from "./charts/RevenueExpenseChart";
import { VatBreakdownChart } from "./charts/VatBreakdownChart";
import { MonthlyTrendChart } from "./charts/MonthlyTrendChart";

export function AnalyticsSection({
  analytics,
  period,
}: {
  analytics: Analytics;
  /** Passed through to the export so the download matches the charts. */
  period?: Pick<PeriodInfo, "year" | "quarter" | "month"> | null;
}) {
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-slate-500 dark:text-slate-400">
          ΑΝΑΦΟΡΕΣ
        </h2>
        <ExportButton
          period={period}
          label="Εξαγωγή αναφοράς (CSV)"
          title="Λήψη των αναλυτικών κινήσεων της περιόδου σε CSV για Excel"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Έσοδα vs Έξοδα"
            subtitle="Μικτά ποσά ανά πελάτη"
            icon={<BarChart3 className="h-4 w-4" />}
          />
          <div className="px-3 py-4">
            <RevenueExpenseChart data={analytics.revenue_vs_expenses} />
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Ανάλυση Φ.Π.Α."
            subtitle="Καθαρό υπόλοιπο Φ.Π.Α. ανά πελάτη"
            icon={<Landmark className="h-4 w-4" />}
          />
          <div className="px-3 py-4">
            <VatBreakdownChart data={analytics.vat_breakdown} />
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Μηνιαία Τάση"
          subtitle="Έσοδα & έξοδα ανά μήνα"
          icon={<LineChart className="h-4 w-4" />}
        />
        <div className="px-3 py-4">
          <MonthlyTrendChart data={analytics.monthly_trend} />
        </div>
      </Card>
    </section>
  );
}
