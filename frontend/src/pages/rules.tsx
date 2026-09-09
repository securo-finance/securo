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
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { Category, Payee, Rule, RuleAction, RuleCondition, RuleConditionNode, RuleExportPayload } from '@/types'
import { isConditionGroup } from '@/lib/rule-conditions'
import { normalizeRuleMatchValue, ruleSearchText } from '@/lib/rule-match-utils'
import { Trash2, Plus, RefreshCw, Package, Check, ArrowUpDown, ArrowUp, ArrowDown, Download, Upload, Search, Power } from 'lucide-react'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/page-header'
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

const CONDITION_FIELDS = [
  { value: 'description', label: 'rules.fieldDescription' },
  { value: 'payee', label: 'rules.fieldRawPayee' },
  { value: 'notes', label: 'rules.fieldNotes' },
  { value: 'amount', label: 'rules.fieldAmount' },
  { value: 'type', label: 'rules.fieldType' },
  { value: 'account_id', label: 'rules.fieldAccount' },
  { value: 'payee_id', label: 'rules.fieldPayee' },
  { value: 'date', label: 'rules.fieldDate' },
] as const

const STRING_OPS = [
  { value: 'contains', label: 'rules.opContains' },
  { value: 'not_contains', label: 'rules.opNotContains' },
  { value: 'equals', label: 'rules.opEquals' },
  { value: 'not_equals', label: 'rules.opNotEquals' },
  { value: 'starts_with', label: 'rules.opStartsWith' },
  { value: 'ends_with', label: 'rules.opEndsWith' },
  { value: 'regex', label: 'rules.opRegex' },
]

const NUMERIC_OPS = [
  { value: 'equals', label: '=' },
  { value: 'gt', label: '>' },
  { value: 'gte', label: '>=' },
  { value: 'lt', label: '<' },
  { value: 'lte', label: '<=' },
]

function getOpsForField(field: string) {
  if (field === 'amount' || field === 'date') return NUMERIC_OPS
  if (field === 'type') return [{ value: 'equals', label: 'rules.opIs' }]
  if (field === 'payee_id' || field === 'account_id') return [
    { value: 'equals', label: 'rules.opIs' },
    { value: 'not_equals', label: 'rules.opIsNot' },
  ]
  return STRING_OPS
}

function conditionSummary(conditions: RuleConditionNode[], conditionsOp: string, t: (key: string) => string, payeesList: Payee[]): string {
  const fieldLabel = (f: string) => {
    const key = CONDITION_FIELDS.find(x => x.value === f)?.label
    return key ? t(key) : f
  }
  const opLabel = (f: string, op: string) => {
    const key = getOpsForField(f).find(x => x.value === op)?.label
    return key ? t(key) : op
  }
  const valueLabel = (c: RuleCondition) => {
    if (c.field === 'payee_id') {
      const p = payeesList.find(p => p.id === c.value)
      return p ? p.name : String(c.value)
    }
    return String(c.value)
  }
  const leafSummary = (c: RuleCondition) => `${fieldLabel(c.field)} ${opLabel(c.field, c.op)} "${valueLabel(c)}"`
  const joiner = (op: string) => ` ${op === 'or' ? t('rules.orOp') : t('rules.andOp')} `
  // Groups get parentheses so a mixed AND/OR rule reads unambiguously.
  const parts = conditions.map(node => (
    isConditionGroup(node)
      ? `(${node.conditions.map(leafSummary).join(joiner(node.op))})`
      : leafSummary(node)
  ))
  return parts.join(joiner(conditionsOp)) || t('rules.noConditions')
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
  const { canWrite, hasModule } = useWorkspace()
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

  const { data: rulesList } = useQuery({
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
  // Matching serves two sets that belong to different modules: invoices,
  // and the bills you told us to expect. Either one is enough to have a
  // queue and a history worth showing; without both, the routes behind
  // them answer 404, so two permanently empty tabs would be furniture and
  // asking for their contents would be asking for a 404 on every load.
  const matching = hasModule('invoices') || hasModule('recurring')

  // Addressable, because the queue is now linked to from elsewhere: a
  // badge on a transaction row is a promise to land on the question, and
  // landing on the rules list instead would break it.
  const [searchParams, setSearchParams] = useSearchParams()
  const requested = searchParams.get('tab')
  const asked =
    requested === 'queue' || requested === 'history' ? requested : 'rules'
  // A link to a tab this workspace does not have lands on the one it
  // does, rather than on a page rendering nothing.
  const tab: 'rules' | 'queue' | 'history' = matching ? asked : 'rules'
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
    enabled: matching,
  })

  return (
    <div>
      <PageHeader section={t('rules.section')} title={t('nav.rules')} />

      <div className="mb-4">
        <Segmented
          value={tab}
          onChange={setTab}
          testIdPrefix="automation-tab"
          options={[
            { value: 'rules', label: t('rules.tab.rules') },
            ...(matching ? [{
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
            { value: 'history' as const, label: t('rules.tab.history') }] : []),
          ]}
        />
      </div>

      {matching && tab === 'queue' && <ReconciliationQueue canWrite={canWrite} />}
      {matching && tab === 'history' && <ReconciliationHistory />}

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
                      {conditionSummary(rule.conditions, rule.conditions_op, t, payees)}
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
          Not mounted at all without the module: rendering it and letting
          it decide to show nothing still costs a request that comes back
          404. */}
      <div className="mt-6 space-y-6">
        {matching && <ReconciliationRules canWrite={canWrite} />}
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
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [createMissingCategories, setCreateMissingCategories] = useState(true)

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
                <p className="text-sm font-semibold text-foreground">{pack.name}</p>
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
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  )
}
