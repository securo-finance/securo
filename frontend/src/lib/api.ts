import axios from 'axios'
import type {
  User,
  Category,
  BankConnection,
  ConnectionSettings,
  Account,
  AccountSummary,
  Transaction,
  Payee,
  PayeeSummary,
  RecurringTransaction,
  ProjectedTransaction,
  BudgetVsActual,
  Rule,
  ImportLog,
  Asset,
  AssetValue,
  Attachment,
  DashboardSummary,
  SpendingByCategory,
  MonthlyTrend,
  BalanceHistory,
  PaginatedResponse,
  ReportResponse,
} from '@/types'

const api = axios.create({
  baseURL: '/api',
})

// Add auth token to requests
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Handle auth errors
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

// Setup
export const setup = {
  status: async (): Promise<{ has_users: boolean }> => {
    const { data } = await api.get('/setup/status')
    return data
  },
  createAdmin: async (email: string, password: string, currency = 'USD', name = '', language = 'en'): Promise<{ access_token: string }> => {
    const { data } = await api.post('/setup/create-admin', { email, password, currency, name, language })
    return data
  },
}

// Auth
export const auth = {
  login: async (email: string, password: string) => {
    const formData = new URLSearchParams()
    formData.append('username', email)
    formData.append('password', password)
    const { data } = await api.post('/auth/login', formData, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    })
    return data
  },
  register: async (email: string, password: string) => {
    const { data } = await api.post('/auth/register', { email, password })
    return data
  },
  me: async (): Promise<User> => {
    const { data } = await api.get('/users/me')
    return data
  },
  updateMe: async (updates: Partial<User>): Promise<User> => {
    const { data } = await api.patch('/users/me', updates)
    return data
  },
  changePassword: async (password: string): Promise<User> => {
    const { data } = await api.patch('/users/me', { password })
    return data
  },
}

// Categories
export const categories = {
  list: async (): Promise<Category[]> => {
    const { data } = await api.get('/categories')
    return data
  },
  create: async (category: Partial<Category>): Promise<Category> => {
    const { data } = await api.post('/categories', category)
    return data
  },
  update: async (id: string, category: Partial<Category>): Promise<Category> => {
    const { data } = await api.patch(`/categories/${id}`, category)
    return data
  },
  delete: async (id: string): Promise<void> => {
    await api.delete(`/categories/${id}`)
  },
}

// Bank Connections
export const connections = {
  list: async (): Promise<BankConnection[]> => {
    const { data } = await api.get('/connections')
    return data
  },
  getProviders: async (): Promise<{ name: string; display_name: string; description: string; flow_type: string; configured: boolean }[]> => {
    const { data } = await api.get('/connections/providers')
    return data.providers
  },
  getConnectToken: async (provider = 'pluggy'): Promise<string> => {
    const { data } = await api.post('/connections/connect-token', { provider })
    return data.access_token
  },
  getOAuthUrl: async (provider: string): Promise<string> => {
    const { data } = await api.post('/connections/oauth/url', { provider })
    return data.url
  },
  handleCallback: async (code: string, provider: string): Promise<BankConnection> => {
    const { data } = await api.post('/connections/oauth/callback', { code, provider })
    return data
  },
  sync: async (id: string): Promise<BankConnection> => {
    const { data } = await api.post(`/connections/${id}/sync`)
    return data
  },
  getReconnectToken: async (connectionId: string): Promise<string> => {
    const { data } = await api.post(`/connections/${connectionId}/reconnect-token`)
    return data.access_token
  },
  updateSettings: async (id: string, settings: Partial<ConnectionSettings>): Promise<BankConnection> => {
    const { data } = await api.patch(`/connections/${id}/settings`, settings)
    return data
  },
  delete: async (id: string): Promise<void> => {
    await api.delete(`/connections/${id}`)
  },
}

// Accounts
export const accounts = {
  list: async (includeClosed = false): Promise<Account[]> => {
    const { data } = await api.get('/accounts', { params: { include_closed: includeClosed } })
    return data
  },
  get: async (id: string): Promise<Account> => {
    const { data } = await api.get(`/accounts/${id}`)
    return data
  },
  create: async (account: { name: string; type: string; balance?: number; currency?: string }): Promise<Account> => {
    const { data } = await api.post('/accounts', account)
    return data
  },
  update: async (id: string, account: Partial<Account>): Promise<Account> => {
    const { data } = await api.patch(`/accounts/${id}`, account)
    return data
  },
  delete: async (id: string): Promise<void> => {
    await api.delete(`/accounts/${id}`)
  },
  summary: async (id: string, from?: string, to?: string): Promise<AccountSummary> => {
    const { data } = await api.get(`/accounts/${id}/summary`, { params: { from, to } })
    return data
  },
  balanceHistory: async (id: string, from?: string, to?: string): Promise<{ date: string; balance: number; balance_primary?: number }[]> => {
    const { data } = await api.get(`/accounts/${id}/balance-history`, { params: { from, to } })
    return data
  },
  close: async (id: string): Promise<Account> => {
    const { data } = await api.post(`/accounts/${id}/close`)
    return data
  },
  reopen: async (id: string): Promise<Account> => {
    const { data } = await api.post(`/accounts/${id}/reopen`)
    return data
  },
}

// Transactions
export const transactions = {
  list: async (params?: {
    account_id?: string
    category_id?: string
    payee_id?: string
    uncategorized?: boolean
    type?: string
    from?: string
    to?: string
    q?: string
    page?: number
    limit?: number
    include_opening_balance?: boolean
  }): Promise<PaginatedResponse<Transaction>> => {
    const { data } = await api.get('/transactions', { params })
    return data
  },
  get: async (id: string): Promise<Transaction> => {
    const { data } = await api.get(`/transactions/${id}`)
    return data
  },
  create: async (transaction: Partial<Transaction>): Promise<Transaction> => {
    const { data } = await api.post('/transactions', transaction)
    return data
  },
  update: async (id: string, transaction: Partial<Transaction>): Promise<Transaction> => {
    const { data } = await api.patch(`/transactions/${id}`, transaction)
    return data
  },
  delete: async (id: string): Promise<void> => {
    await api.delete(`/transactions/${id}`)
  },
  createTransfer: async (transfer: {
    from_account_id: string
    to_account_id: string
    amount: number
    date: string
    description: string
    notes?: string
    fx_rate?: number
  }): Promise<{ debit: Transaction; credit: Transaction; transfer_pair_id: string }> => {
    const { data } = await api.post('/transactions/transfer', transfer)
    return data
  },
  bulkCategorize: async (transactionIds: string[], categoryId: string | null): Promise<{ updated: number }> => {
    const { data } = await api.patch('/transactions/bulk-categorize', {
      transaction_ids: transactionIds,
      category_id: categoryId,
    })
    return data
  },
  previewImport: async (file: File, options?: {
    date_format?: string
    flip_amount?: boolean
    inflow_column?: string
    outflow_column?: string
  }): Promise<{ transactions: Transaction[]; detected_format: string }> => {
    const formData = new FormData()
    formData.append('file', file)
    if (options?.date_format) formData.append('date_format', options.date_format)
    if (options?.flip_amount) formData.append('flip_amount', 'true')
    if (options?.inflow_column) formData.append('inflow_column', options.inflow_column)
    if (options?.outflow_column) formData.append('outflow_column', options.outflow_column)
    const { data } = await api.post('/transactions/import/preview', formData)
    return data
  },
  import: async (account_id: string, transactions: Transaction[], filename: string, detected_format: string): Promise<{ imported: number; skipped: number; import_log_id: string }> => {
    const { data } = await api.post('/transactions/import', { account_id, transactions, filename, detected_format })
    return data
  },
  export: async (params?: {
    account_id?: string
    category_id?: string
    uncategorized?: boolean
    type?: string
    from?: string
    to?: string
    q?: string
  }): Promise<void> => {
    const { data } = await api.get('/transactions/export', { params, responseType: 'blob' })
    const blob = new Blob([data], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `flux-transactions-${new Date().toISOString().slice(0, 10)}.csv`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  },
  attachments: {
    list: async (transactionId: string): Promise<Attachment[]> => {
      const { data } = await api.get(`/transactions/${transactionId}/attachments`)
      return data
    },
    upload: async (transactionId: string, file: File): Promise<Attachment> => {
      const formData = new FormData()
      formData.append('file', file)
      const { data } = await api.post(`/transactions/${transactionId}/attachments`, formData)
      return data
    },
    downloadUrl: async (transactionId: string, attachmentId: string): Promise<string> => {
      const { data } = await api.get(`/transactions/${transactionId}/attachments/${attachmentId}`, {
        responseType: 'blob',
      })
      return URL.createObjectURL(data)
    },
    rename: async (transactionId: string, attachmentId: string, filename: string): Promise<Attachment> => {
      const { data } = await api.patch(`/transactions/${transactionId}/attachments/${attachmentId}`, { filename })
      return data
    },
    delete: async (transactionId: string, attachmentId: string): Promise<void> => {
      await api.delete(`/transactions/${transactionId}/attachments/${attachmentId}`)
    },
  },
}

// Payees
export const payees = {
  list: async (): Promise<Payee[]> => {
    const { data } = await api.get('/payees')
    return data
  },
  get: async (id: string): Promise<Payee> => {
    const { data } = await api.get(`/payees/${id}`)
    return data
  },
  summary: async (id: string, from?: string, to?: string): Promise<PayeeSummary> => {
    const { data } = await api.get(`/payees/${id}/summary`, { params: { from, to } })
    return data
  },
  create: async (payee: { name: string; type?: string; notes?: string }): Promise<Payee> => {
    const { data } = await api.post('/payees', payee)
    return data
  },
  update: async (id: string, payee: Partial<Payee>): Promise<Payee> => {
    const { data } = await api.patch(`/payees/${id}`, payee)
    return data
  },
  delete: async (id: string): Promise<void> => {
    await api.delete(`/payees/${id}`)
  },
  merge: async (targetId: string, sourceIds: string[]): Promise<{ merged: number; transactions_reassigned: number }> => {
    const { data } = await api.post('/payees/merge', { target_id: targetId, source_ids: sourceIds })
    return data
  },
}

// Categorization Rules
export const rules = {
  list: async (): Promise<Rule[]> => {
    const { data } = await api.get('/rules')
    return data
  },
  create: async (rule: Omit<Rule, 'id' | 'user_id'>): Promise<Rule> => {
    const { data } = await api.post('/rules', rule)
    return data
  },
  update: async (id: string, rule: Partial<Rule>): Promise<Rule> => {
    const { data } = await api.patch(`/rules/${id}`, rule)
    return data
  },
  delete: async (id: string): Promise<void> => {
    await api.delete(`/rules/${id}`)
  },
  applyAll: async (): Promise<{ applied: number }> => {
    const { data } = await api.post('/rules/apply-all')
    return data
  },
  packs: async (): Promise<{ code: string; name: string; flag: string; rule_count: number; installed: boolean }[]> => {
    const { data } = await api.get('/rules/packs')
    return data
  },
  installPack: async (packCode: string): Promise<{ installed: number }> => {
    const { data } = await api.post(`/rules/packs/${packCode}/install`)
    return data
  },
}

// Recurring Transactions
export const recurring = {
  list: async (): Promise<RecurringTransaction[]> => {
    const { data } = await api.get('/recurring-transactions')
    return data
  },
  create: async (rt: Partial<RecurringTransaction>): Promise<RecurringTransaction> => {
    const { data } = await api.post('/recurring-transactions', rt)
    return data
  },
  update: async (id: string, rt: Partial<RecurringTransaction>): Promise<RecurringTransaction> => {
    const { data } = await api.patch(`/recurring-transactions/${id}`, rt)
    return data
  },
  delete: async (id: string): Promise<void> => {
    await api.delete(`/recurring-transactions/${id}`)
  },
  generate: async (): Promise<{ generated: number }> => {
    const { data } = await api.post('/recurring-transactions/generate')
    return data
  },
}

// Budgets
export const budgets = {
  comparison: async (month?: string): Promise<BudgetVsActual[]> => {
    const { data } = await api.get('/budgets/comparison', { params: { month } })
    return data
  },
}

// Dashboard
export const dashboard = {
  summary: async (month?: string, balanceDate?: string): Promise<DashboardSummary> => {
    const { data } = await api.get('/dashboard/summary', { params: { month, balance_date: balanceDate } })
    return data
  },
  spendingByCategory: async (month?: string): Promise<SpendingByCategory[]> => {
    const { data } = await api.get('/dashboard/spending-by-category', { params: { month } })
    return data
  },
  monthlyTrend: async (months = 6): Promise<MonthlyTrend[]> => {
    const { data } = await api.get('/dashboard/monthly-trend', { params: { months } })
    return data
  },
  projectedTransactions: async (month?: string): Promise<ProjectedTransaction[]> => {
    const { data } = await api.get('/dashboard/projected-transactions', { params: { month } })
    return data
  },
  balanceHistory: async (month?: string): Promise<BalanceHistory> => {
    const { data } = await api.get('/dashboard/balance-history', { params: { month } })
    return data
  },
}

// Assets
export const assets = {
  list: async (includeArchived = false): Promise<Asset[]> => {
    const { data } = await api.get('/assets', { params: { include_archived: includeArchived } })
    return data
  },
  get: async (id: string): Promise<Asset> => {
    const { data } = await api.get(`/assets/${id}`)
    return data
  },
  create: async (asset: Partial<Asset> & { name: string; type: string; current_value?: number }): Promise<Asset> => {
    const { data } = await api.post('/assets', asset)
    return data
  },
  update: async (id: string, asset: Partial<Asset>, opts?: { regenerateGrowth?: boolean }): Promise<Asset> => {
    const { data } = await api.patch(`/assets/${id}`, asset, {
      params: opts?.regenerateGrowth ? { regenerate_growth: true } : undefined,
    })
    return data
  },
  delete: async (id: string): Promise<void> => {
    await api.delete(`/assets/${id}`)
  },
  values: async (id: string): Promise<AssetValue[]> => {
    const { data } = await api.get(`/assets/${id}/values`)
    return data
  },
  valueTrend: async (id: string, months = 12): Promise<{ date: string; amount: number }[]> => {
    const { data } = await api.get(`/assets/${id}/value-trend`, { params: { months } })
    return data
  },
  addValue: async (id: string, value: { amount: number; date: string }): Promise<AssetValue> => {
    const { data } = await api.post(`/assets/${id}/values`, value)
    return data
  },
  deleteValue: async (valueId: string): Promise<void> => {
    await api.delete(`/assets/values/${valueId}`)
  },
  portfolioTrend: async (): Promise<{ assets: { id: string; name: string; type: string }[]; trend: Record<string, unknown>[]; total: number }> => {
    const { data } = await api.get('/assets/portfolio-trend')
    return data
  },
}

// Reports
export const reports = {
  netWorth: async (months = 12, interval = 'monthly'): Promise<ReportResponse> => {
    const { data } = await api.get('/reports/net-worth', { params: { months, interval } })
    return data
  },
  incomeExpenses: async (months = 12, interval = 'monthly'): Promise<ReportResponse> => {
    const { data } = await api.get('/reports/income-expenses', { params: { months, interval } })
    return data
  },
}

// Currencies
export const currencies = {
  list: async (): Promise<{ code: string; symbol: string; name: string; flag: string }[]> => {
    const { data } = await api.get('/currencies')
    return data
  },
}

// FX Rates
export const fxRates = {
  refresh: async (): Promise<{ synced: boolean; rates_count: number; date: string }> => {
    const { data } = await api.post('/fx-rates/refresh')
    return data
  },
  status: async (): Promise<{ last_sync_date: string | null; total_rates: number }> => {
    const { data } = await api.get('/fx-rates/status')
    return data
  },
}

// Import Logs
export const importLogs = {
  list: async (): Promise<ImportLog[]> => {
    const { data } = await api.get('/import-logs')
    return data
  },
  delete: async (id: string): Promise<void> => {
    await api.delete(`/import-logs/${id}`)
  },
}

// Settings
export const settings = {
  attachments: async (): Promise<{ allowed_extensions: string[]; max_file_size_mb: number; max_attachments_per_transaction: number }> => {
    const { data } = await api.get('/settings/attachments')
    return data
  },
}

// Backup
export const backup = {
  download: async (): Promise<void> => {
    const { data } = await api.get('/export/backup', { responseType: 'blob' })
    const blob = new Blob([data], { type: 'application/zip' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `flux-backup-${new Date().toISOString().slice(0, 10)}.zip`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  },
}

export default api
