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
  amount: number;
  type: string | null;
  is_revenue: boolean;
  is_debt: boolean;
  vat_amount: number | null;
  vat_rate: number | null;
  date: string | null;
  description: string | null;
  source: string | null;
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
}
