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

export function MonthlyTrendChart({ data }: { data: MonthlyTrendPoint[] }) {
  const c = useChartColors();
  const shaped = data.map((d) => ({ ...d, label: monthLabel(d.month) }));
  return (
    <ResponsiveContainer width="100%" height={280}>
      <AreaChart data={shaped} margin={{ top: 8, right: 8, left: 4, bottom: 0 }}>
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
          width={64}
        />
        <Tooltip content={<ChartTooltip />} />
        <Legend wrapperStyle={{ fontSize: 12, color: c.axis }} />
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
