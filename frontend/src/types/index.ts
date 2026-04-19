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
}

export interface ReportMeta {
  type: string
  series_keys: string[]
  currency: string
  interval: string
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
