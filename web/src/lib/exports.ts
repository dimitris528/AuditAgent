import type { PeriodInfo } from "./types";

/**
 * The three tables that can be downloaded as a spreadsheet.
 *
 * Kept as a union with a matching label map so a caller cannot invent a fourth
 * one that 404s: every export UI in the app builds its links from here, and the
 * BFF route (app/api/exports/[kind]) whitelists exactly these names.
 */
export type ExportKind = "transactions" | "clients" | "invoices";

export const EXPORT_KINDS: ExportKind[] = ["transactions", "clients", "invoices"];

export const EXPORT_LABELS: Record<ExportKind, string> = {
  transactions: "Κινήσεις",
  clients: "Πελάτες",
  invoices: "Παραστατικά",
};

export const EXPORT_HINTS: Record<ExportKind, string> = {
  transactions: "Όλες οι κινήσεις της περιόδου με Φ.Π.Α. και κατάσταση πληρωμής",
  clients: "Πελατολόγιο με Α.Φ.Μ., στοιχεία επικοινωνίας και σύνοψη περιόδου",
  invoices: "Μόνο οι κινήσεις με παραστατικό (τιμολόγια, αποδείξεις, πιστωτικά)",
};

/**
 * CSV flavour.
 *
 * "excel" is UTF-8-with-BOM, semicolon-delimited, decimal commas — the only
 * combination a Greek-locale Excel opens correctly on a double click. "iso" is
 * RFC 4180 for everything that is not Excel. See server/exports.py for why the
 * three parts of the Excel dialect have to travel together.
 */
export type ExportDialect = "excel" | "iso";

export type ExportPeriod = Pick<PeriodInfo, "year" | "quarter" | "month">;

/**
 * The same-origin download URL for one table.
 *
 * The period travels with every export so the file matches what was on screen
 * when the button was pressed — a download that silently covers the whole book
 * is the one people reconcile against by mistake.
 */
export function exportUrl(
  kind: ExportKind,
  {
    period,
    clientId,
    dialect,
  }: {
    period?: ExportPeriod | null;
    clientId?: number | null;
    dialect?: ExportDialect;
  } = {},
): string {
  const qs = new URLSearchParams();
  if (period?.year) qs.set("year", String(period.year));
  if (period?.quarter) qs.set("quarter", String(period.quarter));
  if (period?.month) qs.set("month", String(period.month));
  if (clientId != null) qs.set("client_id", String(clientId));
  // Omitted when it is the default, so the common URL stays short and
  // shareable.
  if (dialect && dialect !== "excel") qs.set("dialect", dialect);
  return `/api/exports/${kind}${qs.toString() ? `?${qs}` : ""}`;
}
