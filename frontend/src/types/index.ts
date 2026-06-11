export interface User {
  id: string
  email: string
  is_active: boolean
  is_superuser: boolean
  is_verified: boolean
  is_2fa_enabled: boolean
  preferences: UserPreferences
}

export interface AdminUser {
  id: string
  email: string
  is_active: boolean
  is_superuser: boolean
  is_verified: boolean
  preferences: UserPreferences | null
}

export interface AdminUserList {
  items: AdminUser[]
  total: number
}

export interface AppSetting {
  key: string
  value: string
}

export type WorkspaceRole = 'owner' | 'editor' | 'viewer' | 'manager'

export interface Workspace {
  id: string
  name: string
  kind: string
  is_archived: boolean
  default_currency: string
  locale: string | null
  icon: string | null
  color: string | null
  created_at: string
  created_by_user_id: string | null
  managed_by_user_id: string | null
  role: WorkspaceRole | null
}

export interface WorkspaceMember {
  id: string
  user_id: string
  email: string
  display_name: string | null
  role: WorkspaceRole
  joined_at: string
}

export interface UserPreferences {
  language?: string
  date_format?: string
  timezone?: string
  currency_display?: string
  display_name?: string
  onboarding_completed?: boolean
}

export interface Category {
  id: string
  user_id: string
  group_id: string | null
  name: string
  icon: string
  color: string
  is_system: boolean
  treat_as_transfer: boolean
  is_ignored: boolean
}

export interface CategoryGroup {
  id: string
  user_id: string
  name: string
  icon: string
  color: string
  position: number
  is_system: boolean
  categories: Category[]
}

export interface BankConnection {
  id: string
  user_id: string
  provider: string
  institution_name: string
  display_name: string | null
  logo_url: string | null
  external_id: string
  status: string
  settings: ConnectionSettings | null
  last_sync_at: string | null
  created_at: string
}

export interface ConnectionSettings {
  payee_source?: 'auto' | 'merchant' | 'payment_data' | 'description' | 'none'
  import_pending?: boolean
}

export interface Account {
  id: string
  user_id: string
  connection_id: string | null
  external_id: string | null
  name: string
  display_name: string | null
  // Denormalized bank identity from the linked connection (null for manual
  // accounts). Used to render the institution logo next to the account.
  institution_name: string | null
  institution_logo_url: string | null
  type: string
  balance: number
  current_balance: number
  previous_balance: number | null
  balance_primary: number | null
  currency: string
  credit_limit: number | null
  available_credit: number | null
  statement_close_day: number | null
  payment_due_day: number | null
  next_close_date: string | null
  next_due_date: string | null
  minimum_payment: number | null
  card_brand: string | null
  card_level: string | null
  is_closed: boolean
  closed_at: string | null
}

export interface CreditCardBill {
  id: string
  account_id: string
  external_id: string
  due_date: string // YYYY-MM-DD
  total_amount: number
  currency: string
  minimum_payment: number | null
}

export interface Collection {
  id: string
  user_id: string
  name: string
  icon: string
  color: string
  position: number
  account_ids: string[]
  account_count: number
  wallet_ids: string[]
  wallet_count: number
}

export interface AccountSummary {
  account_id: string
  current_balance: number
  monthly_income: number
  monthly_expenses: number
  current_balance_primary: number | null
  monthly_income_primary: number | null
  monthly_expenses_primary: number | null
}

export interface Transaction {
  id: string
  user_id: string
  account_id: string | null
  category_id: string | null
  category: Category | null
  external_id: string | null
  description: string
  amount: number
  currency: string
  date: string
  type: 'debit' | 'credit'
  source: string
  status: 'posted' | 'pending'
  payee: string | null
  payee_id: string | null
  payee_name: string | null
  notes: string | null
  transfer_pair_id: string | null
  amount_primary: number | null
  fx_rate_used: number | null
  fx_fallback: boolean
  attachment_count?: number
  installment_number: number | null
  total_installments: number | null
  installment_total_amount: number | null
  installment_purchase_date: string | null
  bill_id: string | null
  // Manual override for which credit-card bill cycle this tx belongs to
  // (issue #92). Empty / null = use auto bucketing (Pluggy bill_id when
  // available, cycle math otherwise). Setting it forces the tx into the
  // bill whose due_date matches.
  effective_bill_date: string | null
  splits: TransactionSplit[]
  // Shared-transaction view fields. Set per-request when the viewer
  // is a linked split member but not the owner. Render `viewer_share`
  // as the amount and treat the row as read-only — editing belongs
  // to the parent's owner.
  is_shared?: boolean
  viewer_share?: number | null
  group_id?: string | null
  // Display name of the parent's owner (the person who actually paid).
  // Derived per-request from the group's `is_self` member.
  parent_owner_name?: string | null
  // Flag to exclude this transaction from reports and dashboard aggregations
  is_ignored: boolean
}

export type ShareType = 'equal' | 'exact' | 'percent'

export interface TransactionSplit {
  id: string
  transaction_id: string
  group_member_id: string
  share_amount: number
  share_type: string
  share_pct: number | null
  notes: string | null
  created_at: string
}

export interface TransactionSplitInput {
  group_member_id: string
  share_amount?: number | null
  share_pct?: number | null
  notes?: string | null
}

export interface TransactionSplitsInput {
  share_type: ShareType
  splits: TransactionSplitInput[]
}

export type GroupKind = 'social' | 'cost_center' | 'project' | 'client' | 'other'

export interface Group {
  id: string
  user_id: string
  name: string
  kind: GroupKind
  default_currency: string
  icon: string
  color: string
  is_archived: boolean
  // Derived server-side per request. False = the current user is a
  // linked member, not the owner — UI should hide edit affordances.
  is_owner: boolean
  notes: string | null
  created_at: string
  members: GroupMember[]
}

export interface GroupMember {
  id: string
  group_id: string
  name: string
  linked_user_id: string | null
  email: string | null
  is_self: boolean
  created_at: string
}

export interface GroupSettlement {
  id: string
  group_id: string
  from_member_id: string
  to_member_id: string
  amount: number
  currency: string
  date: string
  transaction_id: string | null
  notes: string | null
  created_at: string
}

export interface GroupBalanceLine {
  member_id: string
  currency: string
  // Positive = member owes the owner. Negative = owner owes member.
  amount: number
  // FX-converted to the group's default currency for cross-currency rollups.
  amount_in_default_currency: number
}

export interface GroupBalances {
  group_id: string
  self_member_id: string | null
  default_currency: string
  lines: GroupBalanceLine[]
}

export interface Payee {
  id: string
  user_id: string
  name: string
  type: 'merchant' | 'person' | 'company'
  is_favorite: boolean
  notes: string | null
  created_at: string
  transaction_count: number
}

export interface PayeeSummary {
  payee: Payee
  total_spent: number
  total_received: number
  transaction_count: number
  most_common_category: Category | null
  last_transaction_date: string | null
}

export interface RuleCondition {
  field: string
  op: string
  value: string | number
}

export interface RuleAction {
  op: string
  value: string
}

export interface Rule {
  id: string
  user_id: string
  name: string
  conditions_op: 'and' | 'or'
  conditions: RuleCondition[]
  actions: RuleAction[]
  priority: number
  is_active: boolean
}

export interface ImportLog {
  id: string
  user_id: string
  account_id: string
  account_name: string | null
  filename: string
  format: string
  transaction_count: number
  total_credit: number
  total_debit: number
  created_at: string
}

export interface ImportPreviewTransaction {
  description: string
  amount: number
  date: string
  type: 'debit' | 'credit'
  external_id?: string | null
  currency?: string | null
  fx_rate?: number | null
  payee_raw?: string | null
  category_name?: string | null
  suggested_category_id?: string | null
  suggested_category_name?: string | null
  excluded?: boolean
  category_id?: string | null
  force_uncategorized?: boolean
}

export interface ImportReviewTransaction extends ImportPreviewTransaction {
  _id: string
  excluded: boolean
  selected_category_id?: string | null
}

export interface RecurringTransaction {
  id: string
  user_id: string
  account_id: string | null
  category_id: string | null
  description: string
  amount: number
  currency: string
  type: 'debit' | 'credit'
  frequency: 'monthly' | 'weekly' | 'yearly'
  day_of_month: number | null
  start_date: string
  end_date: string | null
  is_active: boolean
  next_occurrence: string
  amount_primary: number | null
  fx_rate_used: number | null
}

export interface ProjectedTransaction {
  recurring_id: string
  description: string
  amount: number
  amount_primary: number | null
  currency: string
  type: 'debit' | 'credit'
  date: string
  category_id: string | null
  category_name: string | null
  category_icon: string | null
  category_color: string | null
  is_ignored: boolean
}

export interface DashboardSummary {
  total_balance: Record<string, number>
  total_balance_primary: number
  balance_date: string
  monthly_income: number
  monthly_expenses: number
  monthly_income_primary: number
  monthly_expenses_primary: number
  accounts_count: number
  pending_categorization: number
  pending_categorization_amount: number
  assets_value: Record<string, number>
  assets_value_primary: number
  primary_currency: string
  // Net pending balance from group splits in primary currency.
  // Negative = net liability, positive = net receivable. Already
  // accounts for partial settlements.
  pending_shares_net: number
}

export interface SpendingByCategory {
  category_id: string | null
  category_name: string
  category_icon: string
  category_color: string
  total: number
  percentage: number
}

export interface MonthlyTrend {
  month: string
  income: number
  expenses: number
}

export interface DailyBalance {
  day: number
  balance: number | null
}

export interface BalanceHistory {
  current: DailyBalance[]
  previous: DailyBalance[]
}

export interface Budget {
  id: string
  user_id: string
  category_id: string
  amount: number
  month: string
  is_recurring: boolean
}

export interface BudgetVsActual {
  category_id: string
  category_name: string
  category_icon: string
  category_color: string
  group_id: string | null
  group_name: string | null
  budget_amount: number | null
  actual_amount: number
  prev_month_amount: number
  percentage_used: number | null
  is_recurring: boolean
}

export interface Asset {
  id: string
  user_id: string
  name: string
  type: string
  currency: string
  units: number | null
  valuation_method: string
  purchase_date: string | null
  purchase_price: number | null
  sell_date: string | null
  sell_price: number | null
  growth_type: string | null
  growth_rate: number | null
  growth_frequency: string | null
  growth_start_date: string | null
  is_archived: boolean
  position: number
  current_value: number | null
  current_value_primary: number | null
  gain_loss: number | null
  gain_loss_primary: number | null
  value_count: number
  source: string
  connection_id: string | null
  isin: string | null
  maturity_date: string | null
  group_id: string | null
  ticker: string | null
  ticker_exchange: string | null
  last_price: number | null
  last_price_at: string | null
  logo_url: string | null
  // Ledger-derived (issue #235): weighted-average cost per unit (preço médio),
  // cost basis of held units, cumulative realized gain, and whether the holding
  // is driven by the transactions ledger.
  average_price: number | null
  total_invested: number | null
  realized_gain: number | null
  transaction_count: number
}

export interface AssetTransaction {
  id: string
  asset_id: string
  kind: 'buy' | 'sell'
  quantity: number
  price: number
  fee: number
  date: string
  source: string
  notes: string | null
  asset_name: string | null
  ticker: string | null
  currency: string | null
  logo_url: string | null
}

export interface MarketSymbolMatch {
  symbol: string
  name: string | null
  exchange: string | null
  quote_type: string | null
}

export interface MarketSymbolQuote {
  symbol: string
  name: string | null
  exchange: string | null
  currency: string
  price: number
  quote_type: string | null
}

export interface AssetGroup {
  id: string
  user_id: string
  name: string
  icon: string
  color: string
  position: number
  source: string
  connection_id: string | null
  institution_name: string | null
  asset_count: number
  current_value: number
  current_value_primary: number
}

export interface AssetValue {
  id: string
  asset_id: string
  amount: number
  date: string
  source: string
}

export interface Goal {
  id: string
  user_id: string
  name: string
  target_amount: number
  current_amount: number
  currency: string
  target_amount_primary: number | null
  current_amount_primary: number | null
  target_date: string | null
  tracking_type: 'manual' | 'account' | 'asset' | 'net_worth'
  account_id: string | null
  asset_id: string | null
  status: 'active' | 'completed' | 'paused' | 'archived'
  icon: string | null
  color: string | null
  position: number
  metadata_json: Record<string, unknown> | null
  created_at: string
  updated_at: string
  percentage: number
  monthly_contribution: number | null
  on_track: 'ahead' | 'on_track' | 'behind' | 'overdue' | 'achieved' | null
  account_name: string | null
  asset_name: string | null
}

export interface GoalSummary {
  id: string
  name: string
  target_amount: number
  current_amount: number
  currency: string
  target_date: string | null
  status: string
  icon: string | null
  color: string | null
  percentage: number
  monthly_contribution: number | null
  on_track: string | null
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  limit: number
}

// Income / expense / net totals for all transactions matching the active
// filters (issue #185) — accompanies the paginated /transactions response.
export interface TransactionsSummary {
  income: number
  expense: number
  net: number
  // Absolute total of everything excluded from income/expense for the same
  // rows — transfers, treat_as_transfer categories and ignored items (#242).
  excluded: number
  currency: string
}

export interface PaginatedTransactions extends PaginatedResponse<Transaction> {
  summary?: TransactionsSummary
}

// Reports (universal schema for all report types)
export interface ReportBreakdown {
  key: string
  label: string
  value: number
  color: string
}

export interface ReportSummary {
  primary_value: number
  change_amount: number
  change_percent: number | null
  breakdowns: ReportBreakdown[]
}

export interface ReportDataPoint {
  date: string
  value: number
  breakdowns: Record<string, number>
  change: number | null
}

export interface ReportMeta {
  type: string
  series_keys: string[]
  currency: string
  interval: string
  forecast_start_date?: string | null
  baseline_active?: boolean
  baseline_lookback_days?: number | null
}

export interface ReportCompositionItem {
  key: string
  label: string
  value: number
  color: string
  group: string
}

export interface CategoryTrendItem {
  key: string
  label: string
  color: string
  total: number
  group: string
  series: ReportDataPoint[]
}

export interface Attachment {
  id: string
  transaction_id: string
  filename: string
  content_type: string
  size: number
  created_at: string
}

export interface ReportResponse {
  summary: ReportSummary
  trend: ReportDataPoint[]
  meta: ReportMeta
  composition: ReportCompositionItem[]
  category_trend: CategoryTrendItem[]
}
