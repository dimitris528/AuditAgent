"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { RevenueExpensePoint } from "@/lib/types";
import { moneyCompact } from "@/lib/format";
import { useChartColors } from "./useChartColors";
import { ChartTooltip } from "./ChartTooltip";

export function RevenueExpenseChart({ data }: { data: RevenueExpensePoint[] }) {
  const c = useChartColors();
  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 4, bottom: 0 }} barGap={4}>
        <CartesianGrid strokeDasharray="3 3" stroke={c.grid} vertical={false} />
        <XAxis
          dataKey="client"
          tick={{ fill: c.axis, fontSize: 11 }}
          tickLine={false}
          axisLine={{ stroke: c.grid }}
          interval={0}
          height={48}
          angle={-18}
          textAnchor="end"
        />
        <YAxis
          tick={{ fill: c.axis, fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => moneyCompact(v)}
          width={64}
        />
        <Tooltip content={<ChartTooltip />} cursor={{ fill: c.grid, opacity: 0.3 }} />
        <Legend wrapperStyle={{ fontSize: 12, color: c.axis }} />
        <Bar dataKey="revenue" name="Έσοδα" fill={c.revenue} radius={[4, 4, 0, 0]} maxBarSize={38} />
        <Bar dataKey="expense" name="Έξοδα" fill={c.expense} radius={[4, 4, 0, 0]} maxBarSize={38} />
      </BarChart>
    </ResponsiveContainer>
  );
}
