import { Building2 } from "lucide-react";
import type { ClientData } from "@/lib/types";
import { money, moneyAbs } from "@/lib/format";
import { clsx } from "@/lib/clsx";
import { Badge } from "./ui/Badge";
import { TaxProgressBar } from "./TaxProgressBar";

function Metric({
  label,
  value,
  valueClass,
  sub,
}: {
  label: string;
  value: string;
  valueClass?: string;
  sub?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50/60 p-3 dark:border-slate-800 dark:bg-slate-800/40">
      <div className="truncate text-[11px] font-medium text-slate-500 dark:text-slate-400">
        {label}
      </div>
      <div
        className={clsx(
          "mt-0.5 truncate text-[15px] font-bold tabular-nums text-slate-900 dark:text-white",
          valueClass,
        )}
      >
        {value}
      </div>
      {sub ? <div className="mt-0.5 truncate text-[11px] text-slate-500 dark:text-slate-400">{sub}</div> : null}
    </div>
  );
}

export function ClientCard({ client }: { client: ClientData }) {
  const m = client.metrics;
  const vatRefund = m.net_vat < 0;
  const profitPositive = m.net_profit >= 0;

  return (
    <div className="flex flex-col rounded-2xl border border-slate-200 bg-white p-5 shadow-card transition hover:border-slate-300 hover:shadow-md dark:border-slate-800 dark:bg-slate-900 dark:shadow-card-dark dark:hover:border-slate-700">
      {/* Header */}
      <div className="mb-4 flex items-center gap-2.5">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-400">
          <Building2 className="h-5 w-5" />
        </span>
        <h3 className="truncate text-base font-semibold text-slate-900 dark:text-white">
          {client.name.trim() || "—"}
        </h3>
      </div>

      {/* 5 core metrics */}
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
        <Metric
          label="Έσοδα"
          value={money(m.gross_rev)}
          valueClass="text-emerald-600 dark:text-emerald-400"
          sub={`Καθαρά ${money(m.net_rev)}`}
        />
        <Metric
          label="Έξοδα"
          value={money(m.gross_exp)}
          valueClass="text-rose-600 dark:text-rose-400"
          sub={`Καθαρά ${money(m.net_exp)}`}
        />
        <Metric
          label="Φ.Π.Α."
          value={moneyAbs(m.net_vat)}
          valueClass="text-violet-600 dark:text-violet-400"
          sub={
            <Badge tone={vatRefund ? "success" : "vat"} className="mt-0.5">
              {vatRefund ? "Προς επιστροφή" : m.net_vat > 0 ? "Προς απόδοση" : "Μηδενικό"}
            </Badge>
          }
        />
        <Metric
          label="Χρεωστούμενα"
          value={money(m.debt)}
          valueClass="text-amber-600 dark:text-amber-400"
        />
        <Metric
          label="Καθαρό Αποτέλεσμα"
          value={money(m.net_profit)}
          valueClass={
            profitPositive
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-rose-600 dark:text-rose-400"
          }
        />
        <div className="hidden sm:block" aria-hidden />
      </div>

      {/* Income-tax progress bar */}
      <div className="mt-4 border-t border-slate-100 pt-4 dark:border-slate-800">
        <TaxProgressBar tax={client.tax} />
      </div>
    </div>
  );
}
