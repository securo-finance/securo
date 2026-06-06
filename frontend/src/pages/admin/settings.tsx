import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useTheme } from 'next-themes'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { admin as adminApi, currencies as currenciesApi } from '@/lib/api'
import { resolveDisplayLocale, resolveDateLocale, type NumberFormat, type DateFormat } from '@/lib/format'
import { resolveSupportedLang } from '@/lib/i18n'
import { useAuth } from '@/contexts/auth-context'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { PageHeader } from '@/components/page-header'
import { setThemeBasedOnSystem } from '@/lib/theme-utils'
import { Search, Plus, Trash2, Shield, ShieldOff, UserCog, Users, Scale, Tag, Palette, Save, Hash, CalendarDays } from 'lucide-react'
import type { AdminUser } from '@/types'

export default function AdminSettingsPage() {
  const { t, i18n } = useTranslation()
  const { user: currentUser } = useAuth()
  const queryClient = useQueryClient()
  const { resolvedTheme } = useTheme()

  const [search, setSearch] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [editUser, setEditUser] = useState<AdminUser | null>(null)
  const [deleteUser, setDeleteUser] = useState<AdminUser | null>(null)

  const [formEmail, setFormEmail] = useState('')
  const [formPassword, setFormPassword] = useState('')
  const [formIsAdmin, setFormIsAdmin] = useState(false)
  const [formLanguage, setFormLanguage] = useState('en')
  const [formCurrency, setFormCurrency] = useState('USD')

  const [editEmail, setEditEmail] = useState('')
  const [editIsActive, setEditIsActive] = useState(true)
  const [editIsAdmin, setEditIsAdmin] = useState(false)
  const [editPassword, setEditPassword] = useState('')
  const [showPasswordField, setShowPasswordField] = useState(false)

  const [lastSyncedLight, setLastSyncedLight] = useState<string | undefined>()
  const [lastSyncedDark, setLastSyncedDark] = useState<string | undefined>()
  const [localLight, setLocalLight] = useState<string>('#6366F1')
  const [localDark, setLocalDark] = useState<string>('#818CF8')

  const { data: usersData, isLoading: usersLoading } = useQuery({
    queryKey: ['admin', 'users', search],
    queryFn: () => adminApi.listUsers({ search: search || undefined }),
  })

  const createMutation = useMutation({
    mutationFn: (data: { email: string; password: string; is_superuser: boolean; preferences: Record<string, unknown> }) =>
      adminApi.createUser(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] })
      setCreateOpen(false)
      resetCreateForm()
      toast.success(t('admin.users.created'))
    },
    onError: (err: { response?: { data?: { detail?: string } } }) => {
      toast.error(err.response?.data?.detail || t('common.error'))
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: { id: string } & Record<string, unknown>) =>
      adminApi.updateUser(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] })
      setEditUser(null)
      toast.success(t('admin.users.updated'))
    },
    onError: (err: { response?: { data?: { detail?: string } } }) => {
      toast.error(err.response?.data?.detail || t('common.error'))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => adminApi.deleteUser(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] })
      setDeleteUser(null)
      toast.success(t('admin.users.deleted'))
    },
    onError: (err: { response?: { data?: { detail?: string } } }) => {
      toast.error(err.response?.data?.detail || t('common.error'))
    },
  })

  const { data: regSetting, isLoading: settingsLoading } = useQuery({
    queryKey: ['admin', 'settings', 'registration_enabled'],
    queryFn: () => adminApi.getSetting('registration_enabled'),
  })

  const updateSettingMutation = useMutation({
    mutationFn: (value: string) => adminApi.updateSetting('registration_enabled', value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'settings'] })
      toast.success(t('admin.settings.updated'))
    },
    onError: () => {
      toast.error(t('common.error'))
    },
  })

  // Theme color settings
  const { data: themeColorLightSetting } = useQuery({
    queryKey: ['admin', 'settings', 'theme_color_light'],
    queryFn: () => adminApi.getSetting('theme_color_light').catch(() => null),
    retry: false,
  })

  const { data: themeColorDarkSetting } = useQuery({
    queryKey: ['admin', 'settings', 'theme_color_dark'],
    queryFn: () => adminApi.getSetting('theme_color_dark').catch(() => null),
    retry: false,
  })

  // Credit card accounting mode: returns 404 when unset → defaults to "cash".
  const { data: ccModeSetting } = useQuery({
    queryKey: ['admin', 'settings', 'credit_card_accounting_mode'],
    queryFn: () => adminApi.getSetting('credit_card_accounting_mode').catch(() => null),
    retry: false,
  })

  const updateAccountingModeMutation = useMutation({
    mutationFn: (value: string) => adminApi.updateSetting('credit_card_accounting_mode', value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'settings', 'credit_card_accounting_mode'] })
      toast.success(t('admin.settings.updated'))
    },
    onError: () => {
      toast.error(t('common.error'))
    },
  })

  const accountingMode = (ccModeSetting?.value === 'accrual' ? 'accrual' : 'cash') as 'cash' | 'accrual'

  // Provider categories: returns 404 when unset → defaults to "true" so
  // existing installs keep the historical sync behavior.
  const { data: providerCatsSetting } = useQuery({
    queryKey: ['admin', 'settings', 'use_provider_categories'],
    queryFn: () => adminApi.getSetting('use_provider_categories').catch(() => null),
    retry: false,
  })

  const { data: supportedCurrencies } = useQuery({
    queryKey: ['currencies'],
    queryFn: currenciesApi.list,
    staleTime: Infinity,
  })

  const updateProviderCatsMutation = useMutation({
    mutationFn: (value: string) => adminApi.updateSetting('use_provider_categories', value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'settings', 'use_provider_categories'] })
      toast.success(t('admin.settings.updated'))
    },
    onError: () => {
      toast.error(t('common.error'))
    },
  })

  const useProviderCats = providerCatsSetting?.value !== 'false'

  // Number/date display format: 404 when unset → defaults to "auto" (derive
  // separators from each user's display currency).
  const { data: numberFormatSetting } = useQuery({
    queryKey: ['admin', 'settings', 'number_format'],
    queryFn: () => adminApi.getSetting('number_format').catch(() => null),
    retry: false,
  })

  const numberFormat = numberFormatSetting?.value ?? 'auto'

  const updateNumberFormatMutation = useMutation({
    mutationFn: (value: string) => adminApi.updateSetting('number_format', value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'settings', 'number_format'] })
      queryClient.invalidateQueries({ queryKey: ['admin', 'number-format'] })
      toast.success(t('admin.settings.updated'))
    },
    onError: () => {
      toast.error(t('common.error'))
    },
  })

  // Date format: 404 when unset → defaults to "auto" (order follows the
  // number format / currency).
  const { data: dateFormatSetting } = useQuery({
    queryKey: ['admin', 'settings', 'date_format'],
    queryFn: () => adminApi.getSetting('date_format').catch(() => null),
    retry: false,
  })

  const dateFormat = dateFormatSetting?.value ?? 'auto'

  const updateDateFormatMutation = useMutation({
    mutationFn: (value: string) => adminApi.updateSetting('date_format', value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'settings', 'date_format'] })
      queryClient.invalidateQueries({ queryKey: ['admin', 'date-format'] })
      toast.success(t('admin.settings.updated'))
    },
    onError: () => {
      toast.error(t('common.error'))
    },
  })

  if (themeColorLightSetting?.value && themeColorLightSetting.value !== lastSyncedLight) {
    setLastSyncedLight(themeColorLightSetting.value)
    setLocalLight(themeColorLightSetting.value)
  }
  if (themeColorDarkSetting?.value && themeColorDarkSetting.value !== lastSyncedDark) {
    setLastSyncedDark(themeColorDarkSetting.value)
    setLocalDark(themeColorDarkSetting.value)
  }

  const saveColorsMutation = useMutation({
    mutationFn: async () => {
      await Promise.all([
        adminApi.updateSetting('theme_color_light', localLight),
        adminApi.updateSetting('theme_color_dark', localDark),
      ])
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'settings'] })
      setThemeBasedOnSystem(localLight, localDark, resolvedTheme)
      toast.success(t('admin.settings.updated'))
    },
    onError: () => {
      toast.error(t('common.error'))
    },
  })

  function resetCreateForm() {
    setFormEmail('')
    setFormPassword('')
    setFormIsAdmin(false)
    setFormLanguage('en')
    // New users default to the admin's current display currency so they
    // follow the currency the workspace is running in.
    setFormCurrency(currentUser?.preferences?.currency_display ?? 'USD')
  }

  function openEdit(u: AdminUser) {
    setEditUser(u)
    setEditEmail(u.email)
    setEditIsActive(u.is_active)
    setEditIsAdmin(u.is_superuser)
    setEditPassword('')
    setShowPasswordField(false)
  }

  const users = usersData?.items ?? []
  const isSelf = (u: AdminUser) => u.id === currentUser?.id
  const isEnabled = regSetting?.value === 'true'

  const filteredUsers = users

  return (
    <div>
      <PageHeader
        section={t('nav.groupAdmin')}
        title={t('admin.settings.title')}
        action={
          <Button onClick={() => { resetCreateForm(); setCreateOpen(true) }}>
            <Plus size={16} className="mr-1.5" />
            {t('admin.users.add')}
          </Button>
        }
      />

      {/* Search */}
      <div className="relative max-w-md mb-5">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t('admin.users.searchPlaceholder')}
          className="pl-9 h-10 bg-card border-border/60 rounded-xl"
        />
      </div>

      {/* User list */}
      <div className="rounded-xl border border-border/60 bg-card overflow-hidden mb-8">
        {usersLoading ? (
          <div className="p-4 space-y-3">
            {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-14 w-full rounded-lg" />)}
          </div>
        ) : filteredUsers.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
            <Users size={32} className="mb-3 opacity-40" />
            <p className="text-sm">{t('admin.users.empty')}</p>
          </div>
        ) : (
          <div className="divide-y divide-border/40">
            {filteredUsers.map((u) => (
              <button
                key={u.id}
                onClick={() => openEdit(u)}
                className="flex items-center gap-4 w-full px-5 py-3.5 text-left hover:bg-muted/40 transition-colors"
              >
                <Avatar className="h-9 w-9 shrink-0">
                  <AvatarFallback className={u.is_superuser ? 'bg-primary/15 text-primary text-xs font-semibold' : 'bg-muted text-muted-foreground text-xs font-semibold'}>
                    {u.email.charAt(0).toUpperCase()}
                  </AvatarFallback>
                </Avatar>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-foreground truncate">{u.email}</span>
                    {isSelf(u) && (
                      <span className="text-[10px] font-medium text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                        {t('admin.users.you')}
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {u.is_superuser ? t('admin.users.admin') : t('admin.users.user')}
                  </span>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  {!u.is_active && (
                    <Badge variant="secondary" className="text-[10px] font-medium">
                      {t('admin.users.inactive')}
                    </Badge>
                  )}
                  {u.is_superuser && (
                    <Shield size={14} className="text-primary" />
                  )}
                  {!isSelf(u) && (
                    <span
                      role="button"
                      onClick={(e) => { e.stopPropagation(); setDeleteUser(u) }}
                      className="p-1.5 rounded-md text-muted-foreground/40 hover:text-destructive hover:bg-destructive/10 transition-colors"
                    >
                      <Trash2 size={14} />
                    </span>
                  )}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Theme and Customization Section */}
      <div className="grid grid-cols-1 gap-6 mb-8">
        <div className="rounded-xl border border-border/60 bg-card overflow-hidden">
          <div className="px-5 py-4 border-b border-border/40 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Palette size={15} className="text-muted-foreground" />
              <h3 className="text-sm font-semibold text-foreground">{t('settings.customization')}</h3>
            </div>
            <Button
              onClick={() => saveColorsMutation.mutate()}
              disabled={saveColorsMutation.isPending}
            >
              <Save size={13} />
              {saveColorsMutation.isPending ? t('common.loading') : t('common.save')}
            </Button>
          </div>
          <div className="p-5 grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Light Mode Colors */}
            <div className="space-y-3">
              <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                {t('settings.lightMode')}
              </p>
              <div className="space-y-1.5">
                <Label className="text-xs">{t('settings.themeColor')}</Label>
                <div className="flex items-center gap-2">
                  <Input 
                    type="color" 
                    className="w-12 h-9 p-1 cursor-pointer" 
                    value={localLight}
                    onChange={(e) => setLocalLight(e.target.value)}
                  />
                  <span className="text-xs font-mono text-muted-foreground">{localLight}</span>
                </div>
              </div>
            </div>

            {/* Dark Mode Colors */}
            <div className="space-y-3">
              <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                {t('settings.darkMode')}
              </p>
              <div className="space-y-1.5">
                <Label className="text-xs">{t('settings.themeColor')}</Label>
                <div className="flex items-center gap-2">
                  <Input 
                    type="color" 
                    className="w-12 h-9 p-1 cursor-pointer" 
                    value={localDark}
                    onChange={(e) => setLocalDark(e.target.value)}
                  />
                  <span className="text-xs font-mono text-muted-foreground">{localDark}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Accounting section */}
      <div className="rounded-xl border border-border/60 bg-card overflow-hidden mb-8">
        <div className="px-5 py-4 border-b border-border/40">
          <div className="flex items-center gap-2 mb-0.5">
            <Scale size={15} className="text-muted-foreground" />
            <h3 className="text-sm font-semibold text-foreground">{t('admin.settings.accountingTitle')}</h3>
          </div>
          <p className="text-xs text-muted-foreground">{t('admin.settings.accountingSubtitle')}</p>
        </div>
        <div className="divide-y divide-border/40">
          <button
            type="button"
            onClick={() => updateAccountingModeMutation.mutate('cash')}
            disabled={updateAccountingModeMutation.isPending}
            className="flex items-start gap-3 w-full px-5 py-4 text-left hover:bg-muted/40 transition-colors"
          >
            <div className={`mt-0.5 h-4 w-4 rounded-full border-2 shrink-0 ${accountingMode === 'cash' ? 'border-primary bg-primary' : 'border-muted-foreground/40'}`}>
              {accountingMode === 'cash' && <div className="h-full w-full rounded-full bg-primary ring-2 ring-background ring-inset" />}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-foreground">{t('admin.settings.accountingCash')}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{t('admin.settings.accountingCashDesc')}</p>
            </div>
          </button>
          <button
            type="button"
            onClick={() => updateAccountingModeMutation.mutate('accrual')}
            disabled={updateAccountingModeMutation.isPending}
            className="flex items-start gap-3 w-full px-5 py-4 text-left hover:bg-muted/40 transition-colors"
          >
            <div className={`mt-0.5 h-4 w-4 rounded-full border-2 shrink-0 ${accountingMode === 'accrual' ? 'border-primary bg-primary' : 'border-muted-foreground/40'}`}>
              {accountingMode === 'accrual' && <div className="h-full w-full rounded-full bg-primary ring-2 ring-background ring-inset" />}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-foreground">{t('admin.settings.accountingAccrual')}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{t('admin.settings.accountingAccrualDesc')}</p>
            </div>
          </button>
        </div>
      </div>

      {/* Provider categories toggle */}
      <div className="rounded-xl border border-border/60 bg-card overflow-hidden mb-8">
        <div className="px-5 py-4 border-b border-border/40">
          <div className="flex items-center gap-2 mb-0.5">
            <Tag size={15} className="text-muted-foreground" />
            <h3 className="text-sm font-semibold text-foreground">{t('admin.settings.providerCategoriesTitle')}</h3>
          </div>
        </div>
        <div className="px-5 py-4 flex items-center justify-between">
          <div>
            <p className="text-sm text-foreground">{t('admin.settings.providerCategories')}</p>
            <p className="text-xs text-muted-foreground mt-0.5">{t('admin.settings.providerCategoriesDesc')}</p>
          </div>
          <button
            onClick={() => updateProviderCatsMutation.mutate(useProviderCats ? 'false' : 'true')}
            disabled={updateProviderCatsMutation.isPending}
            className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${useProviderCats ? 'bg-primary' : 'bg-muted-foreground/20'}`}
          >
            <span
              className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${useProviderCats ? 'translate-x-6' : 'translate-x-1'}`}
            />
          </button>
        </div>
      </div>

      {/* Number / date format */}
      <div className="rounded-xl border border-border/60 bg-card overflow-hidden mb-8">
        <div className="px-5 py-4 border-b border-border/40">
          <div className="flex items-center gap-2 mb-0.5">
            <Hash size={15} className="text-muted-foreground" />
            <h3 className="text-sm font-semibold text-foreground">{t('admin.settings.numberFormatTitle')}</h3>
          </div>
          <p className="text-xs text-muted-foreground">{t('admin.settings.numberFormatDesc')}</p>
        </div>
        <div className="divide-y divide-border/40">
          {([
            { value: 'auto', label: t('admin.settings.numberFormatAuto'), desc: t('admin.settings.numberFormatAutoDesc') },
            { value: 'comma_dot', label: t('admin.settings.numberFormatCommaDot'), desc: t('admin.settings.numberFormatCommaDotDesc') },
            { value: 'dot_comma', label: t('admin.settings.numberFormatDotComma'), desc: t('admin.settings.numberFormatDotCommaDesc') },
            { value: 'space_comma', label: t('admin.settings.numberFormatSpaceComma'), desc: t('admin.settings.numberFormatSpaceCommaDesc') },
          ] as const).map((opt) => {
            // Live preview of how this option renders a sample amount, resolved
            // against the admin's currency + UI language.
            const adminCurrency = currentUser?.preferences?.currency_display ?? 'USD'
            const numLocale = resolveDisplayLocale(opt.value as NumberFormat, adminCurrency, i18n.language === 'en' ? 'en-US' : i18n.language)
            const numExample = new Intl.NumberFormat(numLocale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(1234.56)
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => updateNumberFormatMutation.mutate(opt.value)}
                disabled={updateNumberFormatMutation.isPending}
                className="flex items-start gap-3 w-full px-5 py-4 text-left hover:bg-muted/40 transition-colors"
              >
                <div className={`mt-0.5 h-4 w-4 rounded-full border-2 shrink-0 ${numberFormat === opt.value ? 'border-primary bg-primary' : 'border-muted-foreground/40'}`}>
                  {numberFormat === opt.value && <div className="h-full w-full rounded-full bg-primary ring-2 ring-background ring-inset" />}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <p className="text-sm font-medium text-foreground tabular-nums">{opt.label}</p>
                    <span className="text-xs tabular-nums text-muted-foreground">{numExample}</span>
                  </div>
                  {opt.desc && (
                    <p className="text-xs text-muted-foreground mt-0.5">{opt.desc}</p>
                  )}
                </div>
              </button>
            )
          })}
        </div>
      </div>

      {/* Date format */}
      <div className="rounded-xl border border-border/60 bg-card overflow-hidden mb-8">
        <div className="px-5 py-4 border-b border-border/40">
          <div className="flex items-center gap-2 mb-0.5">
            <CalendarDays size={15} className="text-muted-foreground" />
            <h3 className="text-sm font-semibold text-foreground">{t('admin.settings.dateFormatTitle')}</h3>
          </div>
          <p className="text-xs text-muted-foreground">{t('admin.settings.dateFormatDesc')}</p>
        </div>
        <div className="divide-y divide-border/40">
          {([
            { value: 'auto', label: t('admin.settings.dateFormatAuto'), desc: t('admin.settings.dateFormatAutoDesc') },
            { value: 'dmy', label: t('admin.settings.dateFormatDmy'), desc: t('admin.settings.dateFormatDmyDesc') },
            { value: 'mdy', label: t('admin.settings.dateFormatMdy'), desc: t('admin.settings.dateFormatMdyDesc') },
            { value: 'ymd', label: t('admin.settings.dateFormatYmd'), desc: t('admin.settings.dateFormatYmdDesc') },
          ] as const).map((opt) => {
            // Live preview, resolved against the current number format + UI
            // language. Sample 4 June makes the day/month order unambiguous.
            const adminCurrency = currentUser?.preferences?.currency_display ?? 'USD'
            const dateLocale = resolveDateLocale(opt.value as DateFormat, numberFormat as NumberFormat, adminCurrency, resolveSupportedLang(i18n.resolvedLanguage ?? i18n.language))
            const numericExample = new Date(2026, 5, 4).toLocaleDateString(dateLocale)
            const wordedExample = new Date(2026, 5, 4).toLocaleDateString(dateLocale, { day: 'numeric', month: 'short', year: 'numeric' })
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => updateDateFormatMutation.mutate(opt.value)}
                disabled={updateDateFormatMutation.isPending}
                className="flex items-start gap-3 w-full px-5 py-4 text-left hover:bg-muted/40 transition-colors"
              >
                <div className={`mt-0.5 h-4 w-4 rounded-full border-2 shrink-0 ${dateFormat === opt.value ? 'border-primary bg-primary' : 'border-muted-foreground/40'}`}>
                  {dateFormat === opt.value && <div className="h-full w-full rounded-full bg-primary ring-2 ring-background ring-inset" />}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <p className="text-sm font-medium text-foreground tabular-nums">{opt.label}</p>
                    <span className="text-xs tabular-nums text-muted-foreground">
                      {numericExample} <span className="opacity-50">·</span> {wordedExample}
                    </span>
                  </div>
                  {opt.desc && (
                    <p className="text-xs text-muted-foreground mt-0.5">{opt.desc}</p>
                  )}
                </div>
              </button>
            )
          })}
        </div>
      </div>

      {/* Settings section */}
      <div className="rounded-xl border border-border/60 bg-card overflow-hidden">
        <div className="px-5 py-4 border-b border-border/40">
          <div className="flex items-center gap-2 mb-0.5">
            <UserCog size={15} className="text-muted-foreground" />
            <h3 className="text-sm font-semibold text-foreground">{t('admin.settings.registrationTitle')}</h3>
          </div>
        </div>

        {settingsLoading ? (
          <div className="p-5">
            <Skeleton className="h-8 w-48" />
          </div>
        ) : (
          <div className="px-5 py-4 flex items-center justify-between">
            <div>
              <p className="text-sm text-foreground">{t('admin.settings.registration')}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{t('admin.settings.registrationDesc')}</p>
            </div>
            <button
              onClick={() => updateSettingMutation.mutate(isEnabled ? 'false' : 'true')}
              disabled={updateSettingMutation.isPending}
              className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${isEnabled ? 'bg-primary' : 'bg-muted-foreground/20'}`}
            >
              <span
                className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${isEnabled ? 'translate-x-6' : 'translate-x-1'}`}
              />
            </button>
          </div>
        )}
      </div>

      {/* Create User Dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('admin.users.add')}</DialogTitle>
            <DialogDescription>{t('admin.users.addDesc')}</DialogDescription>
          </DialogHeader>
          <form onSubmit={(e) => {
            e.preventDefault()
            createMutation.mutate({
              email: formEmail,
              password: formPassword,
              is_superuser: formIsAdmin,
              preferences: { language: formLanguage, currency_display: formCurrency },
            })
          }}>
            <div className="space-y-4 py-2">
              <div className="space-y-1.5">
                <Label className="text-[13px]">{t('admin.users.email')}</Label>
                <Input type="email" value={formEmail} onChange={(e) => setFormEmail(e.target.value)} required className="h-10 rounded-lg" placeholder="user@example.com" />
              </div>
              <div className="space-y-1.5">
                <Label className="text-[13px]">{t('auth.password')}</Label>
                <Input type="password" value={formPassword} onChange={(e) => setFormPassword(e.target.value)} required minLength={8} className="h-10 rounded-lg" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label className="text-[13px]">{t('setup.language')}</Label>
                  <select
                    value={formLanguage}
                    onChange={(e) => setFormLanguage(e.target.value)}
                    className="w-full h-10 rounded-lg border border-input bg-background px-3 text-sm"
                  >
                    <option value="en">English</option>
                    <option value="pt-BR">Português (BR)</option>
                    <option value="es">Español</option>
                    <option value="pl">Polski</option>
                    <option value="it">Italiano</option>
                  </select>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-[13px]">{t('setup.currency')}</Label>
                  <Select value={formCurrency} onValueChange={setFormCurrency}>
                    <SelectTrigger className="h-10 rounded-lg w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {(supportedCurrencies ?? []).map((c) => (
                        <SelectItem key={c.code} value={c.code}>
                          {c.flag} {c.code} — {c.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setFormIsAdmin(!formIsAdmin)}
                className={`flex items-center gap-2 w-full px-3.5 py-2.5 rounded-lg border text-sm font-medium transition-all ${formIsAdmin ? 'bg-primary/8 border-primary/30 text-primary' : 'border-border text-muted-foreground hover:border-border/80'}`}
              >
                {formIsAdmin ? <Shield size={15} /> : <ShieldOff size={15} />}
                <span className="flex-1 text-left">{t('admin.users.adminRole')}</span>
                <span className={`text-xs ${formIsAdmin ? 'text-primary' : 'text-muted-foreground/60'}`}>
                  {formIsAdmin ? t('admin.users.enabled') : t('admin.users.disabled')}
                </span>
              </button>
            </div>
            <DialogFooter className="mt-4">
              <Button variant="outline" type="button" onClick={() => setCreateOpen(false)} className="rounded-lg">
                {t('common.cancel')}
              </Button>
              <Button type="submit" disabled={createMutation.isPending} className="rounded-lg">
                {createMutation.isPending ? t('common.loading') : t('common.save')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Edit User Dialog */}
      <Dialog open={!!editUser} onOpenChange={(open) => !open && setEditUser(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('admin.users.edit')}</DialogTitle>
          </DialogHeader>
          {editUser && (
            <form onSubmit={(e) => {
              e.preventDefault()
              const updates: Record<string, unknown> = { id: editUser.id }
              if (editEmail !== editUser.email) updates.email = editEmail
              if (editIsActive !== editUser.is_active) updates.is_active = editIsActive
              if (editIsAdmin !== editUser.is_superuser) updates.is_superuser = editIsAdmin
              if (editPassword && editPassword.length >= 8) updates.password = editPassword
              updateMutation.mutate(updates as { id: string })
            }}>
              <div className="space-y-4 py-2">
                <div className="flex items-center gap-3 pb-2">
                  <Avatar className="h-10 w-10">
                    <AvatarFallback className={editUser.is_superuser ? 'bg-primary/15 text-primary font-semibold' : 'bg-muted text-muted-foreground font-semibold'}>
                      {editUser.email.charAt(0).toUpperCase()}
                    </AvatarFallback>
                  </Avatar>
                  <div>
                    <p className="text-sm font-medium">{editUser.email}</p>
                    <p className="text-xs text-muted-foreground">
                      {editUser.is_superuser ? t('admin.users.admin') : t('admin.users.user')}
                      {isSelf(editUser) && ` — ${t('admin.users.you')}`}
                    </p>
                  </div>
                </div>

                <div className="space-y-1.5">
                  <Label className="text-[13px]">{t('admin.users.email')}</Label>
                  <Input type="email" value={editEmail} onChange={(e) => setEditEmail(e.target.value)} required autoComplete="off" className="h-10 rounded-lg" />
                </div>
                {showPasswordField ? (
                  <div className="space-y-1.5">
                    <Label className="text-[13px]">{t('admin.users.resetPassword')}</Label>
                    <Input
                      type="text"
                      value={editPassword}
                      onChange={(e) => setEditPassword(e.target.value)}
                      placeholder={t('admin.users.resetPasswordPlaceholder')}
                      minLength={8}
                      maxLength={72}
                      autoComplete="off"
                      autoFocus
                      className="h-10 rounded-lg"
                    />
                  </div>
                ) : (
                  <Button
                    type="button"
                    variant="outline"
                    className="w-full rounded-lg"
                    onClick={() => setShowPasswordField(true)}
                  >
                    {t('admin.users.resetPassword')}
                  </Button>
                )}

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => !isSelf(editUser) && setEditIsActive(!editIsActive)}
                    disabled={isSelf(editUser)}
                    className={`flex-1 flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg border text-sm font-medium transition-all ${
                      isSelf(editUser) ? 'opacity-50 cursor-not-allowed' : ''
                    } ${editIsActive ? 'bg-emerald-500/8 border-emerald-500/30 text-emerald-600' : 'border-border text-muted-foreground'}`}
                  >
                    <span className={`h-2 w-2 rounded-full ${editIsActive ? 'bg-emerald-500' : 'bg-muted-foreground/30'}`} />
                    {editIsActive ? t('admin.users.active') : t('admin.users.inactive')}
                  </button>
                  <button
                    type="button"
                    onClick={() => !isSelf(editUser) && setEditIsAdmin(!editIsAdmin)}
                    disabled={isSelf(editUser)}
                    className={`flex-1 flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg border text-sm font-medium transition-all ${
                      isSelf(editUser) ? 'opacity-50 cursor-not-allowed' : ''
                    } ${editIsAdmin ? 'bg-primary/8 border-primary/30 text-primary' : 'border-border text-muted-foreground'}`}
                  >
                    {editIsAdmin ? <Shield size={14} /> : <ShieldOff size={14} />}
                    {t('admin.users.admin')}
                  </button>
                </div>
              </div>
              <DialogFooter className="mt-4">
                <Button variant="outline" type="button" onClick={() => setEditUser(null)} className="rounded-lg">
                  {t('common.cancel')}
                </Button>
                <Button type="submit" disabled={updateMutation.isPending} className="rounded-lg">
                  {updateMutation.isPending ? t('common.loading') : t('common.save')}
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={!!deleteUser} onOpenChange={(open) => !open && setDeleteUser(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t('admin.users.confirmDeleteTitle')}</DialogTitle>
            <DialogDescription>
              {t('admin.users.confirmDeleteDesc', { email: deleteUser?.email })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="mt-2">
            <Button variant="outline" onClick={() => setDeleteUser(null)} className="rounded-lg">
              {t('common.cancel')}
            </Button>
            <Button
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={() => deleteUser && deleteMutation.mutate(deleteUser.id)}
              className="rounded-lg"
            >
              {deleteMutation.isPending ? t('common.loading') : t('common.delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
