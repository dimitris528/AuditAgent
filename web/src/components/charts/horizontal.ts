/**
 * Geometry for the per-client HORIZONTAL bar charts.
 *
 * Why horizontal at all: the category on these charts is a client name, and
 * Greek company names are long ("Νησίδα Café Μονοπρόσωπη Ι.Κ.Ε."). On a
 * vertical bar chart those names live on the X axis, where the only ways to fit
 * them are rotation, truncation, or dropping every other tick — all three of
 * which make a chart you have to decode rather than read. Turned on its side
 * the name gets a whole line of its own and the bar length stays the thing the
 * eye compares.
 *
 * The consequence is that HEIGHT now scales with the number of clients instead
 * of being a fixed box, which is what the helpers here are for.
 */

/**
 * How many clients a per-client chart draws.
 *
 * A book with sixty clients does not want sixty bars in a dashboard panel — it
 * wants the ones that move the totals. The rest are still in the table below
 * and in the CSV export, and the chart says how many it left out rather than
 * quietly showing a partial picture.
 */
export const MAX_ROWS = 8;

/**
 * The `limit` biggest rows, largest first.
 *
 * `weight` returns the MAGNITUDE to rank by, so a large VAT credit sorts
 * alongside a large VAT bill rather than at the bottom of the list. Largest
 * first also puts the biggest bar at the top: recharts lays a category axis out
 * in data order, top to bottom.
 */
export function topRows<T>(
  data: readonly T[],
  weight: (row: T) => number,
  limit: number = MAX_ROWS,
): T[] {
  return [...data]
    .sort((a, b) => Math.abs(weight(b)) - Math.abs(weight(a)))
    .slice(0, limit);
}

/** A label trimmed to `max` characters, with an ellipsis when it was cut. */
export function truncateLabel(value: string, max = 18): string {
  const text = String(value ?? "");
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/**
 * Width for the category axis: wide enough for the longest name on screen,
 * capped so one very long client cannot eat the plot area.
 *
 * ~7px per character, which is a slight OVER-estimate for the 11px axis font.
 * That is deliberate: recharts word-wraps a tick that does not fit its axis
 * width, and a label that silently becomes two lines undoes the row spacing the
 * chart height was calculated from. Erring wide costs a few pixels of plot;
 * erring narrow costs the layout.
 */
export function labelWidth(labels: readonly string[], max = 18): number {
  const longest = labels.reduce(
    (n, l) => Math.max(n, truncateLabel(l, max).length),
    0,
  );
  return Math.round(Math.min(max * 7 + 12, Math.max(70, longest * 7 + 12)));
}

/**
 * Chart height for `rows` bars.
 *
 * `chrome` is the axis and legend allowance; `perRow` the vertical slot one
 * client gets. The floor keeps a one-client chart from collapsing into a strip
 * with no room for its axis; the ceiling keeps a busy book from pushing the
 * panel taller than the right-hand rail beside it.
 */
export function barChartHeight(
  rows: number,
  { perRow = 30, chrome = 44, min = 150, max = 330 } = {},
): number {
  return Math.round(
    Math.min(max, Math.max(min, chrome + Math.max(rows, 1) * perRow)),
  );
}
