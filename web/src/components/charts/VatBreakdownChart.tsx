"use client";

import {
  Bar,
  BarChart,
  Cell,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { VatBreakdownPoint } from "@/lib/types";
import { moneyCompact } from "@/lib/format";
import { useChartColors } from "./useChartColors";
import { ChartTooltip } from "./ChartTooltip";
import { ChartEmpty } from "./ChartEmpty";
import { CategoryTick } from "./CategoryTick";
import { MAX_ROWS, barChartHeight, labelWidth, topRows } from "./horizontal";

/**
 * Net ΦΠΑ per client — violet when payable, emerald when a credit (negative).
 *
 * Horizontal, and sized by row count rather than pinned to a fixed box, so it
 * matches its neighbour and long client names stay readable. Ranked by
 * MAGNITUDE: a large credit is as worth seeing as a large bill.
 */
export function VatBreakdownChart({
  data,
  limit = MAX_ROWS,
}: {
  data: VatBreakdownPoint[];
  limit?: number;
}) {
  const c = useChartColors();
  const rows = topRows(data, (d) => d.vat, limit);
  const hidden = data.length - rows.length;
  // Only meaningful once the axis actually spans zero — otherwise the reference
  // line lands on the axis itself and just thickens it.
  const crossesZero =
    rows.some((r) => r.vat > 0) && rows.some((r) => r.vat < 0);

  if (rows.length === 0) return <ChartEmpty />;

  return (
    <>
      <ResponsiveContainer width="100%" height={barChartHeight(rows.length, { perRow: 30, chrome: 40 })}>
        <BarChart
          data={rows}
          layout="vertical"
          margin={{ top: 4, right: 14, left: 0, bottom: 0 }}
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
            tick={<CategoryTick fill={c.axis} />}
            tickLine={false}
            axisLine={false}
            width={labelWidth(rows.map((r) => r.client))}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: c.grid, opacity: 0.3 }} />
          {/* Where "owed" turns into "owed back" — the one value on this chart
              that is not just a length. */}
          {crossesZero ? <ReferenceLine x={0} stroke={c.axis} strokeWidth={1} /> : null}
          <Bar dataKey="vat" name="Καθαρό Φ.Π.Α." radius={[0, 3, 3, 0]} maxBarSize={16}>
            {rows.map((d, i) => (
              <Cell key={i} fill={d.vat < 0 ? c.vatNeg : c.vat} />
            ))}
          </Bar>
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
