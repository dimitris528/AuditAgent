import { AlertTriangle } from "lucide-react";
import type { TaxInfo } from "@/lib/types";
import { money, percent } from "@/lib/format";
import { clsx } from "@/lib/clsx";
import { Badge } from "./ui/Badge";

/**
 * Μπάρα Φόρου Εισοδήματος — net taxable income filling toward the scale limit.
 * Turns warning-red with an alert badge once income crosses into the high
 * (29 %) bracket.
 */
export function TaxProgressBar({ tax }: { tax: TaxInfo }) {
  const isOver = tax.status === "over";
  const isNone = tax.status === "none";
  const ratePct = Math.round(tax.rate * 100);

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-xs font-medium text-slate-500 dark:text-slate-400">
          Μπάρα Φόρου Εισοδήματος
        </span>
        {isOver ? (
          <Badge tone="warning" icon={<AlertTriangle className="h-3 w-3" />}>
            Κλίμακα {ratePct}%
          </Badge>
        ) : (
          <Badge tone={isNone ? "neutral" : "success"}>Κλίμακα {ratePct}%</Badge>
        )}
      </div>

      <div className="relative h-2.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div
          className={clsx(
            "h-full rounded-full transition-all duration-500",
            isOver
              ? "bg-gradient-to-r from-amber-500 to-rose-600"
              : "bg-gradient-to-r from-emerald-500 to-emerald-400",
          )}
          style={{ width: `${Math.max(tax.pct, isNone ? 0 : 2)}%` }}
        />
      </div>

      <div className="mt-1.5 flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400">
        <span className="tabular-nums">
          {money(tax.taxable)} / {money(tax.limit)}
        </span>
        <span className="tabular-nums">{percent(tax.pct)}</span>
      </div>

      {isOver ? (
        <p className="mt-1 text-[11px] font-medium text-rose-600 dark:text-rose-400">
          Υπέρβαση ορίου — συντελεστής {ratePct}%
        </p>
      ) : null}
    </div>
  );
}
