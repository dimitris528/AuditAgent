/**
 * What a chart panel shows when the period has nothing to plot.
 *
 * Recharts renders an empty dataset as a pair of bare axes, which on a dense
 * grid reads as a chart that failed to load rather than as a period with no
 * movements. A sentence is unambiguous and costs less vertical space.
 */
export function ChartEmpty({
  message = "Καμία κίνηση στην επιλεγμένη περίοδο.",
  height = 150,
}: {
  message?: string;
  height?: number;
}) {
  return (
    <div
      className="flex items-center justify-center rounded-lg border border-dashed border-slate-200 text-xs text-slate-500 dark:border-slate-700 dark:text-slate-400"
      style={{ height }}
    >
      {message}
    </div>
  );
}
