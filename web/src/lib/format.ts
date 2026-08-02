// Greek-locale (EUR) formatting helpers, shared by cards and charts.

const eur = new Intl.NumberFormat("el-GR", {
  style: "currency",
  currency: "EUR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const eurCompact = new Intl.NumberFormat("el-GR", {
  style: "currency",
  currency: "EUR",
  notation: "compact",
  maximumFractionDigits: 1,
});

/** Full currency, magnitude-aware (rounds -0 to 0). */
export function money(value: number | null | undefined): string {
  const n = Number(value ?? 0);
  return eur.format(Object.is(n, -0) ? 0 : n);
}

/** Absolute magnitude — matches the app's "always show magnitude" convention. */
export function moneyAbs(value: number | null | undefined): string {
  return eur.format(Math.abs(Number(value ?? 0)));
}

/** Compact currency for chart axes/tooltips ("1,2 χιλ. €"). */
export function moneyCompact(value: number | null | undefined): string {
  return eurCompact.format(Number(value ?? 0));
}

export function percent(value: number | null | undefined): string {
  return `${Math.round(Number(value ?? 0))}%`;
}

/** "2026-08" -> "Αυγ 26" for the trend axis. */
const MONTHS_EL = [
  "Ιαν", "Φεβ", "Μαρ", "Απρ", "Μαϊ", "Ιουν",
  "Ιουλ", "Αυγ", "Σεπ", "Οκτ", "Νοε", "Δεκ",
];
export function monthLabel(ym: string): string {
  const [y, m] = ym.split("-");
  const idx = Number(m) - 1;
  if (idx < 0 || idx > 11) return ym;
  return `${MONTHS_EL[idx]} ${y.slice(2)}`;
}
