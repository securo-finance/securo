import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useInfiniteQuery } from '@tanstack/react-query'
import { getAccountName, sortAccountsByDisplayName } from '@/lib/account-utils'
import { isInvalidDescriptionAction, parseRulePriority, previewableActions } from '@/lib/rule-form-utils'
import { rules as rulesApi } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { X, Plus, ChevronDown, Eye, ArrowRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { CategorySelect } from '@/components/category-select'
import { flattenConditions, isConditionGroup } from '@/lib/rule-conditions'
import type {
  Category,
  CategoryGroup,
  Payee,
  Rule,
  RuleCondition,
  RuleConditionNode,
  RuleAction,
} from '@/types'

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

function defaultValueForField(field: string) {
  return field === 'type' ? 'debit' : ''
}

function newCondition(): RuleCondition {
  return { field: 'description', op: 'contains', value: '' }
}

/** Apply one edit to a leaf, resetting op/value when the field changes. */
function applyLeafChange(
  condition: RuleCondition,
  key: keyof RuleCondition,
  val: string | number,
): RuleCondition {
  if (key !== 'field') return { ...condition, [key]: val }
  return {
    ...condition,
    field: String(val),
    op: getOpsForField(String(val))[0].value,
    value: defaultValueForField(String(val)),
  }
}

const SELECT_CLASS = 'border border-border rounded-lg px-2 py-1.5 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary'

/** AND/OR switch, used for the rule itself and for each nested group. */
function OpToggle({ value, onChange }: { value: 'and' | 'or'; onChange: (op: 'and' | 'or') => void }) {
  const { t } = useTranslation()
  return (
    <div className="flex items-center gap-1 bg-muted rounded-lg p-0.5">
      {(['and', 'or'] as const).map(op => (
        <button
          key={op}
          type="button"
          className={cn(
            'px-3 py-1 text-xs font-semibold rounded-md transition-all',
            value === op ? 'bg-card shadow-sm text-foreground' : 'text-muted-foreground hover:text-foreground'
          )}
          onClick={() => onChange(op)}
        >
          {op === 'and' ? t('rules.andOp') : t('rules.orOp')}
        </button>
      ))}
    </div>
  )
}

function ConditionRow({
  condition, accounts, payees, onChange, onRemove,
}: {
  condition: RuleCondition
  accounts: { id: string; name: string; display_name?: string | null }[]
  payees: Payee[]
  onChange: (key: keyof RuleCondition, val: string | number) => void
  onRemove: () => void
}) {
  const { t } = useTranslation()
  return (
    <div className="relative grid min-w-0 grid-cols-2 gap-2 pr-7 sm:flex sm:items-center sm:pr-0">
      <select
        className={`${SELECT_CLASS} w-full sm:w-32 sm:shrink-0`}
        value={condition.field}
        onChange={(e) => onChange('field', e.target.value)}
      >
        {CONDITION_FIELDS.map(f => (
          <option key={f.value} value={f.value}>{t(f.label)}</option>
        ))}
      </select>
      <select
        className={`${SELECT_CLASS} w-full sm:w-32 sm:shrink-0`}
        value={condition.op}
        onChange={(e) => onChange('op', e.target.value)}
      >
        {getOpsForField(condition.field).map(o => (
          <option key={o.value} value={o.value}>{t(o.label)}</option>
        ))}
      </select>
      {condition.field === 'type' ? (
        <select
          className={`${SELECT_CLASS} col-span-2 w-full min-w-0 sm:w-0 sm:flex-1`}
          value={String(condition.value)}
          onChange={(e) => onChange('value', e.target.value)}
        >
          <option value="debit">{t('rules.typeExpense')}</option>
          <option value="credit">{t('rules.typeIncome')}</option>
        </select>
      ) : condition.field === 'account_id' ? (
        <select
          className={`${SELECT_CLASS} col-span-2 w-full min-w-0 sm:w-0 sm:flex-1`}
          value={String(condition.value)}
          onChange={(e) => onChange('value', e.target.value)}
        >
          <option value="">{t('rules.selectAccount')}</option>
          {accounts.map(acc => (
            <option key={acc.id} value={acc.id}>{getAccountName(acc)}</option>
          ))}
        </select>
      ) : condition.field === 'payee_id' ? (
        <select
          className={`${SELECT_CLASS} col-span-2 w-full min-w-0 sm:w-0 sm:flex-1`}
          value={String(condition.value)}
          onChange={(e) => onChange('value', e.target.value)}
        >
          <option value="">{t('rules.selectPayee')}</option>
          {payees.map(p => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
      ) : (
        <Input
          className="col-span-2 h-8 w-full min-w-0 text-sm sm:w-0 sm:flex-1"
          value={String(condition.value)}
          onChange={(e) => onChange('value', e.target.value)}
          placeholder={condition.field === 'amount' ? '0.00' : condition.field === 'date' ? 'YYYY-MM-DD' : t('rules.valuePlaceholder')}
          type={condition.field === 'amount' ? 'number' : condition.field === 'date' ? 'date' : 'text'}
        />
      )}
      <button
        type="button"
        className="absolute right-0 top-1 shrink-0 p-1 text-muted-foreground transition-colors hover:text-rose-500 sm:static"
        onClick={onRemove}
      >
        <X size={13} />
      </button>
    </div>
  )
}

/** Rows per preview request. Each one re-evaluates the whole ledger, so the
 * page is large enough that reading through a broad rule's matches is a few
 * requests rather than dozens. The API caps it at 100. */
const PREVIEW_PAGE_SIZE = 50

/** Collapsible "what would this rule do?" panel.
 *
 * Matching runs on the backend against the same engine that applies rules, so
 * the table shows exactly what saving the draft would produce — including the
 * transactions it matches but leaves untouched because they already have a
 * category. The counts cover every match; the table is one window of them at a
 * time, so a broad rule — the kind this panel exists to catch before it is
 * saved — can be read through rather than judged by its first screenful. Any
 * edit to the draft collapses the panel rather than leaving a stale table on
 * screen.
 */
function RulePreviewPanel({
  conditionsOp, conditions, actions, isActive, applyToExisting, overwriteExistingCategories,
  disabled, open, onOpenChange,
}: {
  conditionsOp: 'and' | 'or'
  conditions: RuleConditionNode[]
  actions: RuleAction[]
  // The save-time flags go to the backend too: an inactive rule, or one not
  // being applied to existing transactions, changes nothing when saved, and
  // the preview has to say so rather than promise changes that won't happen.
  isActive: boolean
  applyToExisting: boolean
  overwriteExistingCategories: boolean
  disabled: boolean
  // Open state lives in the parent: the dialog widens while the table is
  // expanded, so both have to react to the same toggle.
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()

  // A half-filled action row is a draft in progress, not a rule to reject, so
  // it is left out of the request the backend validates.
  const draftActions = useMemo(() => previewableActions(actions), [actions])

  // Keyed on the whole draft, so flipping a flag refetches while the panel
  // stays open — and a slower response for a previous draft can never land on
  // top of the current one. Paged, because a rule matching four figures of
  // transactions is one worth reading past the first page of.
  const preview = useInfiniteQuery({
    queryKey: [
      'rule-preview', conditionsOp, conditions, draftActions,
      isActive, applyToExisting, overwriteExistingCategories,
    ],
    queryFn: ({ pageParam }) => rulesApi.preview({
      conditions_op: conditionsOp,
      conditions,
      actions: draftActions,
      is_active: isActive,
      apply_to_existing: applyToExisting,
      overwrite_existing_categories: overwriteExistingCategories,
      limit: PREVIEW_PAGE_SIZE,
      offset: pageParam,
    }),
    initialPageParam: 0,
    // The counts are exact whatever window came back, so what is already on
    // screen is the offset of the next page.
    getNextPageParam: (lastPage, pages) => {
      const shown = pages.reduce((total, page) => total + page.sample.length, 0)
      return shown < lastPage.matched ? shown : undefined
    },
    enabled: open,
    staleTime: Infinity,
    gcTime: 0,
  })

  // Editing the rule itself collapses the panel rather than leaving a table
  // that describes a draft the user has moved on from. The flags don't: their
  // whole point is watching the numbers move.
  useEffect(() => {
    onOpenChange(false)
  }, [conditionsOp, conditions, actions, onOpenChange])

  // Every page carries the same counts; the rows accumulate.
  const data = preview.data?.pages[0]
  const sample = useMemo(
    () => preview.data?.pages.flatMap(page => page.sample) ?? [],
    [preview.data],
  )

  return (
    <div className="rounded-lg border border-border">
      <button
        type="button"
        disabled={disabled}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-sm text-foreground transition-colors hover:bg-muted/60 disabled:cursor-not-allowed disabled:opacity-50"
        onClick={() => onOpenChange(!open)}
      >
        <span className="flex items-center gap-1.5 font-medium">
          <Eye size={13} /> {t('rules.preview')}
        </span>
        <span className="flex items-center gap-2 text-xs text-muted-foreground">
          {data && t('rules.previewMatched', { matched: data.matched })}
          <ChevronDown size={14} className={cn('transition-transform', open && 'rotate-180')} />
        </span>
      </button>

      {open && (
        <div className="border-t border-border p-3">
          {preview.isError ? (
            <p className="text-xs text-rose-500">{t('rules.previewError')}</p>
          ) : /* also while a flag change is being recomputed: the old numbers
                 no longer describe the flags now on screen. Fetching a further
                 page is not that — those rows are appended to a table that is
                 still current. */
          !data || (preview.isFetching && !preview.isFetchingNextPage) ? (
            <p className="text-xs text-muted-foreground">{t('common.loading')}</p>
          ) : data.matched === 0 ? (
            <p className="text-xs text-muted-foreground">{t('rules.previewEmpty')}</p>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">
                {t('rules.previewSummary', { matched: data.matched, changed: data.will_change })}
                {!data.will_apply && (
                  <> · <span className="font-medium text-amber-600 dark:text-amber-400">
                    {isActive ? t('rules.previewNotAppliedToExisting') : t('rules.previewInactive')}
                  </span></>
                )}
                {sample.length < data.matched && (
                  <> · {t('rules.previewSampleNote', { shown: sample.length })}</>
                )}
              </p>
              <div className="max-h-56 overflow-y-auto overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-card text-muted-foreground">
                    <tr className="border-b border-border text-left">
                      <th className="py-1.5 pr-2 font-medium">{t('transactions.date')}</th>
                      <th className="py-1.5 pr-2 font-medium">{t('transactions.description')}</th>
                      <th className="py-1.5 pr-2 text-right font-medium">{t('transactions.amount')}</th>
                      <th className="py-1.5 font-medium">{t('transactions.category')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {sample.map(item => (
                      <tr key={item.id} className={cn(!item.will_change && 'text-muted-foreground')}>
                        <td className="whitespace-nowrap py-1.5 pr-2 tabular-nums">
                          {new Date(item.date + 'T00:00:00').toLocaleDateString(dateLocale)}
                        </td>
                        <td className="max-w-[22rem] truncate py-1.5 pr-2" title={item.description}>
                          {item.description}
                        </td>
                        <td className={cn(
                          'whitespace-nowrap py-1.5 pr-2 text-right tabular-nums',
                          item.will_change && item.type === 'credit' && 'text-emerald-600',
                        )}>
                          {mask(formatCurrency(Math.abs(item.amount), item.currency, locale))}
                        </td>
                        <td className="py-1.5">
                          {item.will_change ? (
                            <span className="flex items-center gap-1">
                              <span className="truncate">
                                {item.current_category_name ?? t('transactions.uncategorized')}
                              </span>
                              <ArrowRight size={11} className="shrink-0 text-muted-foreground" />
                              <span className="truncate font-medium text-emerald-600">
                                {item.new_category_name ?? t('transactions.uncategorized')}
                              </span>
                            </span>
                          ) : (
                            <span className="flex items-center gap-1">
                              <span className="truncate">
                                {item.current_category_name ?? t('transactions.uncategorized')}
                              </span>
                              <span className="shrink-0 rounded-full bg-muted px-1.5 text-[10px] font-semibold">
                                {t('rules.previewNoChange')}
                              </span>
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {preview.hasNextPage && (
                <button
                  type="button"
                  className="w-full rounded-md border border-border py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted/60 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={preview.isFetchingNextPage}
                  onClick={() => preview.fetchNextPage()}
                >
                  {preview.isFetchingNextPage
                    ? t('common.loading')
                    : t('rules.previewLoadMore', {
                        more: Math.min(PREVIEW_PAGE_SIZE, data.matched - sample.length),
                      })}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export interface RuleDialogInitialData {
  name?: string
  conditions?: RuleConditionNode[]
  actions?: RuleAction[]
}

export function RuleDialog({
  open, onClose, rule, categories, categoryGroups, currentCategories = [], accounts, payees, onSave, loading, initialData,
}: {
  open: boolean
  onClose: () => void
  rule: Rule | null
  categories: Category[]
  categoryGroups: CategoryGroup[]
  currentCategories?: Category[]
  accounts: { id: string; name: string; display_name?: string | null }[]
  payees: Payee[]
  onSave: (data: Partial<Rule>) => void
  loading: boolean
  initialData?: RuleDialogInitialData
}) {
  const { t } = useTranslation()
  const sortedAccounts = useMemo(() => sortAccountsByDisplayName(accounts), [accounts])

  const defaultConditions: RuleConditionNode[] = initialData?.conditions ?? rule?.conditions ?? [newCondition()]
  const defaultActions: RuleAction[] = initialData?.actions ?? rule?.actions as RuleAction[] ?? [{ op: 'set_category', value: '' }]

  const [name, setName] = useState(initialData?.name ?? rule?.name ?? '')
  const [conditionsOp, setConditionsOp] = useState<'and' | 'or'>(rule?.conditions_op ?? 'and')
  const [conditions, setConditions] = useState<RuleConditionNode[]>(
    defaultConditions.length ? defaultConditions : [newCondition()]
  )
  const [actions, setActions] = useState<RuleAction[]>(
    defaultActions.length ? defaultActions : [{ op: 'set_category', value: '' }]
  )
  const [priority, setPriority] = useState(String(rule?.priority ?? 0))
  const [isActive, setIsActive] = useState(rule?.is_active ?? true)
  const [applyToExisting, setApplyToExisting] = useState(!rule)
  const [overwriteExistingCategories, setOverwriteExistingCategories] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)

  function updateCondition(i: number, field: keyof RuleCondition, val: string | number) {
    setConditions(prev => prev.map((node, idx) => (
      idx === i && !isConditionGroup(node) ? applyLeafChange(node, field, val) : node
    )))
  }

  function removeCondition(i: number) {
    setConditions(prev => prev.filter((_, idx) => idx !== i))
  }

  function addCondition() {
    setConditions(prev => [...prev, newCondition()])
  }

  // A group starts with two conditions: one alone would carry no AND/OR meaning.
  function addGroup() {
    setConditions(prev => [...prev, { op: 'or', conditions: [newCondition(), newCondition()] }])
  }

  function setGroupOp(i: number, op: 'and' | 'or') {
    setConditions(prev => prev.map((node, idx) => (
      idx === i && isConditionGroup(node) ? { ...node, op } : node
    )))
  }

  function addGroupCondition(i: number) {
    setConditions(prev => prev.map((node, idx) => (
      idx === i && isConditionGroup(node)
        ? { ...node, conditions: [...node.conditions, newCondition()] }
        : node
    )))
  }

  function updateGroupCondition(i: number, j: number, field: keyof RuleCondition, val: string | number) {
    setConditions(prev => prev.map((node, idx) => {
      if (idx !== i || !isConditionGroup(node)) return node
      return {
        ...node,
        conditions: node.conditions.map((c, cIdx) => (cIdx === j ? applyLeafChange(c, field, val) : c)),
      }
    }))
  }

  // Removing a group's last condition removes the group — an empty one never
  // matches anything and the API rejects it.
  function removeGroupCondition(i: number, j: number) {
    setConditions(prev => prev.flatMap((node, idx) => {
      if (idx !== i || !isConditionGroup(node)) return [node]
      const remaining = node.conditions.filter((_, cIdx) => cIdx !== j)
      return remaining.length ? [{ ...node, conditions: remaining }] : []
    }))
  }

  function updateAction(i: number, field: keyof RuleAction, val: string) {
    setActions(prev => prev.map((a, idx) => {
      if (idx !== i) return a
      const next = { ...a, [field]: val }
      if (field === 'op') next.value = ''
      return next
    }))
  }

  function removeAction(i: number) {
    setActions(prev => prev.filter((_, idx) => idx !== i))
  }

  function addAction() {
    setActions(prev => [...prev, { op: 'set_category', value: '' }])
  }

  // A blank condition value matches every transaction, so the rule would apply
  // its actions to the whole ledger. The API rejects these too.
  const hasBlankCondition = flattenConditions(conditions).some(c => String(c.value ?? '').trim() === '')
  const hasInvalidDescriptionAction = actions.some(isInvalidDescriptionAction)

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (hasBlankCondition || hasInvalidDescriptionAction) return
    onSave({
      name,
      conditions_op: conditionsOp,
      conditions,
      actions,
      priority: parseRulePriority(priority),
      is_active: isActive,
      apply_to_existing: applyToExisting,
      overwrite_existing_categories: applyToExisting && overwriteExistingCategories,
    })
  }

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent
        aria-describedby={undefined}
        className={cn(
          // The preview table needs room to breathe, so the dialog widens while
          // it is expanded — same idiom as the transaction dialog's preview pane.
          'max-h-[90vh] overflow-y-auto overflow-x-hidden transition-[max-width] duration-300',
          previewOpen ? 'sm:max-w-5xl max-w-2xl' : 'sm:max-w-2xl max-w-2xl',
        )}
      >
        <DialogHeader>
          <DialogTitle>{rule ? t('rules.editRule') : t('rules.newRule')}</DialogTitle>
        </DialogHeader>
        <form key={rule?.id ?? 'new'} onSubmit={handleSubmit} className="space-y-5">
          {/* Name + Priority */}
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-2 space-y-1.5">
              <Label>{t('rules.name')}</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} required placeholder="Ex: Uber" />
            </div>
            <div className="space-y-1.5">
              <Label>{t('rules.priority')}</Label>
              <Input
                type="number"
                step="1"
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
                onBlur={() => {
                  if (priority.trim() === '' || !Number.isFinite(Number(priority))) {
                    setPriority('0')
                  }
                }}
              />
            </div>
          </div>

          {/* Conditions */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>{t('rules.conditions')}</Label>
              <OpToggle value={conditionsOp} onChange={setConditionsOp} />
            </div>
            <div className="space-y-2">
              {conditions.map((node, i) => (
                isConditionGroup(node) ? (
                  <div key={i} className="rounded-lg border border-border bg-muted/40 p-2 space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-muted-foreground">{t('rules.group')}</span>
                      <div className="flex items-center gap-1">
                        <OpToggle value={node.op} onChange={(op) => setGroupOp(i, op)} />
                        <button
                          type="button"
                          className="shrink-0 p-1 text-muted-foreground transition-colors hover:text-rose-500"
                          title={t('rules.removeGroup')}
                          onClick={() => removeCondition(i)}
                        >
                          <X size={13} />
                        </button>
                      </div>
                    </div>
                    {node.conditions.map((cond, j) => (
                      <ConditionRow
                        key={j}
                        condition={cond}
                        accounts={sortedAccounts}
                        payees={payees}
                        onChange={(field, val) => updateGroupCondition(i, j, field, val)}
                        onRemove={() => removeGroupCondition(i, j)}
                      />
                    ))}
                    <button
                      type="button"
                      className="text-xs text-primary hover:text-primary/80 font-medium flex items-center gap-1"
                      onClick={() => addGroupCondition(i)}
                    >
                      <Plus size={12} /> {t('rules.addCondition')}
                    </button>
                  </div>
                ) : (
                  <ConditionRow
                    key={i}
                    condition={node}
                    accounts={sortedAccounts}
                    payees={payees}
                    onChange={(field, val) => updateCondition(i, field, val)}
                    onRemove={() => removeCondition(i)}
                  />
                )
              ))}
              {hasBlankCondition && (
                <p className="text-xs text-rose-500">{t('rules.blankConditionValue')}</p>
              )}
              <div className="flex flex-wrap items-center gap-4">
                <button
                  type="button"
                  className="text-xs text-primary hover:text-primary/80 font-medium flex items-center gap-1"
                  onClick={addCondition}
                >
                  <Plus size={12} /> {t('rules.addCondition')}
                </button>
                <button
                  type="button"
                  className="text-xs text-primary hover:text-primary/80 font-medium flex items-center gap-1"
                  title={t('rules.addGroupHint')}
                  onClick={addGroup}
                >
                  <Plus size={12} /> {t('rules.addGroup')}
                </button>
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="space-y-2">
            <Label>{t('rules.actions')}</Label>
            <div className="space-y-2">
              {actions.map((action, i) => {
                const invalidDescription = isInvalidDescriptionAction(action)
                return (
                  <div key={i} className="space-y-1">
                    <div className="relative grid min-w-0 gap-2 pr-7 sm:flex sm:items-center sm:pr-0">
                      <select
                        className={`${SELECT_CLASS} w-full sm:w-40 sm:shrink-0`}
                        value={action.op}
                        onChange={(e) => updateAction(i, 'op', e.target.value)}
                      >
                        <option value="set_category">{t('rules.setCategory')}</option>
                        <option value="set_description">{t('rules.setDescription')}</option>
                        <option value="set_payee">{t('rules.setPayee')}</option>
                        <option value="append_notes">{t('rules.appendNotes')}</option>
                        <option value="ignore">{t('rules.ignoreAction')}</option>
                      </select>
                      {action.op === 'ignore' ? (
                        <span className="min-w-0 text-sm italic text-muted-foreground sm:w-0 sm:flex-1">
                          {t('rules.ignoreActionHint')}
                        </span>
                      ) : action.op === 'set_category' ? (
                        <div className="w-full min-w-0 sm:w-0 sm:flex-1">
                          <CategorySelect
                            value={action.value}
                            onChange={(val) => updateAction(i, 'value', val)}
                            categories={categories}
                            groups={categoryGroups}
                            currentCategory={currentCategories.find(
                              (category) => category.id === action.value
                            )}
                            placeholder={t('rules.selectCategory')}
                            className={`${SELECT_CLASS} w-full`}
                          />
                        </div>
                      ) : action.op === 'set_payee' ? (
                        <select
                          className={`${SELECT_CLASS} w-full min-w-0 sm:w-0 sm:flex-1`}
                          value={action.value}
                          onChange={(e) => updateAction(i, 'value', e.target.value)}
                          required
                        >
                          <option value="">{t('rules.selectPayee')}</option>
                          {payees.map(p => (
                            <option key={p.id} value={p.id}>{p.name}</option>
                          ))}
                        </select>
                      ) : (
                        <Input
                          className="h-8 w-full min-w-0 text-sm aria-invalid:border-input aria-invalid:ring-0 dark:aria-invalid:ring-0 sm:w-0 sm:flex-1"
                          value={action.value}
                          onChange={(e) => updateAction(i, 'value', e.target.value)}
                          placeholder={
                            action.op === 'set_description'
                              ? t('rules.descriptionValuePlaceholder')
                              : t('rules.notesValuePlaceholder')
                          }
                          maxLength={action.op === 'set_description' ? 500 : undefined}
                          required={action.op === 'set_description'}
                          aria-invalid={invalidDescription || undefined}
                          aria-describedby={invalidDescription ? `action-${i}-description-error` : undefined}
                        />
                      )}
                      <button
                        type="button"
                        className="absolute right-0 top-1 shrink-0 p-1 text-muted-foreground transition-colors hover:text-rose-500 sm:static"
                        onClick={() => removeAction(i)}
                      >
                        <X size={13} />
                      </button>
                    </div>
                    {invalidDescription && (
                      <p id={`action-${i}-description-error`} className="text-xs text-rose-500">
                        {t('rules.invalidDescriptionValue')}
                      </p>
                    )}
                  </div>
                )
              })}
              <button
                type="button"
                className="text-xs text-primary hover:text-primary/80 font-medium flex items-center gap-1"
                onClick={addAction}
              >
                <Plus size={12} /> {t('rules.addAction')}
              </button>
            </div>
          </div>

          {/* Active toggle */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={isActive}
              onChange={(e) => setIsActive(e.target.checked)}
              className="h-4 w-4 rounded border-border"
            />
            <span className="text-sm text-foreground">{t('rules.ruleActive')}</span>
          </label>

          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={applyToExisting}
              onChange={(e) => setApplyToExisting(e.target.checked)}
              className="h-4 w-4 rounded border-border"
            />
            <span className="text-sm text-foreground">
              {t('rules.applyToExisting', 'Apply matching actions to existing transactions')}
            </span>
          </label>

          {applyToExisting && (
            <label className="flex items-center gap-2 cursor-pointer pl-6">
              <input
                type="checkbox"
                checked={overwriteExistingCategories}
                onChange={(e) => setOverwriteExistingCategories(e.target.checked)}
                className="h-4 w-4 rounded border-border"
              />
              <span className="text-sm text-foreground">
                {t('rules.overwriteExistingCategories', 'Also replace existing categories')}
              </span>
            </label>
          )}

          <RulePreviewPanel
            conditionsOp={conditionsOp}
            conditions={conditions}
            actions={actions}
            isActive={isActive}
            applyToExisting={applyToExisting}
            overwriteExistingCategories={overwriteExistingCategories}
            disabled={hasBlankCondition || conditions.length === 0}
            open={previewOpen}
            onOpenChange={setPreviewOpen}
          />

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>{t('common.cancel')}</Button>
            <Button type="submit" disabled={loading || hasBlankCondition}>
              {loading ? t('common.loading') : t('common.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
