import { BarChart3, LineChart, Landmark } from "lucide-react";
import type { Analytics } from "@/lib/types";
import { Card, CardHeader } from "./ui/Card";
import { RevenueExpenseChart } from "./charts/RevenueExpenseChart";
import { VatBreakdownChart } from "./charts/VatBreakdownChart";
import { MonthlyTrendChart } from "./charts/MonthlyTrendChart";

export function AnalyticsSection({ analytics }: { analytics: Analytics }) {
  return (
    <section className="space-y-4">
      {/* No export button here any more: the dashboard toolbar now carries CSV
          and PDF right next to the period filter, and in the new grid this
          section sits directly beneath it — two identical buttons 30px apart
          is clutter, not convenience. */}
      <h2 className="text-sm font-semibold text-slate-500 dark:text-slate-400">
        ΑΝΑΦΟΡΕΣ
      </h2>

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
