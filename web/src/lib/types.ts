// Types mirroring the FastAPI payloads (server/main.py + finance.py).

export interface ClientMetrics {
  gross_rev: number;
  net_rev: number;
  gross_exp: number;
  net_exp: number;
  net_vat: number;
  debt: number;
  net_profit: number;
  taxable: number;
}

export type TaxStatus = "none" | "within" | "over";

export interface TaxInfo {
  taxable: number;
  limit: number;
  pct: number;
  rate: number;
  status: TaxStatus;
  low_rate: number;
  high_rate: number;
}

export interface ClientData {
  id: string | null;
  name: string;
  key: string;
  metrics: ClientMetrics;
  tax: TaxInfo;
}

export type VatStatus = "payable" | "refund" | "zero";

export interface HeaderTotals {
  total_gross_rev: number;
  total_net_rev: number;
  total_gross_exp: number;
  total_net_exp: number;
  total_net_profit: number;
  total_vat: number;
  total_debt: number;
  vat_status: VatStatus;
}

export interface RevenueExpensePoint {
  client: string;
  revenue: number;
  expense: number;
}

export interface VatBreakdownPoint {
  client: string;
  vat: number;
}

export interface MonthlyTrendPoint {
  month: string;
  revenue: number;
  expense: number;
}

export interface Analytics {
  revenue_vs_expenses: RevenueExpensePoint[];
  vat_breakdown: VatBreakdownPoint[];
  monthly_trend: MonthlyTrendPoint[];
}

export interface PeriodInfo {
  year: number | null;
  quarter: number | null;
  month: number | null;
  start: string | null;
  end: string | null;
  /** Years present across ALL history, so the selector can offer a year the
   *  current filter excludes. */
  available_years: number[];
  transactions_in_period: number;
  transactions_total: number;
}

export interface DashboardData {
  header: HeaderTotals;
  clients: ClientData[];
  analytics: Analytics;
  counts: {
    active_clients: number;
    archived_clients: number;
    transactions: number;
    open_debts: number;
  };
  vat_rates: { value: number; label: string }[];
  username: string;
  demo: boolean;
  /** False when the backend has no ANTHROPIC_API_KEY — the scan button is then
   *  hidden rather than offered as a control that can only fail. */
  scan_enabled: boolean;
  period: PeriodInfo;
}

/** A client row as returned by /api/v1/clients (not the finance shape). */
export interface ClientDetail {
  id: number;
  name: string;
  status: string;
  archived: boolean;
  afm: string | null;
  contact: string | null;
  notes: string | null;
  closed_date: string | null;
  created_at: string | null;
}

export interface TransactionRow {
  id: string | null;
  client: string | null;
  /** On a debt row this is what is STILL owed — a settlement shrinks it. */
  amount: number;
  type: string | null;
  is_revenue: boolean;
  is_debt: boolean;
  vat_amount: number | null;
  vat_rate: number | null;
  date: string | null;
  description: string | null;
  source: string | null;
  doc_number: string | null;
  counterparty_afm: string | null;
  /** Set on the revenue row a partial settlement created, pointing at the debt
   *  it paid down. */
  debt_id: number | null;
  /** Debt rows only — the settlement progress behind `amount`. */
  paid?: number;
  remaining?: number;
  original?: number;
}

/** One entry in a debt's settlement history. */
export interface DebtPaymentRow {
  id: number;
  debt_id: number;
  payment_txn_id: number | null;
  client: string;
  amount: number;
  /** What was still owed immediately AFTER this payment. */
  remaining: number;
  vat_amount: number | null;
  vat_rate: number | null;
  paid_date: string | null;
  kind: "full" | "partial";
  note: string | null;
  created_at: string | null;
}

export interface SettlementResult {
  ok: boolean;
  id: string;
  settled: boolean;
  paid: number;
  remaining: number;
  payment: DebtPaymentRow;
}

/** An existing row a duplicate check matched. */
export interface DuplicateTransaction {
  id: string;
  client: string | null;
  amount: number;
  type: string | null;
  date: string | null;
  doc_number: string | null;
  counterparty_afm: string | null;
  description: string | null;
}

export type DuplicateClient = ClientDetail & { matched_by: "afm" | "name" };

/** What the OCR scanner read off a document, for the user to review. */
export interface ScannedDocument {
  total_amount: number | null;
  vat_amount: number | null;
  net_amount: number | null;
  vat_rate: number | null;
  doc_date: string | null;
  doc_number: string | null;
  counterparty_name: string | null;
  counterparty_afm: string | null;
  recipient_name: string | null;
  recipient_afm: string | null;
  currency: string | null;
  document_type: string | null;
  confidence: "high" | "medium" | "low";
  notes: string | null;
  filename: string | null;
}

export interface ScanResult {
  extracted: ScannedDocument;
  /** The existing client the issuer was recognised as, if any. */
  client_match: DuplicateClient | null;
  /** Set when this document has already been filed. */
  duplicate: DuplicateTransaction | null;
}

export interface ClientSummary {
  gross_rev: number;
  net_rev: number;
  gross_exp: number;
  net_exp: number;
  net_vat: number;
  vat_status: "payable" | "refund" | "zero";
  debt: number;
  net_profit: number;
  taxable: number;
  count: number;
  open_debts: number;
}

export interface ClientDetailPayload {
  client: ClientDetail;
  summary: ClientSummary;
  transactions: TransactionRow[];
  /** NOT period-scoped: a debt raised last quarter and paid this one still has
   *  to show what has been paid against it. */
  payments: DebtPaymentRow[];
}
