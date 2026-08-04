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
  /** Α.Φ.Μ. — null on clients that have none, absent on the Airtable path. */
  afm?: string | null;
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
  doc_types: DocTypeInfo[];
  debt_alerts: DebtAlerts;
  username: string;
  /** False when the backend has no OPENAI_API_KEY — the scan button is then
   *  hidden rather than offered as a control that can only fail. */
  scan_enabled: boolean;
  period: PeriodInfo;
  /** The period's rows, newest first — the dashboard's transactions table. */
  transactions: TransactionRow[];
  /** Rides along on the dashboard payload so the page can redirect a lapsed
   *  tenant to /billing before rendering, without a second round trip. */
  subscription: SubscriptionInfo;
}

export type SubscriptionStatus = "trialing" | "active" | "inactive";

/** server/subscription.py → SubscriptionState.to_dict(). */
export interface SubscriptionInfo {
  status: SubscriptionStatus;
  /** ISO-8601 (UTC, "Z") end of the free trial. Null once paid. */
  trial_ends_at: string | null;
  /** Whole days left, rounded up. 0 unless trialing. */
  days_left: number;
  is_trialing: boolean;
  /** The paywall, in one boolean: false = writes return 402. */
  allows_writes: boolean;
  ending_soon: boolean;
  /** Trial length the backend grants, so copy never hard-codes "14". */
  trial_days: number;
}

/** GET /api/v1/billing/status — the subscription plus what billing can do. */
export interface BillingStatus extends SubscriptionInfo {
  username: string;
  email?: string;
  has_stripe_customer?: boolean;
  /** STRIPE_SECRET_KEY + STRIPE_PRICE_ID are both set. */
  stripe_configured: boolean;
  /** …and a database is configured, so checkout can actually be opened. */
  checkout_enabled: boolean;
  /** This tenant can open the Stripe customer portal: the server has a secret
   *  key AND the account has a Stripe customer to open it for. */
  portal_enabled?: boolean;
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
  /** Τύπος παραστατικού — one of DocTypeInfo["value"]. */
  doc_type: string | null;
  /** Value net of VAT, keeping the row's sign. Null when no VAT is stored. */
  net_amount: number | null;
  /** Set on the revenue row a partial settlement created, pointing at the debt
   *  it paid down. */
  debt_id: number | null;
  /** Debt rows only — the settlement progress behind `amount`. */
  paid?: number;
  remaining?: number;
  original?: number;
  /** Debt rows only — when it falls due and how late it is. */
  due_date?: string | null;
  status?: DebtStatus;
  days_overdue?: number;
}

export type DebtStatus = "overdue" | "due_soon" | "current" | "unknown";

export interface DocTypeInfo {
  value: string;
  label: string;
  /** The transaction type this document normally implies — a form default,
   *  never a constraint. */
  suggests: string;
  /** A Πιστωτικό reverses an earlier document: booked negative in its own
   *  bucket rather than as the opposite bucket. */
  credit: boolean;
}

/** One outstanding debt inside an alert row. */
export interface AlertDebt {
  id: string | null;
  amount: number;
  date: string | null;
  due_date: string | null;
  status: DebtStatus;
  days_overdue: number;
  doc_number: string | null;
  doc_type: string | null;
  description: string | null;
}

export interface DebtAlertClient {
  /** Null when the debt names a client with no row — no drawer to open. */
  id: string | null;
  name: string;
  key: string;
  total: number;
  overdue: number;
  count: number;
  /** The worst status among this client's debts. */
  status: DebtStatus;
  max_days_overdue: number;
  debts: AlertDebt[];
}

export interface AgingBucket {
  label: string;
  amount: number;
}

/**
 * Outstanding debt across the WHOLE book — deliberately not period-scoped,
 * unlike every other figure on the dashboard. An overdue debt is a fact about
 * today and must not vanish because a past period is selected.
 */
export interface DebtAlerts {
  total: number;
  overdue_total: number;
  count: number;
  overdue_count: number;
  clients_affected: number;
  clients_overdue: number;
  aging: AgingBucket[];
  clients: DebtAlertClient[];
  payment_terms: number;
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

/** Who issued the statement — the signed-in tenant, for the letterhead. */
export interface StatementIssuer {
  name: string;
  email: string | null;
}

export interface ClientDetailPayload {
  client: ClientDetail;
  /** Optional so an older backend response still typechecks. */
  issuer?: StatementIssuer;
  summary: ClientSummary;
  transactions: TransactionRow[];
  /** NOT period-scoped: a debt raised last quarter and paid this one still has
   *  to show what has been paid against it. */
  payments: DebtPaymentRow[];
}
