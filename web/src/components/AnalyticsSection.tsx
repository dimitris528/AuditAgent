import { BarChart3, LineChart, Landmark } from "lucide-react";
import type { Analytics } from "@/lib/types";
import { Card, CardHeader } from "./ui/Card";
import { RevenueExpenseChart } from "./charts/RevenueExpenseChart";
import { VatBreakdownChart } from "./charts/VatBreakdownChart";
import { MonthlyTrendChart } from "./charts/MonthlyTrendChart";

export function AnalyticsSection({ analytics }: { analytics: Analytics }) {
  return (
    // gap-3 and a 2-up chart row at `md`: the section is a column of the
    // dashboard grid, not a page of its own, so the panels inside it pack the
    // same way the cards above them do.
    <section className="space-y-3">
      {/* No export button here any more: the dashboard toolbar now carries CSV
          and PDF right next to the period filter, and in the new grid this
          section sits directly beneath it — two identical buttons 30px apart
          is clutter, not convenience. */}
      <h2 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
        Αναφορές
      </h2>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Card>
          <CardHeader
            title="Έσοδα vs Έξοδα"
            subtitle="Μικτά ποσά ανά πελάτη"
            icon={<BarChart3 className="h-4 w-4" />}
          />
          {/* px-2: the chart brings its own axis margins, and stacking card
              padding on top of them only narrows the plot. */}
          <div className="px-2 py-3">
            <RevenueExpenseChart data={analytics.revenue_vs_expenses} />
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Ανάλυση Φ.Π.Α."
            subtitle="Καθαρό υπόλοιπο Φ.Π.Α. ανά πελάτη"
            icon={<Landmark className="h-4 w-4" />}
          />
          <div className="px-2 py-3">
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
        <div className="px-2 py-3">
          <MonthlyTrendChart data={analytics.monthly_trend} />
        </div>
      </Card>
    </section>
  );
}
