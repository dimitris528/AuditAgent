"use client";

import {
  Bar,
  BarChart,
  Cell,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { VatBreakdownPoint } from "@/lib/types";
import { moneyCompact } from "@/lib/format";
import { useChartColors } from "./useChartColors";
import { ChartTooltip } from "./ChartTooltip";

/** Net ΦΠΑ per client — violet when payable, emerald when a credit (negative). */
export function VatBreakdownChart({ data }: { data: VatBreakdownPoint[] }) {
  const c = useChartColors();
  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 8, right: 16, left: 8, bottom: 0 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke={c.grid} horizontal={false} />
        <XAxis
          type="number"
          tick={{ fill: c.axis, fontSize: 11 }}
          tickLine={false}
          axisLine={{ stroke: c.grid }}
          tickFormatter={(v) => moneyCompact(v)}
        />
        <YAxis
          type="category"
          dataKey="client"
          tick={{ fill: c.axis, fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          width={110}
        />
        <Tooltip content={<ChartTooltip />} cursor={{ fill: c.grid, opacity: 0.3 }} />
        <Bar dataKey="vat" name="Καθαρό Φ.Π.Α." radius={[0, 4, 4, 0]} maxBarSize={26}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.vat < 0 ? c.vatNeg : c.vat} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
