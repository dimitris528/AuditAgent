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
}
