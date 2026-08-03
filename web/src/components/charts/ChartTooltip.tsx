"use client";

import type { TooltipContentProps } from "recharts";
import { money } from "@/lib/format";
import { useChartColors } from "./useChartColors";

/**
 * Shared themed tooltip that formats every numeric value as EUR.
 *
 * recharts 3 split the tooltip types: TooltipProps is now what you pass to
 * <Tooltip>, with the context-supplied fields omitted, while custom content
 * receives TooltipContentProps (TooltipProps plus active/payload/label). Props
 * are optional here because recharts also renders content with nothing active.
 */
export function ChartTooltip({
  active,
  payload,
  label,
}: Partial<TooltipContentProps<number, string>>) {
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
