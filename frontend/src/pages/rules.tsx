import { useState, useMemo } from 'react'
import { getAccountName } from '@/lib/account-utils'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { categories as categoriesApi, rules as rulesApi, accounts as accountsApi, payees as payeesApi } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import type { Category, Payee, Rule, RuleCondition, RuleAction } from '@/types'
import { Trash2, Plus, RefreshCw, X, Package, Check, ArrowUpDown, ArrowUp, ArrowDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/page-header'

function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      {children}
    </div>
  )
}

function SectionHeader({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-2">
      <p className="text-sm font-semibold text-foreground">{title}</p>
      {action}
    </div>
  )
}

const CONDITION_FIELDS = [
  { value: 'description', label: 'rules.fieldDescription' },
  { value: 'notes', label: 'rules.fieldNotes' },
  { value: 'amount', label: 'rules.fieldAmount' },
  { value: 'type', label: 'rules.fieldType' },
  { value: 'account_id', label: 'rules.fieldAccount' },
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
  return STRING_OPS
}

function conditionSummary(conditions: RuleCondition[], conditionsOp: string, t: (key: string) => string): string {
  const fieldLabel = (f: string) => {
    const key = CONDITION_FIELDS.find(x => x.value === f)?.label
    return key ? t(key) : f
  }
  const opLabel = (f: string, op: string) => {
    const key = getOpsForField(f).find(x => x.value === op)?.label
    return key ? t(key) : op
  }
  const parts = conditions.map(c => `${fieldLabel(c.field)} ${opLabel(c.field, c.op)} "${c.value}"`)
  return parts.join(` ${conditionsOp === 'or' ? t('rules.orOp') : t('rules.andOp')} `) || t('rules.noConditions')
}

function actionSummary(actions: RuleAction[], categories: Category[], payeesList: Payee[], t: (key: string) => string): string {
  return actions.map(a => {
    if (a.op === 'set_category') {
      const cat = categories.find(c => c.id === a.value)
      return cat ? `→ ${cat.name}` : `→ ${t('transactions.category')}`
    }
    if (a.op === 'set_payee') {
      const p = payeesList.find(p => p.id === a.value)
      return p ? `→ ${t('payees.payee')}: ${p.name}` : `→ ${t('payees.payee')}`
    }
    if (a.op === 'append_notes') return `→ ${t('rules.fieldNotes')}: ${a.value}`
    return a.op
  }).join('  ') || t('rules.noActions')
}

export default function RulesPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [packsDialogOpen, setPacksDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Rule | null>(null)

  const { data: rulesList } = useQuery({
    queryKey: ['rules'],
    queryFn: rulesApi.list,
  })

  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      queryClient.invalidateQueries({ queryKey: ['rule-packs'] })
      setDialogOpen(false)
      toast.success(t('rules.created'))
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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      queryClient.invalidateQueries({ queryKey: ['rule-packs'] })
      setDialogOpen(false)
      setEditing(null)
      toast.success(t('rules.updated'))
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

  const deleteMutation = useMutation({
    mutationFn: (id: string) => rulesApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      queryClient.invalidateQueries({ queryKey: ['rule-packs'] })
      toast.success(t('rules.deleted'))
    },
  })

  const applyAllMutation = useMutation({
    mutationFn: () => rulesApi.applyAll(),
    onSuccess: (data) => {
      invalidateFinancialQueries(queryClient)
      toast.success(t('rules.applied', { count: data.applied }))
    },
    onError: () => toast.error(t('common.error')),
  })

  const categories = categoriesList ?? []
  const payees = payeesList ?? []

  const [sortBy, setSortBy] = useState<'priority' | 'name' | 'category'>('priority')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  const sortedRules = useMemo(() => {
    const list = [...(rulesList ?? [])]
    const dir = sortDir === 'asc' ? 1 : -1
    if (sortBy === 'name') {
      return list.sort((a, b) => dir * a.name.localeCompare(b.name))
    }
    if (sortBy === 'category') {
      const getCategoryName = (rule: Rule) => {
        const action = rule.actions.find(a => a.op === 'set_category')
        if (!action) return ''
        const cat = categories.find(c => c.id === action.value)
        return cat?.name ?? ''
      }
      return list.sort((a, b) => dir * getCategoryName(a).localeCompare(getCategoryName(b)))
    }
    return list.sort((a, b) => dir * (a.priority - b.priority))
  }, [rulesList, categories, sortBy, sortDir])

  return (
    <div>
      <PageHeader section={t('rules.section')} title={t('nav.rules')} />

      <SectionCard>
        <SectionHeader
          title={t('rules.sectionTitle')}
          action={
            <div className="flex gap-2">
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
                onClick={() => applyAllMutation.mutate()}
                disabled={applyAllMutation.isPending}
              >
                <RefreshCw size={12} />
                <span className="hidden sm:inline">{t('rules.reapplyAll')}</span>
              </Button>
              <Button size="sm" className="gap-1.5 h-8" onClick={() => { setEditing(null); setDialogOpen(true) }}>
                <Plus size={13} /> <span className="hidden sm:inline">{t('rules.add')}</span>
              </Button>
            </div>
          }
        />
        <div className="px-4 sm:px-5 py-2 bg-muted/50 border-b border-border flex items-center gap-2">
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
        {rulesList && rulesList.length > 0 ? (
          <div className="divide-y divide-border">
            {sortedRules.map((rule) => (
              <div
                key={rule.id}
                className="px-4 sm:px-5 py-3 hover:bg-muted transition-colors cursor-pointer"
                onClick={() => { setEditing(rule); setDialogOpen(true) }}
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
                      {conditionSummary(rule.conditions, rule.conditions_op, t)}
                    </p>
                    <p className="text-xs text-emerald-600 font-medium mt-0.5">
                      {actionSummary(rule.actions, categories, payees, t)}
                    </p>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
                      onClick={(e) => { e.stopPropagation(); deleteMutation.mutate(rule.id) }}
                      disabled={deleteMutation.isPending}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground text-center py-10">{t('rules.empty')}</p>
        )}
      </SectionCard>

      <RulePacksDialog
        open={packsDialogOpen}
        onClose={() => setPacksDialogOpen(false)}
      />

      <RuleDialog
        key={editing?.id ?? 'new'}
        open={dialogOpen}
        onClose={() => { setDialogOpen(false); setEditing(null) }}
        rule={editing}
        categories={categories}
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

  const { data: rulePacks } = useQuery({
    queryKey: ['rule-packs'],
    queryFn: rulesApi.packs,
    enabled: open,
  })

  const installPackMutation = useMutation({
    mutationFn: (code: string) => rulesApi.installPack(code),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      queryClient.invalidateQueries({ queryKey: ['rule-packs'] })
      if (data.installed === 0) {
        toast.info(t('rules.packAlreadyInstalled'))
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

function RuleDialog({
  open, onClose, rule, categories, accounts, payees, onSave, loading,
}: {
  open: boolean
  onClose: () => void
  rule: Rule | null
  categories: Category[]
  accounts: { id: string; name: string }[]
  payees: Payee[]
  onSave: (data: Partial<Rule>) => void
  loading: boolean
}) {
  const { t } = useTranslation()
  const [name, setName] = useState(rule?.name ?? '')
  const [conditionsOp, setConditionsOp] = useState<'and' | 'or'>(rule?.conditions_op ?? 'and')
  const [conditions, setConditions] = useState<RuleCondition[]>(
    rule?.conditions?.length ? rule.conditions as RuleCondition[] : [{ field: 'description', op: 'contains', value: '' }]
  )
  const [actions, setActions] = useState<RuleAction[]>(
    rule?.actions?.length ? rule.actions as RuleAction[] : [{ op: 'set_category', value: '' }]
  )
  const [priority, setPriority] = useState(rule?.priority ?? 0)
  const [isActive, setIsActive] = useState(rule?.is_active ?? true)

  const selectClass = 'border border-border rounded-lg px-2 py-1.5 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary'

  function updateCondition(i: number, field: keyof RuleCondition, val: string | number) {
    setConditions(prev => prev.map((c, idx) => idx === i ? { ...c, [field]: val } : c))
  }

  function removeCondition(i: number) {
    setConditions(prev => prev.filter((_, idx) => idx !== i))
  }

  function addCondition() {
    setConditions(prev => [...prev, { field: 'description', op: 'contains', value: '' }])
  }

  function updateAction(i: number, field: keyof RuleAction, val: string) {
    setActions(prev => prev.map((a, idx) => idx === i ? { ...a, [field]: val } : a))
  }

  function removeAction(i: number) {
    setActions(prev => prev.filter((_, idx) => idx !== i))
  }

  function addAction() {
    setActions(prev => [...prev, { op: 'set_category', value: '' }])
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    onSave({ name, conditions_op: conditionsOp, conditions, actions, priority, is_active: isActive })
  }

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
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
              <Input type="number" value={priority} onChange={(e) => setPriority(Number(e.target.value))} />
            </div>
          </div>

          {/* Conditions */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>{t('rules.conditions')}</Label>
              <div className="flex items-center gap-1 bg-muted rounded-lg p-0.5">
                {(['and', 'or'] as const).map(op => (
                  <button
                    key={op}
                    type="button"
                    className={cn(
                      'px-3 py-1 text-xs font-semibold rounded-md transition-all',
                      conditionsOp === op ? 'bg-card shadow-sm text-foreground' : 'text-muted-foreground hover:text-foreground'
                    )}
                    onClick={() => setConditionsOp(op)}
                  >
                    {op === 'and' ? t('rules.andOp') : t('rules.orOp')}
                  </button>
                ))}
              </div>
            </div>
            <div className="space-y-2">
              {conditions.map((cond, i) => (
                <div key={i} className="flex items-center gap-2">
                  <select
                    className={`${selectClass} w-32 shrink-0`}
                    value={cond.field}
                    onChange={(e) => updateCondition(i, 'field', e.target.value)}
                  >
                    {CONDITION_FIELDS.map(f => (
                      <option key={f.value} value={f.value}>{t(f.label)}</option>
                    ))}
                  </select>
                  <select
                    className={`${selectClass} w-32 shrink-0`}
                    value={cond.op}
                    onChange={(e) => updateCondition(i, 'op', e.target.value)}
                  >
                    {getOpsForField(cond.field).map(o => (
                      <option key={o.value} value={o.value}>{t(o.label)}</option>
                    ))}
                  </select>
                  {cond.field === 'type' ? (
                    <select
                      className={`${selectClass} flex-1`}
                      value={String(cond.value)}
                      onChange={(e) => updateCondition(i, 'value', e.target.value)}
                    >
                      <option value="debit">{t('rules.typeExpense')}</option>
                      <option value="credit">{t('rules.typeIncome')}</option>
                    </select>
                  ) : cond.field === 'account_id' ? (
                    <select
                      className={`${selectClass} flex-1`}
                      value={String(cond.value)}
                      onChange={(e) => updateCondition(i, 'value', e.target.value)}
                    >
                      <option value="">{t('rules.selectAccount')}</option>
                      {accounts.map(acc => (
                        <option key={acc.id} value={acc.id}>{getAccountName(acc)}</option>
                      ))}
                    </select>
                  ) : (
                    <Input
                      className="flex-1 h-8 text-sm"
                      value={String(cond.value)}
                      onChange={(e) => updateCondition(i, 'value', e.target.value)}
                      placeholder={cond.field === 'amount' ? '0.00' : cond.field === 'date' ? 'YYYY-MM-DD' : t('rules.valuePlaceholder')}
                      type={cond.field === 'amount' ? 'number' : cond.field === 'date' ? 'date' : 'text'}
                    />
                  )}
                  <button
                    type="button"
                    className="p-1 text-muted-foreground hover:text-rose-500 transition-colors shrink-0"
                    onClick={() => removeCondition(i)}
                  >
                    <X size={13} />
                  </button>
                </div>
              ))}
              <button
                type="button"
                className="text-xs text-primary hover:text-primary/80 font-medium flex items-center gap-1"
                onClick={addCondition}
              >
                <Plus size={12} /> {t('rules.addCondition')}
              </button>
            </div>
          </div>

          {/* Actions */}
          <div className="space-y-2">
            <Label>{t('rules.actions')}</Label>
            <div className="space-y-2">
              {actions.map((action, i) => (
                <div key={i} className="flex items-center gap-2">
                  <select
                    className={`${selectClass} w-40 shrink-0`}
                    value={action.op}
                    onChange={(e) => updateAction(i, 'op', e.target.value)}
                  >
                    <option value="set_category">{t('rules.setCategory')}</option>
                    <option value="set_payee">{t('rules.setPayee')}</option>
                    <option value="append_notes">{t('rules.appendNotes')}</option>
                  </select>
                  {action.op === 'set_category' ? (
                    <select
                      className={`${selectClass} flex-1`}
                      value={action.value}
                      onChange={(e) => updateAction(i, 'value', e.target.value)}
                      required
                    >
                      <option value="">{t('rules.selectCategory')}</option>
                      {categories.map(cat => (
                        <option key={cat.id} value={cat.id}>{cat.name}</option>
                      ))}
                    </select>
                  ) : action.op === 'set_payee' ? (
                    <select
                      className={`${selectClass} flex-1`}
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
                      className="flex-1 h-8 text-sm"
                      value={action.value}
                      onChange={(e) => updateAction(i, 'value', e.target.value)}
                      placeholder="Ex: #work #reimbursable"
                    />
                  )}
                  <button
                    type="button"
                    className="p-1 text-muted-foreground hover:text-rose-500 transition-colors shrink-0"
                    onClick={() => removeAction(i)}
                  >
                    <X size={13} />
                  </button>
                </div>
              ))}
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

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>{t('common.cancel')}</Button>
            <Button type="submit" disabled={loading}>
              {loading ? t('common.loading') : t('common.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
