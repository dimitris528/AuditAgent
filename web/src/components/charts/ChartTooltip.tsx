"use client";

import type { TooltipProps } from "recharts";
import { money } from "@/lib/format";
import { useChartColors } from "./useChartColors";

/** Shared themed tooltip that formats every numeric value as EUR. */
export function ChartTooltip({ active, payload, label }: TooltipProps<number, string>) {
  const c = useChartColors();
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div
      className="rounded-lg border px-3 py-2 text-xs shadow-lg"
      style={{ background: c.tooltipBg, borderColor: c.tooltipBorder, color: c.tooltipText }}
    >
      {label ? <div className="mb-1 font-semibold">{label}</div> : null}
      {payload.map((entry) => (
        <div key={String(entry.dataKey)} className="flex items-center gap-2 tabular-nums">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: entry.color }}
          />
          <span className="text-slate-500 dark:text-slate-400">{entry.name}:</span>
          <span className="font-medium">{money(Number(entry.value))}</span>
        </div>
      ))}
    </div>
  );
}
