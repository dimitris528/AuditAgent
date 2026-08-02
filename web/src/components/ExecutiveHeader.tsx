import {
  ArrowDownRight,
  ArrowUpRight,
  Landmark,
  Wallet,
} from "lucide-react";
import type { HeaderTotals } from "@/lib/types";
import { money, moneyAbs } from "@/lib/format";
import { clsx } from "@/lib/clsx";
import { Badge } from "./ui/Badge";

function Kpi({
  label,
  value,
  sub,
  icon,
  accent,
}: {
  label: string;
  value: string;
  sub?: React.ReactNode;
  icon: React.ReactNode;
  accent: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card transition-colors dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark">
      <div className="flex items-center justify-between">
        <span className="text-[13px] font-medium text-slate-500 dark:text-slate-400">
          {label}
        </span>
        <span className={clsx("rounded-lg p-1.5", accent)}>{icon}</span>
      </div>
      <div className="mt-3 text-2xl font-bold tracking-tight text-slate-900 tabular-nums dark:text-white">
        {value}
      </div>
      {sub ? <div className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">{sub}</div> : null}
    </div>
  );
}

export function ExecutiveHeader({ header }: { header: HeaderTotals }) {
  const profitPositive = header.total_net_profit >= 0;
  const vatLabel =
    header.vat_status === "refund"
      ? "Προς επιστροφή"
      : header.vat_status === "payable"
        ? "Προς απόδοση"
        : "Μηδενικό";

  return (
    <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Kpi
        label="Συνολικά Έσοδα"
        value={money(header.total_gross_rev)}
        sub={<>Καθαρά: {money(header.total_net_rev)}</>}
        icon={<ArrowUpRight className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />}
        accent="bg-emerald-50 dark:bg-emerald-500/10"
      />
      <Kpi
        label="Συνολικά Έξοδα"
        value={money(header.total_gross_exp)}
        sub={<>Καθαρά: {money(header.total_net_exp)}</>}
        icon={<ArrowDownRight className="h-4 w-4 text-rose-600 dark:text-rose-400" />}
        accent="bg-rose-50 dark:bg-rose-500/10"
      />
      <Kpi
        label="Καθαρό Αποτέλεσμα"
        value={money(header.total_net_profit)}
        sub={
          <span className={profitPositive ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}>
            Καθαρό κέρδος (μετά Φ.Π.Α.)
          </span>
        }
        icon={<Wallet className="h-4 w-4 text-indigo-600 dark:text-indigo-400" />}
        accent="bg-indigo-50 dark:bg-indigo-500/10"
      />
      <Kpi
        label="Καθαρό Φ.Π.Α."
        value={moneyAbs(header.total_vat)}
        sub={
          <Badge tone={header.vat_status === "refund" ? "success" : "vat"}>
            {vatLabel}
          </Badge>
        }
        icon={<Landmark className="h-4 w-4 text-violet-600 dark:text-violet-400" />}
        accent="bg-violet-50 dark:bg-violet-500/10"
      />
    </section>
  );
}
