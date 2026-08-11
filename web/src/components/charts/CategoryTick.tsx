"use client";

import { truncateLabel } from "./horizontal";

/**
 * A client name on a horizontal chart's category axis, on exactly ONE line.
 *
 * Recharts' default tick wraps at word boundaries as soon as the text is wider
 * than the axis, which on Greek company names turns half the labels into two
 * lines — the rows then no longer line up with the bars the chart height was
 * calculated for, and the ellipsis ends up stranded on a line of its own.
 *
 * SVG <text> does not wrap, so rendering the tick directly makes single-line
 * the guaranteed outcome rather than the lucky one. The trimming is ours (see
 * truncateLabel), and the full name is always in the tooltip.
 */
interface TickProps {
  x?: number;
  y?: number;
  payload?: { value?: string | number };
  fill?: string;
  /** Characters before the ellipsis — must match what labelWidth() sized for. */
  max?: number;
}

export function CategoryTick({ x = 0, y = 0, payload, fill, max = 18 }: TickProps) {
  return (
    <text
      x={x}
      // dy centres the cap-height on the tick rather than sitting the baseline
      // on it, which is what makes the label look level with its bar.
      y={y}
      dy={4}
      textAnchor="end"
      fontSize={11}
      fill={fill}
    >
      {truncateLabel(String(payload?.value ?? ""), max)}
    </text>
  );
}
