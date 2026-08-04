import { AlertTriangle, Building2 } from "lucide-react";
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

export function ClientCard({
  client,
  onOpen,
  daysOverdue,
  compact = false,
}: {
  client: ClientData;
  onOpen?: () => void;
  /** Set when this client has an overdue debt — drives the red marking. */
  daysOverdue?: number;
  /** Rendered in a narrow column — see the metrics grid below. */
  compact?: boolean;
}) {
  const m = client.metrics;
  const vatRefund = m.net_vat < 0;
  const profitPositive = m.net_profit >= 0;
  const overdue = daysOverdue !== undefined;

  return (
    <div
      className={clsx(
        "flex flex-col rounded-2xl border bg-white p-5 shadow-card transition dark:bg-slate-900 dark:shadow-card-dark",
        // Red border rather than a red card: the figures inside still have to
        // be readable, and a tinted card fights the metric colours.
        overdue
          ? "border-rose-300 dark:border-rose-500/40"
          : "border-slate-200 dark:border-slate-800",
        onOpen &&
          "cursor-pointer hover:border-indigo-300 hover:shadow-md dark:hover:border-indigo-500/40",
      )}
      // The whole card is the target, but the accessible control is the button
      // in the header — a div with a click handler is invisible to keyboards.
      onClick={onOpen}
    >
      {/* Header */}
      <div className="mb-4 flex items-center gap-2.5">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-400">
          <Building2 className="h-5 w-5" />
        </span>
        {onOpen ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onOpen();
            }}
            className="truncate text-left text-base font-semibold text-slate-900 outline-none hover:text-indigo-600 focus-visible:ring-2 focus-visible:ring-indigo-500 dark:text-white dark:hover:text-indigo-400"
          >
            {client.name.trim() || "—"}
          </button>
        ) : (
          <h3 className="truncate text-base font-semibold text-slate-900 dark:text-white">
            {client.name.trim() || "—"}
          </h3>
        )}
        {overdue ? (
          <span
            className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-full bg-rose-100 px-2 py-0.5 text-[10px] font-semibold text-rose-700 dark:bg-rose-500/15 dark:text-rose-300"
            title={`Ληξιπρόθεσμο εδώ και ${daysOverdue} ημέρες`}
          >
            <AlertTriangle className="h-3 w-3" />
            {daysOverdue} ημ.
          </span>
        ) : null}
      </div>

      {/* 5 core metrics.
          `compact` forces two-up regardless of viewport. The default classes
          are VIEWPORT breakpoints, and this card now also lives in a narrow
          side rail of the dashboard grid — on a wide screen `sm:grid-cols-3`
          fired inside a ~370px column and squeezed every tile to an unreadable
          sliver. The rail passes compact; the full-width layouts do not. */}
      <div
        className={clsx(
          "grid gap-2.5",
          compact ? "grid-cols-2" : "grid-cols-2 sm:grid-cols-3",
        )}
      >
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
          valueClass={
            overdue
              ? "text-rose-600 dark:text-rose-400"
              : "text-amber-600 dark:text-amber-400"
          }
          sub={overdue ? "Ληξιπρόθεσμο" : undefined}
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
