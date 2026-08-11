"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { MonthlyTrendPoint } from "@/lib/types";
import { moneyCompact, monthLabel } from "@/lib/format";
import { useChartColors } from "./useChartColors";
import { ChartTooltip } from "./ChartTooltip";
import { ChartEmpty } from "./ChartEmpty";

/**
 * Revenue and expenses by month.
 *
 * The one chart on the dashboard that stays on its original axes: its category
 * is TIME, which reads left-to-right, and turning it on its side to match the
 * per-client charts would make a trend line something you tilt your head at.
 */
export function MonthlyTrendChart({ data }: { data: MonthlyTrendPoint[] }) {
  const c = useChartColors();
  const shaped = data.map((d) => ({ ...d, label: monthLabel(d.month) }));

  if (shaped.length === 0) return <ChartEmpty height={180} />;

  return (
    // 210 rather than the old 280: this sits beneath two panels whose height
    // now follows their row count, and the row it shares has to stay compact.
    <ResponsiveContainer width="100%" height={210}>
      <AreaChart data={shaped} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="rev" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={c.revenue} stopOpacity={0.35} />
            <stop offset="95%" stopColor={c.revenue} stopOpacity={0} />
          </linearGradient>
          <linearGradient id="exp" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={c.expense} stopOpacity={0.3} />
            <stop offset="95%" stopColor={c.expense} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke={c.grid} vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fill: c.axis, fontSize: 11 }}
          tickLine={false}
          axisLine={{ stroke: c.grid }}
          minTickGap={8}
        />
        <YAxis
          tick={{ fill: c.axis, fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => moneyCompact(v)}
          width={58}
        />
        <Tooltip content={<ChartTooltip />} />
        <Legend wrapperStyle={{ fontSize: 11, color: c.axis }} height={22} />
        <Area
          type="monotone"
          dataKey="revenue"
          name="Έσοδα"
          stroke={c.revenue}
          strokeWidth={2}
          fill="url(#rev)"
        />
        <Area
          type="monotone"
          dataKey="expense"
          name="Έξοδα"
          stroke={c.expense}
          strokeWidth={2}
          fill="url(#exp)"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
