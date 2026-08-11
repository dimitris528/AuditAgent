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
import { ChartEmpty } from "./ChartEmpty";
import { CategoryTick } from "./CategoryTick";
import { MAX_ROWS, barChartHeight, labelWidth, topRows } from "./horizontal";

/**
 * Έσοδα vs Έξοδα per client, as HORIZONTAL grouped bars.
 *
 * This was a vertical chart with its client names rotated -18° along the X
 * axis, which truncated the long ones and overlapped the rest as soon as a book
 * had more than a handful of clients. On its side each name gets a full line
 * (see charts/horizontal.ts), and the chart shows the biggest clients first
 * rather than whatever order the API happened to return.
 */
export function RevenueExpenseChart({
  data,
  limit = MAX_ROWS,
}: {
  data: RevenueExpensePoint[];
  limit?: number;
}) {
  const c = useChartColors();
  // Ranked by the LARGER of the two bars: a client with heavy expenses and no
  // revenue is exactly as interesting as one with heavy revenue.
  const rows = topRows(data, (d) => Math.max(Math.abs(d.revenue), Math.abs(d.expense)), limit);
  const hidden = data.length - rows.length;

  if (rows.length === 0) return <ChartEmpty />;

  return (
    <>
      <ResponsiveContainer width="100%" height={barChartHeight(rows.length, { perRow: 34, chrome: 52 })}>
        <BarChart
          data={rows}
          layout="vertical"
          margin={{ top: 4, right: 12, left: 0, bottom: 0 }}
          barGap={2}
          barCategoryGap="22%"
        >
          {/* Vertical rules only: on a horizontal chart the gridlines that help
              are the ones the bars are measured AGAINST. */}
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
            tick={<CategoryTick fill={c.axis} />}
            tickLine={false}
            axisLine={false}
            width={labelWidth(rows.map((r) => r.client))}
          />
          {/* The tooltip carries the FULL name, so a truncated tick is never the
              only place a client is identified. */}
          <Tooltip content={<ChartTooltip />} cursor={{ fill: c.grid, opacity: 0.3 }} />
          <Legend wrapperStyle={{ fontSize: 11, color: c.axis }} height={24} />
          <Bar dataKey="revenue" name="Έσοδα" fill={c.revenue} radius={[0, 3, 3, 0]} maxBarSize={13} />
          <Bar dataKey="expense" name="Έξοδα" fill={c.expense} radius={[0, 3, 3, 0]} maxBarSize={13} />
        </BarChart>
      </ResponsiveContainer>
      {hidden > 0 ? (
        <p className="mt-1 text-center text-[10px] text-slate-400 dark:text-slate-500">
          Κορυφαίοι {rows.length} από {data.length} πελάτες
        </p>
      ) : null}
    </>
  );
}
