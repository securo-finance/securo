import { useRef, useState, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { categories as categoriesApi, categoryGroups as categoryGroupsApi, rules as rulesApi, accounts as accountsApi, payees as payeesApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { Category, Payee, Rule, RuleAction, RuleExportPayload, RulePack } from '@/types'
import { conditionSummary } from '@/lib/rule-summary'
import { normalizeRuleMatchValue, ruleSearchText } from '@/lib/rule-match-utils'
import { Trash2, Plus, RefreshCw, Package, Check, ArrowUpDown, ArrowUp, ArrowDown, Download, Upload, Search, Power } from 'lucide-react'
import { cn } from '@/lib/utils'
import { localized } from '@/lib/localized-text'
import { PageHeader } from '@/components/page-header'
import { PageRefreshButton } from '@/components/page-refresh-button'
import { useWorkspace } from '@/contexts/workspace-context'
import { RuleDialog } from '@/components/rule-dialog'
import { ReconciliationRules } from '@/components/reconciliation-rules'
import { ReconciliationQueue } from '@/components/reconciliation-queue'
import { ReconciliationHistory } from '@/components/reconciliation-history'
import { Segmented } from '@/components/invoice-ui'
import { reconciliation as reconciliationApi } from '@/lib/api'
import { findCategoryReference, getRuleCategoryName } from '@/lib/category-reference-utils'

function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      {children}
    </div>
  )
}

function SectionHeader({
  title,
  hint,
  action,
}: {
  title: string
  /** One line saying what this list of rules decides. The matching card
   *  carried one and this one did not, so the two cards answered
   *  different questions: one told you what it was for, the other
   *  assumed you knew. */
  hint?: string
  action?: React.ReactNode
}) {
  return (
    <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-start justify-between gap-2">
      <div>
        <p className="text-sm font-semibold text-foreground">{title}</p>
        {hint && <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>}
      </div>
      {action}
    </div>
  )
}

function actionSummary(actions: RuleAction[], categories: Category[], payeesList: Payee[], t: (key: string) => string): string {
  return actions.map(a => {
    if (a.op === 'set_category') {
      const cat = findCategoryReference(categories, a.value)
      return cat ? `→ ${cat.name}` : `→ ${t('transactions.category')}`
    }
    if (a.op === 'set_payee') {
      const p = payeesList.find(p => p.id === a.value)
      return p ? `→ ${t('payees.payee')}: ${p.name}` : `→ ${t('payees.payee')}`
    }
    if (a.op === 'set_description') {
      return `→ ${t('rules.fieldDescription')}: ${a.value}`
    }
    if (a.op === 'append_notes') return `→ ${t('rules.fieldNotes')}: ${a.value}`
    if (a.op === 'ignore') return `→ ${t('rules.ignoreAction')}`
    return a.op
  }).join('  ') || t('rules.noActions')
}

const ACTION_FILTERS = [
  { value: 'set_category', label: 'rules.setCategory' },
  { value: 'set_description', label: 'rules.setDescription' },
  { value: 'set_payee', label: 'rules.setPayee' },
  { value: 'append_notes', label: 'rules.appendNotes' },
  { value: 'ignore', label: 'rules.ignoreAction' },
] as const

const FILTER_CONTROL_CLASS = 'h-7 rounded-md border border-border bg-background px-2 text-xs text-foreground focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]'

export default function RulesPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { canWrite } = useWorkspace()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [packsDialogOpen, setPacksDialogOpen] = useState(false)
  const [importDialogOpen, setImportDialogOpen] = useState(false)
  const [pendingImport, setPendingImport] = useState<RuleExportPayload | null>(null)
  const [pendingImportName, setPendingImportName] = useState('')
  const importInputRef = useRef<HTMLInputElement | null>(null)
  const [editing, setEditing] = useState<Rule | null>(null)
  const [deletingRule, setDeletingRule] = useState<Rule | null>(null)
  // Bumped on every open so the dialog remounts with fresh state instead of
  // retaining the previously entered rule (issue #306).
  const [dialogInstance, setDialogInstance] = useState(0)

  function openCreate() {
    setEditing(null)
    setDialogInstance((n) => n + 1)
    setDialogOpen(true)
  }

  function openEdit(rule: Rule) {
    setEditing(rule)
    setDialogInstance((n) => n + 1)
    setDialogOpen(true)
  }

  const { data: rulesList, isFetching: rulesFetching } = useQuery({
    queryKey: ['rules'],
    queryFn: rulesApi.list,
  })

  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })

  const { data: allCategoriesList } = useQuery({
    queryKey: ['categories', 'management'],
    queryFn: categoriesApi.listIncludingHidden,
  })

  const { data: categoryGroupsList } = useQuery({
    queryKey: ['categoryGroups'],
    queryFn: categoryGroupsApi.list,
  })

  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })

  const { data: payeesList } = useQuery({
    queryKey: ['payees'],
    queryFn: payeesApi.list,
  })

  const createMutation = useMutation({
    mutationFn: (data: Omit<Rule, 'id' | 'user_id'>) => rulesApi.create(data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      queryClient.invalidateQueries({ queryKey: ['rule-packs'] })
      setDialogOpen(false)
      // The rule was applied to existing transactions on creation; refresh
      // financial views and report how many were affected for transparency.
      const applied = result.applied_count ?? 0
      if (applied > 0) {
        invalidateFinancialQueries(queryClient)
        queryClient.invalidateQueries({ queryKey: ['payees'] })
        toast.success(t('rules.createdAndApplied', { count: applied }))
      } else {
        toast.success(t('rules.created'))
      }
    },
    onError: (error: unknown) => {
      const err = error as { response?: { status?: number } }
      if (err?.response?.status === 409) {
        toast.error(t('rules.duplicateName'))
      } else {
        toast.error(t('common.error'))
      }
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: Partial<Rule> & { id: string }) => rulesApi.update(id, data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      queryClient.invalidateQueries({ queryKey: ['rule-packs'] })
      setDialogOpen(false)
      setEditing(null)
      const applied = result.applied_count ?? 0
      if (applied > 0) {
        invalidateFinancialQueries(queryClient)
        queryClient.invalidateQueries({ queryKey: ['payees'] })
        toast.success(t('rules.updatedAndApplied', { count: applied }))
      } else {
        toast.success(t('rules.updated'))
      }
    },
    onError: (error: unknown) => {
      const err = error as { response?: { status?: number } }
      if (err?.response?.status === 409) {
        toast.error(t('rules.duplicateName'))
      } else {
        toast.error(t('common.error'))
      }
    },
  })

  // Flipping the switch, and nothing else.
  //
  // Turning a rule on through the editor also runs it over transactions
  // already filed, which is the right default when you have just
  // finished writing the rule. It is the wrong default for one click on
  // an icon: nothing on screen warned that months of categories were
  // about to be rewritten. So the row does the smaller act, and catching
  // up stays explicit: the editor's own checkbox, or "Reset and
  // reapply" in the header.
  const toggleMutation = useMutation({
    mutationFn: (rule: Rule) =>
      rulesApi.update(rule.id, { is_active: !rule.is_active, apply_to_existing: false }),
    onSuccess: (_result, rule) => {
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      toast.success(t(rule.is_active ? 'rules.turnedOff' : 'rules.turnedOn'))
    },
    onError: (err: unknown) => {
      toast.error(extractApiError(err, t('common.error')))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => rulesApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      queryClient.invalidateQueries({ queryKey: ['rule-packs'] })
      setDeletingRule(null)
      toast.success(t('rules.deleted'))
    },
    onError: (err: unknown) => {
      toast.error(extractApiError(err, t('common.error')))
    },
  })

  const applyAllMutation = useMutation({
    mutationFn: () => rulesApi.applyAll(),
    onSuccess: (data) => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['payees'] })
      toast.success(t('rules.applied', { count: data.applied }))
    },
    onError: () => toast.error(t('common.error')),
  })

  const exportMutation = useMutation({
    mutationFn: () => rulesApi.exportFile(),
    onSuccess: () => toast.success(t('rules.exported')),
    onError: () => toast.error(t('common.error')),
  })

  const importMutation = useMutation({
    mutationFn: (payload: RuleExportPayload) => rulesApi.importFile(payload, true),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      queryClient.invalidateQueries({ queryKey: ['rule-packs'] })
      setImportDialogOpen(false)
      setPendingImport(null)
      setPendingImportName('')
      toast.success(t('rules.imported', { imported: data.imported, skipped: data.skipped }))
    },
    onError: () => toast.error(t('common.error')),
  })

  async function handleImportFile(file: File) {
    try {
      const parsed = JSON.parse(await file.text()) as RuleExportPayload
      if (parsed.format !== 'securo-categorization-rules' || !Array.isArray(parsed.rules)) {
        toast.error(t('rules.invalidImportFile'))
        return
      }
      setPendingImport(parsed)
      setPendingImportName(file.name)
      setImportDialogOpen(true)
    } catch {
      toast.error(t('rules.invalidImportFile'))
    } finally {
      if (importInputRef.current) importInputRef.current.value = ''
    }
  }

  const categories = useMemo(() => categoriesList ?? [], [categoriesList])
  const displayCategories = useMemo(
    () => allCategoriesList ?? categoriesList ?? [],
    [allCategoriesList, categoriesList],
  )
  const payees = useMemo(() => payeesList ?? [], [payeesList])

  const [sortBy, setSortBy] = useState<'priority' | 'name' | 'category'>('priority')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [search, setSearch] = useState('')
  const [filterCategory, setFilterCategory] = useState('')
  const [filterStatus, setFilterStatus] = useState<'' | 'active' | 'inactive'>('')
  const [filterAction, setFilterAction] = useState('')

  const hasFilters = !!(search || filterCategory || filterStatus || filterAction)

  function clearFilters() {
    setSearch('')
    setFilterCategory('')
    setFilterStatus('')
    setFilterAction('')
  }

  const filteredRules = useMemo(() => {
    const query = normalizeRuleMatchValue(search)
    return (rulesList ?? []).filter(rule => {
      // displayCategories, not categories: the row and the sort already read
      // from it, so a rule assigning a hidden category shows that name.
      // Searching over the visible-only list made that rule unfindable by the
      // very name on screen.
      if (query && !ruleSearchText(rule, displayCategories).includes(query)) return false
      if (filterCategory && !rule.actions.some(a => a.op === 'set_category' && a.value === filterCategory)) return false
      if (filterStatus === 'active' && !rule.is_active) return false
      if (filterStatus === 'inactive' && rule.is_active) return false
      if (filterAction && !rule.actions.some(a => a.op === filterAction)) return false
      return true
    })
  }, [rulesList, displayCategories, search, filterCategory, filterStatus, filterAction])

  const sortedRules = useMemo(() => {
    const list = [...filteredRules]
    const dir = sortDir === 'asc' ? 1 : -1
    if (sortBy === 'name') {
      return list.sort((a, b) => dir * a.name.localeCompare(b.name))
    }
    if (sortBy === 'category') {
      const getCategoryName = (rule: Rule) => {
        return getRuleCategoryName(rule, displayCategories) ?? ''
      }
      return list.sort((a, b) => dir * getCategoryName(a).localeCompare(getCategoryName(b)))
    }
    return list.sort((a, b) => dir * (a.priority - b.priority))
  }, [filteredRules, displayCategories, sortBy, sortDir])

  // Three reasons to come here, not one. Rules is configuration: visited
  // when somebody wants to change behaviour. The queue is *work*, visited
  // when there is something pending. History is *audit*, visited to find
  // out what happened. Burying work inside a configuration page meant only
  // people who came to configure something ever discovered they had any.
  // Matching used to serve only sets that belonged to a module, so a
  // personal workspace had no rules here, no queue and no history, and
  // the tabs were hidden rather than left empty. Transfer pairing
  // changed that: it runs for everybody, it has always run, and it is
  // now written down where its owner can read it. So there is no
  // workspace without matching any more, and nothing left to hide.

  // Addressable, because the queue is now linked to from elsewhere: a
  // badge on a transaction row is a promise to land on the question, and
  // landing on the rules list instead would break it.
  const [searchParams, setSearchParams] = useSearchParams()
  const requested = searchParams.get('tab')
  const asked =
    requested === 'queue' || requested === 'history' ? requested : 'rules'
  const tab: 'rules' | 'queue' | 'history' = asked
  const setTab = (next: 'rules' | 'queue' | 'history') => {
    // `replace`, so the back button leaves the page rather than walking
    // back through tabs somebody clicked on the way.
    setSearchParams(next === 'rules' ? {} : { tab: next }, { replace: true })
  }

  // Fetched here rather than inside the queue so the count can sit on the
  // tab: a queue nobody can see is not a queue.
  const { data: pending } = useQuery({
    queryKey: ['reconciliation-suggestions'],
    queryFn: reconciliationApi.suggestions,
  })

  return (
    <div>
      <PageHeader
        section={t('rules.section')}
        title={t('nav.rules')}
        action={
          <PageRefreshButton
            onRefresh={() => queryClient.refetchQueries({ type: 'active' })}
            refreshing={rulesFetching}
          />
        }
      />

      <div className="mb-4">
        <Segmented
          value={tab}
          onChange={setTab}
          testIdPrefix="automation-tab"
          options={[
            { value: 'rules', label: t('rules.tab.rules') },
            {
              value: 'queue' as const,
              // The count is rendered here rather than through Segmented's
              // own `count`, which is a muted figure beside a filter: the
              // right weight for "Overdue 2" and the wrong one for work
              // waiting on somebody. This is a nudge, so it looks like
              // one; when there is nothing waiting it disappears entirely
              // rather than announcing a zero.
              label: (
                <span className="inline-flex items-center gap-1.5">
                  {t('rules.tab.queue')}
                  {!!pending?.length && (
                    <span className="inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full bg-amber-500 text-white text-[10px] font-semibold tabular-nums">
                      {pending.length}
                    </span>
                  )}
                </span>
              ),
            },
            { value: 'history' as const, label: t('rules.tab.history') },
          ]}
        />
      </div>

      {tab === 'queue' && <ReconciliationQueue canWrite={canWrite} />}
      {tab === 'history' && <ReconciliationHistory />}

      <div className={tab === 'rules' ? '' : 'hidden'}>
      <SectionCard>
        <SectionHeader
          title={t('rules.sectionTitle')}
          hint={t('rules.sectionHint')}
          action={
            canWrite ? (
              <div className="flex gap-2">
                <input
                  ref={importInputRef}
                  type="file"
                  accept="application/json,.json"
                  className="hidden"
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    if (file) void handleImportFile(file)
                  }}
                />
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1.5 h-8"
                  onClick={() => exportMutation.mutate()}
                  disabled={exportMutation.isPending}
                >
                  <Download size={12} />
                  <span className="hidden sm:inline">{t('rules.export')}</span>
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1.5 h-8"
                  onClick={() => importInputRef.current?.click()}
                  disabled={importMutation.isPending}
                >
                  <Upload size={12} />
                  <span className="hidden sm:inline">{t('rules.import')}</span>
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1.5 h-8"
                  onClick={() => setPacksDialogOpen(true)}
                >
                  <Package size={12} />
                  <span className="hidden sm:inline">{t('rules.packs')}</span>
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1.5 h-8"
                  onClick={() => {
                    if (window.confirm(t('rules.confirmResetAndReapplyAll', 'Reset matching transaction categories, notes, and rule-managed descriptions, then reapply all active rules?'))) {
                      applyAllMutation.mutate()
                    }
                  }}
                  disabled={applyAllMutation.isPending}
                >
                  <RefreshCw size={12} />
                  <span className="hidden sm:inline">{t('rules.resetAndReapplyAll', 'Reset and reapply')}</span>
                </Button>
                <Button size="sm" className="gap-1.5 h-8" onClick={openCreate}>
                  <Plus size={13} /> <span className="hidden sm:inline">{t('rules.add')}</span>
                </Button>
              </div>
            ) : undefined
          }
        />
        <div className="px-4 sm:px-5 py-2 bg-muted/50 border-b border-border flex flex-wrap items-center gap-2">
          <div className="flex min-w-[10rem] flex-1 items-center gap-1.5">
            <Search size={14} className="pointer-events-none shrink-0 text-muted-foreground/70" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('rules.searchPlaceholder')}
              className="w-full min-w-0 rounded-sm bg-transparent text-xs text-foreground focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px] placeholder:text-muted-foreground/75"
            />
          </div>
          <select
            className={FILTER_CONTROL_CLASS}
            value={filterCategory}
            onChange={(e) => setFilterCategory(e.target.value)}
          >
            <option value="">{t('rules.filterAllCategories')}</option>
            {/* displayCategories so a rule assigning a hidden category is
                still filterable by it, matching what the row displays. */}
            {displayCategories.map(cat => (
              <option key={cat.id} value={cat.id}>{cat.name}</option>
            ))}
          </select>
          <select
            className={FILTER_CONTROL_CLASS}
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value as '' | 'active' | 'inactive')}
          >
            <option value="">{t('rules.filterAllStatuses')}</option>
            <option value="active">{t('rules.filterActiveOnly')}</option>
            <option value="inactive">{t('rules.filterInactiveOnly')}</option>
          </select>
          <select
            className={FILTER_CONTROL_CLASS}
            value={filterAction}
            onChange={(e) => setFilterAction(e.target.value)}
          >
            <option value="">{t('rules.filterAllActions')}</option>
            {ACTION_FILTERS.map(a => (
              <option key={a.value} value={a.value}>{t(a.label)}</option>
            ))}
          </select>
          {hasFilters && (
            <button
              type="button"
              onClick={clearFilters}
              className="h-7 rounded-md px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
            >
              {t('transactions.clearFilters')}
            </button>
          )}
          <span className="text-xs text-muted-foreground">{t('rules.sortLabel')}</span>
          {(['priority', 'name', 'category'] as const).map(opt => (
            <button
              key={opt}
              onClick={() => {
                if (sortBy === opt) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
                else { setSortBy(opt); setSortDir('asc') }
              }}
              className={cn(
                'flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-colors',
                sortBy === opt
                  ? 'bg-background border border-border text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground hover:bg-background/60'
              )}
            >
              {t(`rules.sortBy_${opt}`)}
              {sortBy === opt
                ? sortDir === 'asc' ? <ArrowUp size={11} /> : <ArrowDown size={11} />
                : <ArrowUpDown size={11} className="opacity-30" />}
            </button>
          ))}
        </div>
        {sortedRules.length > 0 ? (
          <div className="divide-y divide-border">
            {sortedRules.map((rule) => (
              <div
                key={rule.id}
                className={cn(
                  'px-4 sm:px-5 py-3 hover:bg-muted transition-colors',
                  canWrite && 'cursor-pointer',
                )}
                onClick={() => { if (canWrite) openEdit(rule) }}
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <p className="text-sm font-semibold text-foreground">{rule.name}</p>
                      {!rule.is_active && (
                        <span className="text-[10px] font-semibold bg-muted text-muted-foreground px-1.5 py-0 rounded-full">
                          {t('rules.inactive')}
                        </span>
                      )}
                      <span className="text-[10px] font-semibold bg-muted text-muted-foreground px-1.5 py-0 rounded-full">
                        p:{rule.priority}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground font-mono truncate">
                      {conditionSummary(rule.conditions, rule.conditions_op, t, (c) => c.field === 'payee_id' ? (payees.find(p => p.id === c.value)?.name ?? String(c.value)) : undefined)}
                    </p>
                    <p className="text-xs text-emerald-600 font-medium mt-0.5">
                      {actionSummary(rule.actions, displayCategories, payees, t)}
                    </p>
                  </div>
                  {canWrite && (
                    <div className="flex items-center gap-1 shrink-0">
                      {/* Stopping a rule and deleting it are different
                          decisions, and only one of them was reachable
                          from here. The other was a checkbox inside the
                          editor, so switching a rule off meant opening
                          it, finding the box, and saving a form you did
                          not want to change. It lives on the row now, in
                          the same place and the same shape as on a
                          matching rule below. */}
                      <button
                        className={cn(
                          'p-1.5 rounded-md transition-colors hover:bg-background',
                          rule.is_active
                            ? 'text-emerald-600 hover:text-emerald-700'
                            : 'text-muted-foreground hover:text-foreground',
                        )}
                        onClick={(e) => { e.stopPropagation(); toggleMutation.mutate(rule) }}
                        disabled={toggleMutation.isPending}
                        title={t(rule.is_active ? 'rules.turnOff' : 'rules.turnOn')}
                      >
                        <Power size={13} />
                      </button>
                      <button
                        className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
                        onClick={(e) => { e.stopPropagation(); setDeletingRule(rule) }}
                        disabled={deleteMutation.isPending}
                        title={t('common.delete')}
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground text-center py-10">
            {hasFilters ? t('rules.noFilterResults') : t('rules.empty')}
          </p>
        )}
      </SectionCard>

      {/* Matching rules below categorization rules, because they are the
          same promise made twice: the software decides things about your
          money, and you get to see the decision and disagree with it.
          One card per set, and transfers leads, because that is the set
          every workspace has whether or not it ever asked for one. */}
      <div className="mt-6 space-y-6">
        <ReconciliationRules canWrite={canWrite} />
      </div>
      </div>

      <DeleteConfirmationDialog
        open={!!deletingRule}
        title={t('rules.confirmDeleteTitle')}
        description={t('rules.confirmDeleteDescription', { name: deletingRule?.name })}
        isPending={deleteMutation.isPending}
        onClose={() => setDeletingRule(null)}
        onConfirm={() => deletingRule && deleteMutation.mutate(deletingRule.id)}
      />

      <RulePacksDialog
        open={packsDialogOpen}
        onClose={() => setPacksDialogOpen(false)}
      />

      <Dialog open={importDialogOpen} onOpenChange={setImportDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t('rules.importConfirmTitle')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 text-sm text-muted-foreground">
            <p>{t('rules.importConfirmDescription', { count: pendingImport?.rules.length ?? 0, file: pendingImportName })}</p>
            <p className="font-medium text-amber-600">{t('rules.importOverwriteWarning')}</p>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => { setImportDialogOpen(false); setPendingImport(null); setPendingImportName('') }}
              disabled={importMutation.isPending}
            >
              {t('common.cancel')}
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => { if (pendingImport) importMutation.mutate(pendingImport) }}
              disabled={!pendingImport || importMutation.isPending}
            >
              {t('rules.confirmOverwriteImport')}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <RuleDialog
        key={dialogInstance}
        open={dialogOpen}
        onClose={() => { setDialogOpen(false); setEditing(null) }}
        rule={editing}
        categories={categories}
        categoryGroups={categoryGroupsList ?? []}
        currentCategories={allCategoriesList ?? []}
        accounts={accountsList ?? []}
        payees={payees}
        onSave={(data) => {
          if (editing) {
            updateMutation.mutate({ id: editing.id, ...data })
          } else {
            createMutation.mutate(data as Omit<Rule, 'id' | 'user_id'>)
          }
        }}
        loading={createMutation.isPending || updateMutation.isPending}
      />
    </div>
  )
}

function RulePacksDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const [createMissingCategories, setCreateMissingCategories] = useState(true)
  const [removing, setRemoving] = useState<RulePack | null>(null)

  const { data: rulePacks } = useQuery({
    queryKey: ['rule-packs'],
    queryFn: rulesApi.packs,
    enabled: open,
  })

  const installPackMutation = useMutation({
    mutationFn: (code: string) => rulesApi.installPack(code, createMissingCategories),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      queryClient.invalidateQueries({ queryKey: ['rule-packs'] })
      if (data.categories_created > 0) {
        queryClient.invalidateQueries({ queryKey: ['categories'] })
      }
      if (data.installed === 0) {
        if (data.unresolved > 0) {
          toast.error(t('rules.packMissingCategories'))
        } else {
          toast.info(t('rules.packAlreadyInstalled'))
        }
      } else if (data.categories_created > 0) {
        toast.success(
          t('rules.packInstalledWithCategories', {
            rules: data.installed,
            categories: data.categories_created,
          }),
        )
      } else {
        toast.success(t('rules.packInstalled', { count: data.installed }))
      }
    },
    onError: () => toast.error(t('common.error')),
  })

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t('rules.packs')}</DialogTitle>
        </DialogHeader>
        <div className="flex items-center gap-2 px-1">
          <input
            type="checkbox"
            id="create-missing-categories"
            checked={createMissingCategories}
            onChange={(e) => setCreateMissingCategories(e.target.checked)}
            className="rounded border-border text-primary focus:ring-primary"
          />
          <Label
            htmlFor="create-missing-categories"
            className="text-xs text-muted-foreground cursor-pointer"
          >
            {t('rules.createMissingCategories')}
          </Label>
        </div>
        <div className="space-y-2">
          {rulePacks?.map((pack) => (
            <div
              key={pack.code}
              className="flex items-center gap-3 p-3 rounded-lg border border-border"
            >
              <span className="text-2xl">{pack.flag}</span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-semibold text-foreground">{localized(pack.names, i18n.language)}</p>
                <p className="text-xs text-muted-foreground">
                  {t('rules.packRuleCount', { count: pack.rule_count })}
                </p>
              </div>
              {pack.installed ? (
                <span className="flex items-center gap-1 text-xs font-medium text-emerald-600">
                  <Check size={14} />
                  {t('rules.installed')}
                </span>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-1.5 h-7 text-xs"
                  onClick={() => installPackMutation.mutate(pack.code)}
                  disabled={installPackMutation.isPending}
                >
                  <Package size={11} />
                  {t('rules.installPack')}
                </Button>
              )}
              {pack.rules_from_pack > 0 && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                  onClick={() => setRemoving(pack)}
                  aria-label={t('rules.removePackAria', { name: localized(pack.names, i18n.language) })}
                  title={t('rules.removePack')}
                >
                  <Trash2 size={13} />
                </Button>
              )}
            </div>
          ))}
        </div>
      </DialogContent>
      {removing && <RemovePackDialog pack={removing} onClose={() => setRemoving(null)} />}
    </Dialog>
  )
}

/** Edited rules listed by name before the rest are summed up as "and N more". */
const KEPT_NAMES_SHOWN = 5

function RemovePackDialog({ pack, onClose }: { pack: RulePack; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()

  const { data: plan, isFetching, isError, refetch } = useQuery({
    queryKey: ['rule-pack-removal', pack.code],
    queryFn: () => rulesApi.packRemoval(pack.code),
    // Every opening starts from a fresh preview, never a cached one.
    gcTime: 0,
    // Fail fast to the retry button rather than sit on "Loading" for seconds.
    retry: 1,
  })

  const removeMutation = useMutation({
    mutationFn: (token: string) => rulesApi.removePack(pack.code, token),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      queryClient.invalidateQueries({ queryKey: ['rule-packs'] })
      toast.success(t('rules.packRemoved', { count: result.remove.length }), {
        description: result.keep.length > 0
          ? t('rules.packKeptEdited', { count: result.keep.length })
          : undefined,
      })
      onClose()
    },
    onError: (error: unknown) => {
      const err = error as { response?: { status?: number } }
      if (err?.response?.status === 409) {
        // The rules changed since this preview: show the current plan instead.
        toast.error(t('rules.removePackChanged'))
        void refetch()
      } else {
        toast.error(t('common.error'))
      }
    },
  })

  const pending = removeMutation.isPending
  // A failed refresh counts as failed even with an older plan on screen:
  // that plan is exactly what the server would refuse, so it isn't shown.
  const failed = isError && !isFetching
  const kept = !failed && plan ? plan.keep : []
  // Confirm only the plan just fetched, and only if removing does something:
  // delete untouched rules, or hand edited ones over to the user.
  const confirmable =
    plan !== undefined && !isFetching && !isError && plan.remove.length + plan.keep.length > 0

  return (
    <Dialog open onOpenChange={(open) => { if (!open && !pending) onClose() }}>
      <DialogContent className="sm:max-w-md" showCloseButton={!pending}>
        <DialogHeader>
          <DialogTitle>{t('rules.removePackTitle', { name: localized(pack.names, i18n.language) })}</DialogTitle>
          <DialogDescription>
            {failed
              ? t('rules.removePackPreviewFailed')
              : plan
                ? t('rules.removePackSummary', { count: plan.remove.length })
                : t('common.loading')}
          </DialogDescription>
        </DialogHeader>
        {kept.length > 0 && (
          <div className="rounded-lg border border-border bg-muted/40 px-3 py-2.5 text-xs">
            <p className="font-medium text-foreground">
              {t('rules.removePackKeeps', { count: kept.length })}
            </p>
            <ul className="mt-1.5 space-y-0.5 text-muted-foreground">
              {kept.slice(0, KEPT_NAMES_SHOWN).map((rule) => (
                <li key={rule.id} className="truncate">{rule.name}</li>
              ))}
              {kept.length > KEPT_NAMES_SHOWN && (
                <li>{t('rules.removePackMore', { count: kept.length - KEPT_NAMES_SHOWN })}</li>
              )}
            </ul>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={pending}>
            {t('common.cancel')}
          </Button>
          {failed ? (
            <Button variant="outline" onClick={() => void refetch()} disabled={isFetching}>
              <RefreshCw size={14} className="mr-1" />
              {t('common.retry')}
            </Button>
          ) : (
            <Button
              variant="destructive"
              onClick={() => plan && removeMutation.mutate(plan.token)}
              disabled={pending || !confirmable}
            >
              <Trash2 size={14} className="mr-1" />
              {t('rules.removePackConfirm')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
