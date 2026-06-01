import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getAccountName } from '@/lib/account-utils'
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
import { X, Plus } from 'lucide-react'
import { cn } from '@/lib/utils'
import { CategorySelect } from '@/components/category-select'
import type { Category, CategoryGroup, Payee, Rule, RuleCondition, RuleAction } from '@/types'

const CONDITION_FIELDS = [
  { value: 'description', label: 'rules.fieldDescription' },
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

export interface RuleDialogInitialData {
  name?: string
  conditions?: RuleCondition[]
  actions?: RuleAction[]
}

export function RuleDialog({
  open, onClose, rule, categories, categoryGroups, accounts, payees, onSave, loading, initialData,
}: {
  open: boolean
  onClose: () => void
  rule: Rule | null
  categories: Category[]
  categoryGroups: CategoryGroup[]
  accounts: { id: string; name: string }[]
  payees: Payee[]
  onSave: (data: Partial<Rule>) => void
  loading: boolean
  initialData?: RuleDialogInitialData
}) {
  const { t } = useTranslation()

  const defaultConditions: RuleCondition[] = initialData?.conditions ?? rule?.conditions as RuleCondition[] ?? [{ field: 'description', op: 'contains', value: '' }]
  const defaultActions: RuleAction[] = initialData?.actions ?? rule?.actions as RuleAction[] ?? [{ op: 'set_category', value: '' }]

  const [name, setName] = useState(initialData?.name ?? rule?.name ?? '')
  const [conditionsOp, setConditionsOp] = useState<'and' | 'or'>(rule?.conditions_op ?? 'and')
  const [conditions, setConditions] = useState<RuleCondition[]>(
    defaultConditions.length ? defaultConditions : [{ field: 'description', op: 'contains', value: '' }]
  )
  const [actions, setActions] = useState<RuleAction[]>(
    defaultActions.length ? defaultActions : [{ op: 'set_category', value: '' }]
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

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    onSave({ name, conditions_op: conditionsOp, conditions, actions, priority, is_active: isActive })
  }

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto overflow-x-hidden">
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
                <div key={i} className="flex items-center gap-2 min-w-0">
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
                      className={`${selectClass} w-0 flex-1 min-w-0`}
                      value={String(cond.value)}
                      onChange={(e) => updateCondition(i, 'value', e.target.value)}
                    >
                      <option value="debit">{t('rules.typeExpense')}</option>
                      <option value="credit">{t('rules.typeIncome')}</option>
                    </select>
                  ) : cond.field === 'account_id' ? (
                    <select
                      className={`${selectClass} w-0 flex-1 min-w-0`}
                      value={String(cond.value)}
                      onChange={(e) => updateCondition(i, 'value', e.target.value)}
                    >
                      <option value="">{t('rules.selectAccount')}</option>
                      {accounts.map(acc => (
                        <option key={acc.id} value={acc.id}>{getAccountName(acc)}</option>
                      ))}
                    </select>
                  ) : cond.field === 'payee_id' ? (
                    <select
                      className={`${selectClass} w-0 flex-1 min-w-0`}
                      value={String(cond.value)}
                      onChange={(e) => updateCondition(i, 'value', e.target.value)}
                    >
                      <option value="">{t('rules.selectPayee')}</option>
                      {payees.map(p => (
                        <option key={p.id} value={p.id}>{p.name}</option>
                      ))}
                    </select>
                  ) : (
                    <Input
                      className="w-0 flex-1 min-w-0 h-8 text-sm"
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
                <div key={i} className="flex items-center gap-2 min-w-0">
                  <select
                    className={`${selectClass} w-40 shrink-0`}
                    value={action.op}
                    onChange={(e) => updateAction(i, 'op', e.target.value)}
                  >
                    <option value="set_category">{t('rules.setCategory')}</option>
                    <option value="set_payee">{t('rules.setPayee')}</option>
                    <option value="append_notes">{t('rules.appendNotes')}</option>
                    <option value="ignore">{t('rules.ignoreAction')}</option>
                  </select>
                  {action.op === 'ignore' ? (
                    <span className="w-0 flex-1 min-w-0 text-sm text-muted-foreground italic">
                      {t('rules.ignoreActionHint')}
                    </span>
                  ) : action.op === 'set_category' ? (
                    <div className="w-0 flex-1 min-w-0">
                      <CategorySelect
                        value={action.value}
                        onChange={(val) => updateAction(i, 'value', val)}
                        categories={categories}
                        groups={categoryGroups}
                        placeholder={t('rules.selectCategory')}
                        className={`${selectClass} w-full`}
                      />
                    </div>
                  ) : action.op === 'set_payee' ? (
                    <select
                      className={`${selectClass} w-0 flex-1 min-w-0`}
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
                      className="w-0 flex-1 min-w-0 h-8 text-sm"
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
