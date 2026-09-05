import { lazy, Suspense } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { ThemeProvider } from '@/components/theme-provider'
import { AuthProvider } from '@/contexts/auth-context'
import { WorkspaceProvider } from '@/contexts/workspace-context'
import { CollectionFilterProvider } from '@/contexts/collection-filter-context'
import { ProtectedRoute } from '@/components/protected-route'
import { AdminRoute } from '@/components/admin-route'
import { AgentsRoute } from '@/components/agents-route'
import { ModuleRoute } from '@/components/module-route'
import { AppLayout } from '@/components/app-layout'

const SetupPage = lazy(() => import('@/pages/setup'))
const LoginPage = lazy(() => import('@/pages/login'))
const RegisterPage = lazy(() => import('@/pages/register'))
const DashboardPage = lazy(() => import('@/pages/dashboard'))
const TransactionsPage = lazy(() => import('@/pages/transactions'))
const AccountsPage = lazy(() => import('@/pages/accounts'))
const AccountDetailPage = lazy(() => import('@/pages/account-detail'))
const ImportPage = lazy(() => import('@/pages/import'))
const RulesPage = lazy(() => import('@/pages/rules'))
const CategoriesPage = lazy(() => import('@/pages/categories'))
const CollectionsPage = lazy(() => import('@/pages/collections'))
const BudgetsPage = lazy(() => import('@/pages/budgets'))
const RecurringPage = lazy(() => import('@/pages/recurring'))
const GoalsPage = lazy(() => import('@/pages/goals'))
const AssetsPage = lazy(() => import('@/pages/assets'))
const ReportsPage = lazy(() => import('@/pages/reports'))
const PayeesPage = lazy(() => import('@/pages/payees'))
const GroupsPage = lazy(() => import('@/pages/groups'))
const GroupDetailPage = lazy(() => import('@/pages/group-detail'))
const AdminSettingsPage = lazy(() => import('@/pages/admin/settings'))
const AgentsListPage = lazy(() => import('@/pages/agents-list'))
const AgentDetailPage = lazy(() => import('@/pages/agent-detail'))
const AgentConnectionsPage = lazy(() => import('@/pages/agent-connections'))
const InvoicesPage = lazy(() => import('@/pages/invoices'))
const InvoiceDetailPage = lazy(() => import('@/pages/invoice-detail'))
const SharedInvoicePage = lazy(() => import('@/pages/shared-invoice'))
const WorkspaceSettingsPage = lazy(() => import('@/pages/workspace-settings'))
const OAuthCallbackPage = lazy(() => import('@/pages/oauth-callback'))
const OIDCCallbackPage = lazy(() => import('@/pages/oidc-callback'))

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5,
      retry: 1,
    },
  },
})

function LoadingFallback() {
  return (
    <div className="flex items-center justify-center min-h-[50vh]">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
    </div>
  )
}

function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
        <BrowserRouter>
          <AuthProvider>
            <WorkspaceProvider>
            <Suspense fallback={<LoadingFallback />}>
              <Routes>
                <Route path="/setup" element={<SetupPage />} />
                <Route path="/login" element={<LoginPage />} />
                <Route path="/auth/oidc/callback" element={<OIDCCallbackPage />} />
                <Route path="/register" element={<RegisterPage />} />
                {/* A client opening a link the sender shared. Deliberately
                    outside ProtectedRoute and outside AppLayout: the
                    recipient has no account, and the token is the whole
                    credential. */}
                <Route path="/i/:token" element={<SharedInvoicePage />} />
                <Route
                  element={
                    <ProtectedRoute>
                      <CollectionFilterProvider>
                        <AppLayout />
                      </CollectionFilterProvider>
                    </ProtectedRoute>
                  }
                >
                  <Route path="/" element={<DashboardPage />} />
                  <Route path="/transactions" element={<ModuleRoute module="transactions"><TransactionsPage /></ModuleRoute>} />
                  <Route path="/accounts" element={<ModuleRoute module="accounts"><AccountsPage /></ModuleRoute>} />
                  <Route path="/accounts/:id" element={<ModuleRoute module="accounts"><AccountDetailPage /></ModuleRoute>} />
                  <Route path="/oauth/callback" element={<OAuthCallbackPage />} />
                  <Route path="/enable-banking" element={<OAuthCallbackPage />} />
                  <Route path="/import" element={<ModuleRoute module="import"><ImportPage /></ModuleRoute>} />
                  <Route path="/rules" element={<ModuleRoute module="rules"><RulesPage /></ModuleRoute>} />
                  <Route path="/categories" element={<ModuleRoute module="categories"><CategoriesPage /></ModuleRoute>} />
                  <Route path="/collections" element={<CollectionsPage />} />
                  <Route path="/budgets" element={<ModuleRoute module="budgets"><BudgetsPage /></ModuleRoute>} />
                  <Route path="/goals" element={<ModuleRoute module="goals"><GoalsPage /></ModuleRoute>} />
                  <Route path="/recurring" element={<ModuleRoute module="recurring"><RecurringPage /></ModuleRoute>} />
                  <Route path="/assets" element={<ModuleRoute module="assets"><AssetsPage /></ModuleRoute>} />
                  {/* Kept so links minted before the importers were merged keep working. */}
                  <Route path="/assets/import" element={<Navigate to="/import?tab=investments" replace />} />
                  <Route path="/reports" element={<ModuleRoute module="reports"><ReportsPage /></ModuleRoute>} />
                  <Route path="/payees" element={<ModuleRoute module="payees"><PayeesPage /></ModuleRoute>} />
                  <Route path="/groups" element={<ModuleRoute module="split_groups"><GroupsPage /></ModuleRoute>} />
                  <Route path="/groups/:id" element={<ModuleRoute module="split_groups"><GroupDetailPage /></ModuleRoute>} />
                  <Route path="/invoices" element={<ModuleRoute module="invoices"><InvoicesPage /></ModuleRoute>} />
                  <Route path="/invoices/:id" element={<ModuleRoute module="invoices"><InvoiceDetailPage /></ModuleRoute>} />
                  <Route path="/workspace/settings" element={<WorkspaceSettingsPage />} />
                  <Route path="/admin" element={<AdminRoute><AdminSettingsPage /></AdminRoute>} />
                  <Route path="/agents" element={<AgentsRoute><AgentsListPage /></AgentsRoute>} />
                  <Route path="/agents/connections" element={<AgentsRoute><AgentConnectionsPage /></AgentsRoute>} />
                  <Route path="/agents/:id" element={<AgentsRoute><AgentDetailPage /></AgentsRoute>} />
                </Route>
              </Routes>
            </Suspense>
            <Toaster />
            </WorkspaceProvider>
          </AuthProvider>
        </BrowserRouter>
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  )
}

export default App
