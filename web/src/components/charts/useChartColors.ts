"use client";

import { useTheme } from "../theme/ThemeProvider";

export interface ChartColors {
  revenue: string;
  expense: string;
  vat: string;
  vatNeg: string;
  grid: string;
  axis: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
}

export function useChartColors(): ChartColors {
  const { theme } = useTheme();
  const dark = theme === "dark";
  return {
    revenue: dark ? "#34d399" : "#059669",
    expense: dark ? "#fb7185" : "#e11d48",
    vat: dark ? "#a78bfa" : "#7c3aed",
    vatNeg: dark ? "#34d399" : "#059669",
    grid: dark ? "#1e293b" : "#e2e8f0",
    axis: dark ? "#94a3b8" : "#64748b",
    tooltipBg: dark ? "#0f172a" : "#ffffff",
    tooltipBorder: dark ? "#334155" : "#e2e8f0",
    tooltipText: dark ? "#e2e8f0" : "#0f172a",
  };
}
