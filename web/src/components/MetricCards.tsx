import {
  ArrowDownRight,
  ArrowUpRight,
  HandCoins,
  Landmark,
} from "lucide-react";
import type { DebtAlerts, HeaderTotals } from "@/lib/types";
import { money, moneyAbs } from "@/lib/format";
import { clsx } from "@/lib/clsx";

// The four figures the dashboard opens with. A dedicated row rather than part
// of the old stacked ExecutiveHeader, because at the top of a 12-column grid
// these are the only things that should compete for attention.
//
// 1 / 2 / 4 across: on a phone a two-up row of long euro amounts truncates, so
// it drops to one; tablets take two; a laptop takes all four.

type Tone = "revenue" | "expense" | "debt" | "vat";

const TONES: Record<Tone, { value: string; chip: string }> = {
  revenue: {
    value: "text-emerald-600 dark:text-emerald-400",
    chip: "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400",
  },
  expense: {
    value: "text-rose-600 dark:text-rose-400",
    chip: "bg-rose-50 text-rose-600 dark:bg-rose-500/10 dark:text-rose-400",
  },
  debt: {
    value: "text-amber-600 dark:text-amber-400",
    chip: "bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-400",
  },
  vat: {
    value: "text-violet-600 dark:text-violet-400",
    chip: "bg-violet-50 text-violet-600 dark:bg-violet-500/10 dark:text-violet-400",
  },
};

function MetricCard({
  label,
  value,
  hint,
  tone,
  icon,
}: {
  label: string;
  value: string;
  hint?: React.ReactNode;
  tone: Tone;
  icon: React.ReactNode;
}) {
  const t = TONES[tone];
  return (
    // The hover lift is 2px and 200ms on purpose: enough to confirm the card is
    // a live surface, not enough to make a row of four feel like it is
    // breathing. motion-reduce drops the transform and keeps the shadow, so the
    // feedback survives without the movement.
    <div className="group rounded-2xl border border-slate-200 bg-white p-4 shadow-card transition duration-200 hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-md motion-reduce:transform-none dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark dark:hover:border-slate-700">
      <div className="flex items-start justify-between gap-3">
        <span className="text-xs font-medium text-slate-500 dark:text-slate-400">
          {label}
        </span>
        <span
          className={clsx(
            "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg transition-transform duration-200 group-hover:scale-110 motion-reduce:transform-none",
            t.chip,
          )}
        >
          {icon}
        </span>
      </div>
      {/* Tabular figures so the four cards line up their decimal points, and
          break-words so a seven-figure amount wraps instead of overflowing. */}
      <div
        className={clsx(
          "mt-2 break-words text-2xl font-bold tabular-nums tracking-tight",
          t.value,
        )}
      >
        {value}
      </div>
      {hint ? (
        <div className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
          {hint}
        </div>
      ) : null}
    </div>
  );
}

export function MetricCards({
  header,
  alerts,
}: {
  header: HeaderTotals;
  alerts: DebtAlerts;
}) {
  const vatRefund = header.vat_status === "refund";
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard
        label="Συνολικά Έσοδα"
        value={money(header.total_gross_rev)}
        hint={`Καθαρά ${money(header.total_net_rev)}`}
        tone="revenue"
        icon={<ArrowUpRight className="h-4 w-4" />}
      />
      <MetricCard
        label="Συνολικά Έξοδα"
        value={moneyAbs(header.total_gross_exp)}
        hint={`Καθαρά ${moneyAbs(header.total_net_exp)}`}
        tone="expense"
        icon={<ArrowDownRight className="h-4 w-4" />}
      />
      <MetricCard
        label="Ανεξόφλητα"
        value={money(header.total_debt)}
        hint={
          alerts.overdue_count > 0 ? (
            <span className="font-semibold text-rose-600 dark:text-rose-400">
              {alerts.overdue_count} ληξιπρόθεσμα · {money(alerts.overdue_total)}
            </span>
          ) : (
            "Καμία καθυστέρηση"
          )
        }
        tone="debt"
        icon={<HandCoins className="h-4 w-4" />}
      />
      <MetricCard
        label="Καθαρό Φ.Π.Α."
        value={moneyAbs(header.total_vat)}
        hint={
          vatRefund
            ? "Προς επιστροφή"
            : header.vat_status === "payable"
              ? "Προς απόδοση"
              : "Μηδενικό"
        }
        tone="vat"
        icon={<Landmark className="h-4 w-4" />}
      />
    </div>
  );
}
