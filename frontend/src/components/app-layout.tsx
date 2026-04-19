import { useState, useCallback } from 'react'
import { getAccountName } from '@/lib/account-utils'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@/contexts/auth-context'
import { auth as authApi, backup as backupApi } from '@/lib/api'
import { toast } from 'sonner'
import { OnboardingTour } from '@/components/onboarding-tour'
import { useTheme } from 'next-themes'
import { accounts as accountsApi } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuPortal,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import { ShellLogo } from '@/components/shell-logo'
import {
  LayoutDashboard,
  ArrowLeftRight,
  Building2,
  SlidersHorizontal,
  Upload,
  LogOut,
  Menu,
  ChevronRight,
  Tag,
  PiggyBank,
  Target,
  Eye,
  EyeOff,
  Repeat,
  Landmark,
  Users,
  BarChart3,
  Sun,
  Moon,
  Languages,
  KeyRound,
  Check,
  HardDriveDownload,
  Shield,
  ShieldCheck,
} from 'lucide-react'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { ChangePasswordDialog } from '@/components/change-password-dialog'
import { TwoFactorSetup } from '@/components/two-factor-setup'
import { CommandPalette } from '@/components/command-palette'
import { useCommandPaletteHotkey } from '@/hooks/use-command-palette-hotkey'
import { Search } from 'lucide-react'

type NavItem =
  | { type: 'link'; key: string; path: string; icon: React.ElementType }
  | { type: 'separator'; labelKey: string }

const navItems: NavItem[] = [
  { type: 'link', key: 'dashboard',    path: '/',             icon: LayoutDashboard },
  { type: 'link', key: 'transactions', path: '/transactions', icon: ArrowLeftRight },
  { type: 'separator', labelKey: 'nav.groupAccounts' },
  { type: 'link', key: 'accounts',     path: '/accounts',     icon: Building2 },
  { type: 'link', key: 'import',       path: '/import',       icon: Upload },
  { type: 'separator', labelKey: 'nav.groupAnalysis' },
  { type: 'link', key: 'reports',      path: '/reports',      icon: BarChart3 },
  { type: 'link', key: 'assets',       path: '/assets',       icon: Landmark },
  { type: 'separator', labelKey: 'nav.groupSetup' },
  { type: 'link', key: 'budgets',      path: '/budgets',      icon: PiggyBank },
  { type: 'link', key: 'goals',        path: '/goals',        icon: Target },
  { type: 'link', key: 'recurring',    path: '/recurring',    icon: Repeat },
  { type: 'link', key: 'categories',   path: '/categories',   icon: Tag },
  { type: 'link', key: 'payees',      path: '/payees',       icon: Users },
  { type: 'link', key: 'rules',        path: '/rules',        icon: SlidersHorizontal },
]

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

export function AppLayout() {
  const { t, i18n } = useTranslation()
  const { user, logout, updateUser } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language
  const { theme, setTheme } = useTheme()
  const location = useLocation()
  const navigate = useNavigate()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [accountsExpanded, setAccountsExpanded] = useState(true)
  const [accountsShowAll, setAccountsShowAll] = useState(false)
  const { privacyMode, togglePrivacyMode, mask } = usePrivacyMode()
  const [changePasswordOpen, setChangePasswordOpen] = useState(false)
  const [twoFactorOpen, setTwoFactorOpen] = useState(false)
  const [backingUp, setBackingUp] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  useCommandPaletteHotkey(setPaletteOpen)
  const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad|iPod/.test(navigator.platform)

  const showTour = user && !user.preferences?.onboarding_completed && !localStorage.getItem('onboarding_completed')

  const handleTourComplete = useCallback(async () => {
    localStorage.setItem('onboarding_completed', 'true')
    try {
      const prefs = { ...(user?.preferences || {}), onboarding_completed: true }
      const updated = await authApi.updateMe({ preferences: prefs })
      updateUser(updated)
    } catch {
      // localStorage fallback is already set
    }
  }, [user, updateUser])

  const userInitial = user?.email?.charAt(0).toUpperCase() ?? '?'
  const currentLang = i18n.language
  const resolvedTheme = theme === 'system' ? undefined : theme
  const isDark = resolvedTheme
    ? resolvedTheme === 'dark'
    : typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-color-scheme: dark)').matches
  const toggleTheme = () => setTheme(isDark ? 'light' : 'dark')

  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })

  const allAccounts = accountsList ?? []
  const totalBalance = allAccounts.reduce((sum, a) => {
    return sum + Number(a.balance_primary ?? a.current_balance)
  }, 0)

  return (
    <div className="min-h-screen bg-background">
      {/* Mobile header */}
      <header className="sticky top-0 z-40 flex h-14 items-center gap-3 bg-sidebar border-b border-sidebar-border px-4 lg:hidden">
        <button
          onClick={() => setSidebarOpen(!sidebarOpen)}
          className="text-sidebar-muted hover:text-sidebar-foreground transition-colors"
          aria-label="Toggle menu"
        >
          <Menu size={20} />
        </button>
        <div className="flex items-center gap-2">
          <ShellLogo size={22} className="text-primary shrink-0" />
          <span className="font-bold text-sidebar-foreground">{t('app.name')}</span>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setPaletteOpen(true)}
            className="text-sidebar-muted hover:text-sidebar-foreground transition-colors p-1"
            title={t('cmdk.triggerAria')}
            aria-label={t('cmdk.triggerAria')}
          >
            <Search size={18} />
          </button>
          <button
            onClick={togglePrivacyMode}
            className="text-sidebar-muted hover:text-sidebar-foreground transition-colors p-1"
            title={privacyMode ? t('privacy.show') : t('privacy.hide')}
          >
            {privacyMode ? <EyeOff size={18} /> : <Eye size={18} />}
          </button>
          <button
            onClick={toggleTheme}
            className="text-sidebar-muted hover:text-sidebar-foreground transition-colors p-1"
            title={isDark ? t('settings.themeLight') : t('settings.themeDark')}
            aria-label={isDark ? t('settings.themeLight') : t('settings.themeDark')}
          >
            {isDark ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          <UserMenu userInitial={userInitial} logout={logout} onChangePassword={() => setChangePasswordOpen(true)} onTwoFactor={() => setTwoFactorOpen(true)} backingUp={backingUp} onBackup={async () => {
                    setBackingUp(true)
                    try {
                      await backupApi.download()
                      toast.success(t('backup.success'))
                    } catch {
                      toast.error(t('backup.error'))
                    } finally {
                      setBackingUp(false)
                    }
                  }} dark isAdmin={user?.is_superuser} />
        </div>
      </header>

      <div className="flex">
        {/* Sidebar overlay for mobile */}
        {sidebarOpen && (
          <div
            className="fixed inset-0 z-40 bg-black/50 lg:hidden"
            onClick={() => setSidebarOpen(false)}
          />
        )}

        {/* Sidebar */}
        <aside
          className={cn(
            'fixed inset-y-0 left-0 z-50 w-60 bg-sidebar border-r border-sidebar-border flex flex-col transform transition-transform lg:translate-x-0 shrink-0 overflow-y-auto',
            sidebarOpen ? 'translate-x-0' : '-translate-x-full'
          )}
        >
          {/* Logo */}
          <div className="flex h-16 min-h-16 items-center justify-between px-5 border-b border-sidebar-border shrink-0">
            <div className="flex items-center gap-2.5">
              <ShellLogo size={24} className="text-primary shrink-0" />
              <span className="font-bold text-lg text-sidebar-foreground tracking-tight">{t('app.name')}</span>
            </div>
            <div className="flex items-center gap-0.5">
              <button
                onClick={togglePrivacyMode}
                className="text-sidebar-muted hover:text-sidebar-foreground transition-colors p-1 rounded-md hover:bg-sidebar-accent"
                title={privacyMode ? t('privacy.show') : t('privacy.hide')}
                aria-label={privacyMode ? t('privacy.show') : t('privacy.hide')}
              >
                {privacyMode ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
              <button
                onClick={toggleTheme}
                className="text-sidebar-muted hover:text-sidebar-foreground transition-colors p-1 rounded-md hover:bg-sidebar-accent"
                title={isDark ? t('settings.themeLight') : t('settings.themeDark')}
                aria-label={isDark ? t('settings.themeLight') : t('settings.themeDark')}
              >
                {isDark ? <Sun size={16} /> : <Moon size={16} />}
              </button>
            </div>
          </div>

          {/* Command palette trigger */}
          <div className="px-3 pt-3">
            <button
              type="button"
              onClick={() => setPaletteOpen(true)}
              className={cn(
                'group flex w-full items-center gap-2 rounded-lg border border-sidebar-border/80 bg-sidebar-accent/40 px-3 py-2',
                'text-[12.5px] text-sidebar-muted transition-all',
                'hover:bg-sidebar-accent hover:text-sidebar-foreground hover:border-sidebar-border'
              )}
              aria-label={t('cmdk.triggerAria')}
            >
              <Search size={13} className="shrink-0" />
              <span className="flex-1 text-left">{t('cmdk.triggerLabel')}</span>
              <kbd className="hidden lg:inline-flex h-[17px] items-center rounded border border-sidebar-border bg-sidebar px-1 font-mono text-[9.5px] font-semibold text-sidebar-muted/80">
                {isMac ? '⌘' : 'Ctrl'}&nbsp;K
              </kbd>
            </button>
          </div>

          {/* Nav */}
          <nav className="flex flex-col gap-0.5 p-3" data-tour="sidebar">
            {navItems.map((item, idx) => {
              if (item.type === 'separator') {
                return (
                  <div key={`sep-${idx}`} className="pt-3 pb-1 px-3">
                    <span className="text-[10px] uppercase tracking-[0.12em] font-semibold text-sidebar-muted/50">
                      {t(item.labelKey)}
                    </span>
                  </div>
                )
              }

              const isActive = item.path === '/'
                ? location.pathname === '/'
                : location.pathname.startsWith(item.path)
              const Icon = item.icon
              return (
                <Link
                  key={item.key}
                  to={item.path}
                  data-tour={`nav-${item.key}`}
                  onClick={() => setSidebarOpen(false)}
                  className={cn(
                    'flex items-center gap-3 text-[13px] font-medium transition-all rounded-lg px-3 py-2',
                    isActive
                      ? 'bg-primary/[0.08] text-primary border-l-[3px] border-primary pl-[9px]'
                      : 'text-sidebar-muted hover:bg-sidebar-accent hover:text-sidebar-foreground'
                  )}
                >
                  <Icon
                    size={17}
                    className={cn('shrink-0', isActive ? 'text-primary' : 'text-sidebar-muted')}
                  />
                  <span>{t(`nav.${item.key}`)}</span>
                </Link>
              )
            })}
          </nav>

          {/* Account list in sidebar */}
          {allAccounts.length > 0 && (
            <div className="px-3 pb-2 mt-2">
              <button
                onClick={() => setAccountsExpanded(!accountsExpanded)}
                className="flex items-center justify-between w-full px-3 py-2 hover:text-sidebar-foreground transition-colors"
              >
                <span className="text-[11px] uppercase tracking-[0.12em] font-semibold text-sidebar-muted">{t('accounts.title')}</span>
                <div className="flex items-center gap-2">
                  <span className={`tabular-nums font-medium text-xs ${totalBalance < 0 ? 'text-rose-400' : 'text-sidebar-muted'}`}>
                    {mask(formatCurrency(totalBalance, userCurrency, locale))}
                  </span>
                  <ChevronRight
                    size={12}
                    className={cn('text-sidebar-muted transition-transform', accountsExpanded && 'rotate-90')}
                  />
                </div>
              </button>
              {accountsExpanded && (
                <div className="mt-1 space-y-0.5">
                  {[...allAccounts].sort((a, b) => Math.abs(Number(b.current_balance)) - Math.abs(Number(a.current_balance))).slice(0, accountsShowAll ? allAccounts.length : 3).map((acc) => {
                    const balance = Number(acc.current_balance)
                    const prevBalance = acc.previous_balance ?? 0
                    const pctChange = prevBalance !== 0
                      ? ((balance - prevBalance) / Math.abs(prevBalance)) * 100
                      : null
                    const typeKey = acc.type.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase()).replace(/^./, c => c.toUpperCase())

                    return (
                      <Link
                        key={acc.id}
                        to={`/accounts/${acc.id}`}
                        onClick={() => setSidebarOpen(false)}
                        className="flex items-center justify-between px-3 py-1.5 rounded-lg text-xs text-sidebar-muted hover:bg-sidebar-accent hover:text-sidebar-foreground transition-all"
                      >
                        <div className="truncate min-w-0">
                          <span className="block truncate font-medium">{getAccountName(acc)}</span>
                          <span className="block text-[10px] text-sidebar-muted/60">
                            {t(`accounts.type${typeKey}`)}
                          </span>
                        </div>
                        <div className="text-right shrink-0 ml-2">
                          <span className={`block tabular-nums font-medium text-xs ${balance < 0 ? 'text-rose-400' : 'text-sidebar-foreground'}`}>
                            {mask(formatCurrency(balance, acc.currency))}
                          </span>
                          {pctChange !== null && (
                            <span className={`block text-[10px] tabular-nums font-medium ${pctChange >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                              {mask(`${pctChange >= 0 ? '+' : ''}${pctChange.toFixed(1)}%`)}
                            </span>
                          )}
                        </div>
                      </Link>
                    )
                  })}
                  {allAccounts.length > 3 && (
                    <button
                      onClick={() => setAccountsShowAll(!accountsShowAll)}
                      className="w-full px-3 py-1.5 text-[11px] font-medium text-sidebar-muted/70 hover:text-sidebar-foreground transition-colors text-center"
                    >
                      {accountsShowAll
                        ? t('common.showLess', { defaultValue: 'Show less' })
                        : t('common.showMore', { count: allAccounts.length - 3, defaultValue: `+${allAccounts.length - 3} more` })}
                    </button>
                  )}
                </div>
              )}
            </div>
          )}

          <div className="flex-1" />

          {/* User section */}
          <div className="p-3">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button className="flex items-center gap-3 w-full rounded-lg px-3 py-2.5 text-sm hover:bg-sidebar-accent transition-colors text-left">
                  <Avatar className="h-7 w-7 shrink-0">
                    <AvatarFallback className="bg-primary/20 text-primary text-xs font-semibold">
                      {userInitial}
                    </AvatarFallback>
                  </Avatar>
                  <span className="text-xs text-sidebar-muted truncate flex-1">{user?.email}</span>
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-48" side="top">
                {user?.is_superuser && (
                  <>
                    <DropdownMenuItem
                      onClick={() => navigate('/admin')}
                      className="flex items-center gap-2"
                    >
                      <Shield size={14} />
                      {t('nav.groupAdmin')}
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                  </>
                )}
                <DropdownMenuItem
                  onClick={() => setChangePasswordOpen(true)}
                  className="flex items-center gap-2"
                >
                  <KeyRound size={14} />
                  {t('auth.changePassword')}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => setTwoFactorOpen(true)}
                  className="flex items-center gap-2"
                >
                  <ShieldCheck size={14} />
                  {t('auth.twoFactorTitle')}
                </DropdownMenuItem>
                <DropdownMenuItem
                  disabled={backingUp}
                  onClick={async () => {
                    setBackingUp(true)
                    try {
                      await backupApi.download()
                      toast.success(t('backup.success'))
                    } catch {
                      toast.error(t('backup.error'))
                    } finally {
                      setBackingUp(false)
                    }
                  }}
                  className="flex items-center gap-2"
                >
                  <HardDriveDownload size={14} />
                  {backingUp ? t('backup.downloading') : t('backup.button')}
                </DropdownMenuItem>
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger className="flex items-center gap-2">
                    <Languages size={14} />
                    <span className="flex-1">{t('setup.language')}</span>
                    <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {currentLang === 'pt-BR' ? 'PT' : 'EN'}
                    </span>
                  </DropdownMenuSubTrigger>
                  <DropdownMenuPortal>
                    <DropdownMenuSubContent className="w-40">
                      <DropdownMenuLabel className="px-2 py-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/70">
                        {t('setup.language')}
                      </DropdownMenuLabel>
                      <DropdownMenuItem
                        onClick={() => i18n.changeLanguage('pt-BR')}
                        className="flex items-center gap-2"
                      >
                        <span className="flex-1">Português</span>
                        {currentLang === 'pt-BR' && (
                          <Check size={13} className="text-primary" />
                        )}
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onClick={() => i18n.changeLanguage('en')}
                        className="flex items-center gap-2"
                      >
                        <span className="flex-1">English</span>
                        {currentLang === 'en' && (
                          <Check size={13} className="text-primary" />
                        )}
                      </DropdownMenuItem>
                    </DropdownMenuSubContent>
                  </DropdownMenuPortal>
                </DropdownMenuSub>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onClick={logout}
                  className="flex items-center gap-2 text-rose-600 focus:text-rose-600"
                >
                  <LogOut size={14} />
                  {t('auth.logout')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </aside>

        {/* Main content */}
        <main className="flex-1 min-h-screen overflow-x-hidden lg:ml-60">
          <div className="p-6 max-w-7xl mx-auto">
            <Outlet />
          </div>
        </main>
      </div>

      {showTour && <OnboardingTour onComplete={handleTourComplete} />}
      <ChangePasswordDialog open={changePasswordOpen} onClose={() => setChangePasswordOpen(false)} />
      <TwoFactorSetup open={twoFactorOpen} onClose={() => setTwoFactorOpen(false)} />
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  )
}

function UserMenu({ userInitial, logout, onChangePassword, onTwoFactor, onBackup, backingUp, dark, isAdmin }: { userInitial: string; logout: () => void; onChangePassword: () => void; onTwoFactor: () => void; onBackup: () => void; backingUp: boolean; dark?: boolean; isAdmin?: boolean }) {
  const { t, i18n } = useTranslation()
  const nav = useNavigate()
  const currentLang = i18n.language
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="relative h-8 w-8 rounded-full p-0">
          <Avatar className="h-8 w-8">
            <AvatarFallback className={dark ? 'bg-primary/20 text-primary text-xs font-semibold' : 'bg-primary/10 text-primary text-xs font-semibold'}>
              {userInitial}
            </AvatarFallback>
          </Avatar>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {isAdmin && (
          <>
            <DropdownMenuItem onClick={() => nav('/admin')} className="flex items-center gap-2">
              <Shield size={14} />
              {t('nav.groupAdmin')}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
          </>
        )}
        <DropdownMenuItem onClick={onChangePassword} className="flex items-center gap-2">
          <KeyRound size={14} />
          {t('auth.changePassword')}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={onTwoFactor} className="flex items-center gap-2">
          <ShieldCheck size={14} />
          {t('auth.twoFactorTitle')}
        </DropdownMenuItem>
        <DropdownMenuItem disabled={backingUp} onClick={onBackup} className="flex items-center gap-2">
          <HardDriveDownload size={14} />
          {backingUp ? t('backup.downloading') : t('backup.button')}
        </DropdownMenuItem>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger className="flex items-center gap-2">
            <Languages size={14} />
            <span className="flex-1">{t('setup.language')}</span>
            <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              {currentLang === 'pt-BR' ? 'PT' : 'EN'}
            </span>
          </DropdownMenuSubTrigger>
          <DropdownMenuPortal>
            <DropdownMenuSubContent className="w-40">
              <DropdownMenuLabel className="px-2 py-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/70">
                {t('setup.language')}
              </DropdownMenuLabel>
              <DropdownMenuItem
                onClick={() => i18n.changeLanguage('pt-BR')}
                className="flex items-center gap-2"
              >
                <span className="flex-1">Português</span>
                {currentLang === 'pt-BR' && (
                  <Check size={13} className="text-primary" />
                )}
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => i18n.changeLanguage('en')}
                className="flex items-center gap-2"
              >
                <span className="flex-1">English</span>
                {currentLang === 'en' && (
                  <Check size={13} className="text-primary" />
                )}
              </DropdownMenuItem>
            </DropdownMenuSubContent>
          </DropdownMenuPortal>
        </DropdownMenuSub>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={logout} className="text-rose-600 focus:text-rose-600">
          {t('auth.logout')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
