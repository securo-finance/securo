/** The rules matching follows, shown and editable.
 *
 *  Sits under the categorization rules because it is the same promise made
 *  twice: the software decides things about your money, and you get to see
 *  the decision and disagree with it. Matching used to be numbers buried in
 *  a module; this is those numbers, with a name and a switch.
 *
 *  Two things this screen deliberately is not. It is not an open-ended
 *  condition builder like the rules above it — matching runs on a fixed set
 *  of signals, and offering fields the engine cannot read would be a lie
 *  told in a nice font. And it is not a list stored in the database: what
 *  you see is what we ship with whatever you changed applied over it, so a
 *  rule you never touched keeps improving when we improve it.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  reconciliation as reconciliationApi,
  accounts as accountsApi,
  payees as payeesApi,
} from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type {
  Account,
  Payee,
  Trigger,
  ReconciliationConditions,
  ReconciliationNode,
  ReconciliationRule,
} from '@/types'
import { Plus, RotateCcw, Trash2, Zap, HelpCircle, ChevronUp, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'

/** Names for the rules we ship. Kept here rather than sent by the API so
 *  they follow the reader's language, and so a rule someone edited does not
 *  freeze its label in whatever language it was edited in. */
const SHIPPED_NAME: Record<string, string> = {
  same_client_exact: 'reconciliation.rule.sameClientExact',
  same_client_net_of_withholding: 'reconciliation.rule.netOfWithholding',
  exact_amount_any_client: 'reconciliation.rule.exactAmountAnyClient',
  same_client_part_payment: 'reconciliation.rule.partPayment',
  same_client_several_invoices: 'reconciliation.rule.severalInvoices',
  similar_description: 'reconciliation.rule.similarDescription',
  same_account_exact: 'reconciliation.rule.sameAccountExact',
}

const NODE_TITLE: Record<string, string> = {
  'reconciliation.match_invoice': 'reconciliation.node.invoices',
  'reconciliation.match_recurring': 'reconciliation.node.recurring',
}

const NODE_HINT: Record<string, string> = {
  'reconciliation.match_invoice': 'reconciliation.node.invoicesHint',
  'reconciliation.match_recurring': 'reconciliation.node.recurringHint',
}

/** The whole order with two entries exchanged.
 *
 *  Returns every id, not the pair that moved, because that is what the
 *  API takes — and for a good reason: an order where some rules are
 *  placed and the rest fall back to wherever we shipped them reads fine
 *  today and quietly rearranges the day a new default is inserted. */
function swap(rules: ReconciliationRule[], from: number, to: number): string[] {
  const ids = rules.map((rule) => rule.id)
  const moved = ids.splice(from, 1)[0]
  ids.splice(to, 0, moved)
  return ids
}


function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      {children}
    </div>
  )
}

/** One rule in a sentence, built from the signals it actually consults.
 *
 *  Written out rather than shown as a form on the row because the question
 *  a reader arrives with is "why did this match", and a sentence answers it
 *  where a grid of numbers does not. */
function conditionSummary(
  when: ReconciliationConditions & { trigger?: Trigger },
  t: (key: string, opts?: Record<string, unknown>) => string,
  names: { accounts: Account[]; payees: Payee[] },
): string {
  const parts: string[] = []

  // The moment first, because it is the question the old summary could
  // not answer: does this fire when money lands, or when the document is
  // written? "Both" says nothing worth a word, so it says nothing.
  if (when.trigger === 'invoice_issued') parts.push(t('reconciliation.cond.onIssue'))
  else if (when.trigger === 'money_arrives') parts.push(t('reconciliation.cond.onArrival'))

  // Then the scope filters, which answer "does this even apply?" before
  // the question of how closely the pair has to fit.
  if (when.accounts?.in?.length) {
    parts.push(
      t('reconciliation.cond.accounts', {
        names: when.accounts.in
          .map((id) => names.accounts.find((a) => a.id === id)?.name ?? '?')
          .join(', '),
      }),
    )
  }
  if (when.payees?.in?.length) {
    parts.push(
      t('reconciliation.cond.payees', {
        names: when.payees.in
          .map((id) => names.payees.find((p) => p.id === id)?.name ?? '?')
          .join(', '),
      }),
    )
  }
  if (when.direction && when.direction !== 'any') {
    parts.push(
      t(when.direction === 'credit'
        ? 'reconciliation.cond.moneyIn'
        : 'reconciliation.cond.moneyOut'),
    )
  }
  if (when.currency?.foreign) parts.push(t('reconciliation.cond.foreign'))
  if (when.currency?.in?.length)
    parts.push(t('reconciliation.cond.currencyIn', { codes: when.currency.in.join(', ') }))
  if (when.amount?.min) parts.push(t('reconciliation.cond.atLeast', { value: when.amount.min }))
  if (when.amount?.max) parts.push(t('reconciliation.cond.atMost', { value: when.amount.max }))
  if (when.text?.contains)
    parts.push(t('reconciliation.cond.textContains', { text: when.text.contains }))
  if (when.text?.not_contains)
    parts.push(t('reconciliation.cond.textExcludes', { text: when.text.not_contains }))

  if (when.counterparty === 'same_payee') parts.push(t('reconciliation.cond.samePayee'))
  if (when.same_account) parts.push(t('reconciliation.cond.sameAccount'))

  if (when.amount?.match === 'exact') parts.push(t('reconciliation.cond.amountExact'))
  else if (when.amount?.match === 'tolerance')
    parts.push(t('reconciliation.cond.amountTolerance', { percent: when.amount.percent }))
  else if (when.amount?.match === 'ratio')
    parts.push(t('reconciliation.cond.amountRatio'))
  else if (when.amount?.match === 'partial')
    parts.push(t('reconciliation.cond.amountPartial'))
  else if (when.amount?.match === 'set')
    parts.push(
      t('reconciliation.cond.amountSet', { count: when.amount.max_invoices ?? 6 }),
    )

  if (when.date)
    parts.push(
      t('reconciliation.cond.window', {
        before: when.date.before_days,
        after: when.date.after_days,
      }),
    )

  if (when.description_similarity)
    parts.push(
      t('reconciliation.cond.similarity', { min: when.description_similarity.min }),
    )

  if (when.currency?.conversion === 'allow')
    parts.push(t('reconciliation.cond.currencyAllow'))

  return parts.join(' · ') || t('reconciliation.cond.none')
}

function OutcomeBadge({ outcome }: { outcome: 'link' | 'suggest' }) {
  const { t } = useTranslation()
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-full',
        outcome === 'link'
          ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300'
          : 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300',
      )}
    >
      {outcome === 'link' ? <Zap size={10} /> : <HelpCircle size={10} />}
      {t(outcome === 'link' ? 'reconciliation.outcome.link' : 'reconciliation.outcome.suggest')}
    </span>
  )
}

/** One of the three questions a rule answers, with its heading.
 *
 *  The form used to be a flat list, and two of its fields were both called
 *  "Amount" — one asking how close the payment must be to the invoice, the
 *  other asking which payments the rule looks at. Same word, different
 *  question, twenty pixels apart. Splitting them under headings is not
 *  decoration: it is the difference between a form you can read and one
 *  you have to already understand. */
function Step({
  index,
  title,
  hint,
  children,
}: {
  index: number
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <section className="rounded-lg border border-border">
      <header className="px-3 py-2 bg-muted/40 border-b border-border rounded-t-lg">
        <p className="text-sm font-semibold text-foreground">
          <span className="text-muted-foreground mr-1.5">{index}.</span>
          {title}
        </p>
        {hint && <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>}
      </header>
      <div className="p-3 space-y-3">{children}</div>
    </section>
  )
}


/** Pick none, one, or several — accounts or clients.
 *
 *  Checkboxes rather than a multi-select control because the list is short
 *  and the state that matters is "nothing chosen", which a native
 *  multi-select renders as an empty box that reads like a mistake. Here it
 *  reads as a sentence: *any account*. */
function MultiPicker({
  options,
  selected,
  onChange,
  empty,
}: {
  options: { id: string; label: string }[]
  selected: string[]
  onChange: (ids: string[]) => void
  empty: string
}) {
  if (options.length === 0) return null
  return (
    <div className="mt-0.5 max-h-32 overflow-y-auto rounded-md border border-input divide-y divide-border">
      {selected.length === 0 && (
        <p className="px-3 py-1.5 text-xs text-muted-foreground italic">{empty}</p>
      )}
      {options.map((option) => (
        <label
          key={option.id}
          className="flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer hover:bg-muted"
        >
          <input
            type="checkbox"
            checked={selected.includes(option.id)}
            onChange={(e) =>
              onChange(
                e.target.checked
                  ? [...selected, option.id]
                  : selected.filter((id) => id !== option.id),
              )
            }
          />
          <span className="truncate">{option.label}</span>
        </label>
      ))}
    </div>
  )
}


interface EditorProps {
  open: boolean
  node: string
  rule: ReconciliationRule | null
  onClose: () => void
}

const EMPTY: ReconciliationConditions = {
  counterparty: 'any',
  amount: { match: 'exact' },
  date: { before_days: 5, after_days: 30 },
}

function RuleEditor({ open, node, rule, onClose }: EditorProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const creating = rule === null

  const { data: accountList = [] } = useQuery<Account[]>({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })
  const { data: payeeList = [] } = useQuery<Payee[]>({
    queryKey: ['payees'],
    queryFn: () => payeesApi.list(),
  })

  const [name, setName] = useState(rule?.name ?? '')
  const [outcome, setOutcome] = useState<'link' | 'suggest'>(rule?.outcome ?? 'suggest')
  const [trigger, setTrigger] = useState<Trigger>(rule?.trigger ?? 'money_arrives')
  const [when, setWhen] = useState<ReconciliationConditions>(rule?.when ?? EMPTY)

  // The dialog is mounted once and reused, so it has to be re-seeded
  // whenever it opens — and the seed has to be *forgotten* when it closes.
  // Without the second half, reopening the same rule after cancelling
  // shows the abandoned edits as though they had been saved, which is a
  // worse lie than losing them: the screen would claim a threshold the
  // engine is not running.
  const [seeded, setSeeded] = useState<string | null>(null)
  const key = `${node}:${rule?.id ?? 'new'}`
  if (!open && seeded !== null) setSeeded(null)
  if (open && seeded !== key) {
    setSeeded(key)
    setName(rule?.name ?? '')
    setOutcome(rule?.outcome ?? 'suggest')
    setTrigger(rule?.trigger ?? 'money_arrives')
    setWhen(rule?.when ?? EMPTY)
  }

  const save = useMutation({
    mutationFn: async () => {
      if (creating) {
        return reconciliationApi.createRule({ node, name, outcome, trigger, when })
      }
      return reconciliationApi.updateRule(node, rule.id, { outcome, trigger, when })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['reconciliation-rules'] })
      toast.success(t('reconciliation.saved'))
      onClose()
    },
    onError: (error) => toast.error(extractApiError(error, t('common.error'))),
  })

  const amountMatch = when.amount?.match ?? 'exact'

  /** One labelled control inside a step. Keeps the two amount questions
   *  visually distinct even though both are about money. */
  const field = (label: string, hint: string | null, control: React.ReactNode) => (
    <div>
      <Label>{label}</Label>
      {hint && <p className="text-xs text-muted-foreground mt-0.5 mb-1.5">{hint}</p>}
      <div className={hint ? '' : 'mt-1'}>{control}</div>
    </div>
  )

  const inputClass =
    'w-full px-3 py-2 text-sm border border-input rounded-md bg-background'

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose() }}>
      {/* Capped and scrolled, with the actions outside the scrolling part.
          A rule with every section open is taller than a laptop viewport,
          and a dialog whose Save button is below the fold with nothing to
          scroll is a dialog you cannot save from. */}
      <DialogContent className="max-w-lg max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>
            {creating
              ? t('reconciliation.newRule')
              : rule?.name || t(SHIPPED_NAME[rule?.id ?? ''] ?? 'reconciliation.rule.unknown')}
          </DialogTitle>
        </DialogHeader>

        {/* Three questions, in the order a person asks them: when does this
            run, which money does it look at, and what counts as a match.
            The old flat list had two fields called "Amount" twenty pixels
            apart — one about how close the payment must be to the invoice,
            one about which payments the rule looks at. Same word, different
            question. */}
        <div className="space-y-4 flex-1 overflow-y-auto -mx-1 px-1">
          <Step index={1} title={t('reconciliation.step.what')}>
            {creating &&
              field(
                t('reconciliation.field.name'),
                null,
                <input
                  className={inputClass}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t('reconciliation.field.namePlaceholder')}
                />,
              )}

            {field(
              t('reconciliation.field.trigger'),
              t('reconciliation.field.triggerHint'),
              <div className="space-y-1.5">
                {(['money_arrives', 'invoice_issued', 'both'] as const).map((value) => (
                  <label
                    key={value}
                    className={cn(
                      'flex items-start gap-2 px-3 py-2 rounded-md border cursor-pointer transition-colors',
                      trigger === value
                        ? 'border-primary bg-primary/5'
                        : 'border-input hover:bg-muted',
                    )}
                  >
                    <input
                      type="radio"
                      className="mt-1"
                      checked={trigger === value}
                      onChange={() => setTrigger(value)}
                    />
                    <span>
                      <span className="text-sm font-medium block">
                        {t(`reconciliation.trigger.${value}`)}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {t(`reconciliation.trigger.${value}Hint`)}
                      </span>
                    </span>
                  </label>
                ))}
              </div>,
            )}

            {field(
              t('reconciliation.field.outcome'),
              t('reconciliation.field.outcomeHint'),
              <div className="flex gap-2">
                {(['link', 'suggest'] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setOutcome(value)}
                    className={cn(
                      'flex-1 px-3 py-2 rounded-md text-sm font-medium border transition-colors',
                      outcome === value
                        ? 'bg-primary text-primary-foreground border-primary'
                        : 'bg-background border-input text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {t(
                      value === 'link'
                        ? 'reconciliation.outcome.link'
                        : 'reconciliation.outcome.suggest',
                    )}
                  </button>
                ))}
              </div>,
            )}
          </Step>

          <Step
            index={2}
            title={t('reconciliation.step.which')}
            hint={t('reconciliation.step.whichHint')}
          >
            {field(
              t('reconciliation.field.accounts'),
              null,
              <MultiPicker
                options={accountList.map((a) => ({ id: a.id, label: a.name }))}
                selected={when.accounts?.in ?? []}
                onChange={(ids) =>
                  setWhen({ ...when, accounts: ids.length ? { in: ids } : undefined })
                }
                empty={t('reconciliation.field.anyAccount')}
              />,
            )}

            {field(
              t('reconciliation.field.payees'),
              null,
              <MultiPicker
                options={payeeList.map((p) => ({ id: p.id, label: p.name }))}
                selected={when.payees?.in ?? []}
                onChange={(ids) =>
                  setWhen({ ...when, payees: ids.length ? { in: ids } : undefined })
                }
                empty={t('reconciliation.field.anyPayee')}
              />,
            )}

            {field(
              t('reconciliation.field.direction'),
              null,
              <select
                className={inputClass}
                value={when.direction ?? 'any'}
                onChange={(e) =>
                  setWhen({
                    ...when,
                    direction: e.target.value as 'any' | 'credit' | 'debit',
                  })
                }
              >
                <option value="any">{t('reconciliation.direction.any')}</option>
                <option value="credit">{t('reconciliation.direction.in')}</option>
                <option value="debit">{t('reconciliation.direction.out')}</option>
              </select>,
            )}

            {field(
              t('reconciliation.field.currencyScope'),
              null,
              <>
                <input
                  // Only what was typed is shouted. Uppercasing the
                  // placeholder too turns a hint into an instruction.
                  className={`${inputClass} [&:not(:placeholder-shown)]:uppercase`}
                  placeholder={t('reconciliation.field.currencyPlaceholder')}
                  value={(when.currency?.in ?? []).join(', ')}
                  onChange={(e) => {
                    const codes = e.target.value
                      .split(',')
                      .map((code) => code.trim().toUpperCase())
                      .filter((code) => code.length === 3)
                    setWhen({
                      ...when,
                      currency: {
                        ...(when.currency ?? { conversion: 'reject' }),
                        in: codes.length ? codes : undefined,
                      },
                    })
                  }}
                />
                <label className="flex items-center gap-2 mt-1.5 text-xs text-muted-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={!!when.currency?.foreign}
                    onChange={(e) =>
                      setWhen({
                        ...when,
                        currency: {
                          ...(when.currency ?? { conversion: 'reject' }),
                          foreign: e.target.checked || undefined,
                        },
                      })
                    }
                  />
                  {t('reconciliation.field.foreignOnly')}
                </label>
              </>,
            )}

            {field(
              t('reconciliation.field.amountBand'),
              t('reconciliation.field.amountBandHint'),
              <div className="flex gap-2">
                <input
                  type="number"
                  min={0}
                  className={inputClass}
                  placeholder={t('reconciliation.field.noMinimum')}
                  value={when.amount?.min ?? ''}
                  onChange={(e) =>
                    setWhen({
                      ...when,
                      amount: {
                        ...(when.amount ?? { match: 'exact' }),
                        min: e.target.value || undefined,
                      },
                    })
                  }
                />
                <input
                  type="number"
                  min={0}
                  className={inputClass}
                  placeholder={t('reconciliation.field.noMaximum')}
                  value={when.amount?.max ?? ''}
                  onChange={(e) =>
                    setWhen({
                      ...when,
                      amount: {
                        ...(when.amount ?? { match: 'exact' }),
                        max: e.target.value || undefined,
                      },
                    })
                  }
                />
              </div>,
            )}

            {field(
              t('reconciliation.field.text'),
              null,
              <>
                <input
                  className={inputClass}
                  placeholder={t('reconciliation.field.textContains')}
                  value={when.text?.contains ?? ''}
                  onChange={(e) =>
                    setWhen({
                      ...when,
                      text: { ...when.text, contains: e.target.value || undefined },
                    })
                  }
                />
                <input
                  className={`${inputClass} mt-1.5`}
                  placeholder={t('reconciliation.field.textExcludes')}
                  value={when.text?.not_contains ?? ''}
                  onChange={(e) =>
                    setWhen({
                      ...when,
                      text: { ...when.text, not_contains: e.target.value || undefined },
                    })
                  }
                />
              </>,
            )}
          </Step>

          <Step
            index={3}
            title={t('reconciliation.step.match')}
            hint={t('reconciliation.step.matchHint')}
          >
            {field(
              t('reconciliation.field.counterparty'),
              null,
              <select
                className={inputClass}
                value={when.counterparty ?? 'any'}
                onChange={(e) =>
                  setWhen({ ...when, counterparty: e.target.value as 'any' | 'same_payee' })
                }
              >
                <option value="any">{t('reconciliation.counterparty.any')}</option>
                <option value="same_payee">{t('reconciliation.counterparty.samePayee')}</option>
              </select>,
            )}

            {field(
              t('reconciliation.field.amountMatch'),
              t('reconciliation.field.amountMatchHint'),
              <>
                <div className="flex gap-2">
                  <select
                    className={inputClass}
                    value={amountMatch}
                    onChange={(e) => {
                      const match = e.target.value as
                        | 'exact'
                        | 'tolerance'
                        | 'ratio'
                        | 'partial'
                        | 'set'
                      const kept = { min: when.amount?.min, max: when.amount?.max }
                      setWhen({
                        ...when,
                        amount:
                          match === 'tolerance'
                            ? { match, percent: when.amount?.percent ?? '2', ...kept }
                            : match === 'partial'
                              ? {
                                  match,
                                  min_ratio: when.amount?.min_ratio ?? '0.05',
                                  max_ratio: when.amount?.max_ratio ?? '0.95',
                                  ...kept,
                                }
                              : match === 'set'
                                ? {
                                    match,
                                    max_invoices: when.amount?.max_invoices ?? 6,
                                    percent: when.amount?.percent ?? '0',
                                    ...kept,
                                  }
                                : { match, ...kept },
                      })
                    }}
                  >
                    <option value="exact">{t('reconciliation.amount.exact')}</option>
                    <option value="partial">{t('reconciliation.amount.partial')}</option>
                    <option value="set">{t('reconciliation.amount.set')}</option>
                    <option value="tolerance">{t('reconciliation.amount.tolerance')}</option>
                    <option value="ratio">{t('reconciliation.amount.ratio')}</option>
                  </select>
                  {amountMatch === 'tolerance' && (
                    <div className="flex items-center gap-1 shrink-0">
                      <input
                        type="number"
                        min={0}
                        max={100}
                        step="0.5"
                        className="w-20 px-3 py-2 text-sm border border-input rounded-md bg-background"
                        value={when.amount?.percent ?? '2'}
                        onChange={(e) =>
                          setWhen({
                            ...when,
                            amount: {
                              ...(when.amount ?? { match: 'tolerance' }),
                              match: 'tolerance',
                              percent: e.target.value,
                            },
                          })
                        }
                      />
                      <span className="text-sm text-muted-foreground">%</span>
                    </div>
                  )}
                </div>
                {amountMatch === 'partial' && (
                  <p className="text-xs text-muted-foreground mt-1">
                    {t('reconciliation.amount.partialHint')}
                  </p>
                )}
                {amountMatch === 'set' && (
                  <div className="mt-1.5 space-y-1.5">
                    <p className="text-xs text-muted-foreground">
                      {t('reconciliation.amount.setHint')}
                    </p>
                    <div className="flex gap-2 items-end">
                      <label className="flex-1">
                        <span className="text-xs text-muted-foreground">
                          {t('reconciliation.amount.setMax')}
                        </span>
                        <input
                          type="number"
                          min={2}
                          max={6}
                          className={`${inputClass} mt-0.5`}
                          value={when.amount?.max_invoices ?? 6}
                          onChange={(e) =>
                            setWhen({
                              ...when,
                              amount: {
                                ...(when.amount ?? { match: 'set' }),
                                match: 'set',
                                max_invoices: Number(e.target.value),
                              },
                            })
                          }
                        />
                      </label>
                      <label className="flex-1">
                        <span className="text-xs text-muted-foreground">
                          {t('reconciliation.amount.setFee')}
                        </span>
                        <div className="flex items-center gap-1 mt-0.5">
                          <input
                            type="number"
                            min={0}
                            max={20}
                            step="0.5"
                            className={inputClass}
                            value={when.amount?.percent ?? '0'}
                            onChange={(e) =>
                              setWhen({
                                ...when,
                                amount: {
                                  ...(when.amount ?? { match: 'set' }),
                                  match: 'set',
                                  percent: e.target.value,
                                },
                              })
                            }
                          />
                          <span className="text-sm text-muted-foreground">%</span>
                        </div>
                      </label>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {t('reconciliation.amount.setFeeHint')}
                    </p>
                  </div>
                )}
                {amountMatch === 'ratio' && (
                  <p className="text-xs text-muted-foreground mt-1">
                    {t('reconciliation.amount.ratioHint')}
                  </p>
                )}
              </>,
            )}

            {field(
              t('reconciliation.field.window'),
              t('reconciliation.field.windowHint'),
              <div className="flex gap-2">
                <div className="flex-1">
                  <span className="text-xs text-muted-foreground">
                    {t('reconciliation.field.beforeDays')}
                  </span>
                  <input
                    type="number"
                    min={0}
                    max={365}
                    className={`${inputClass} mt-0.5`}
                    value={when.date?.before_days ?? 0}
                    onChange={(e) =>
                      setWhen({
                        ...when,
                        date: {
                          before_days: Number(e.target.value),
                          after_days: when.date?.after_days ?? 0,
                        },
                      })
                    }
                  />
                </div>
                <div className="flex-1">
                  <span className="text-xs text-muted-foreground">
                    {t('reconciliation.field.afterDays')}
                  </span>
                  <input
                    type="number"
                    min={0}
                    max={365}
                    className={`${inputClass} mt-0.5`}
                    value={when.date?.after_days ?? 0}
                    onChange={(e) =>
                      setWhen({
                        ...when,
                        date: {
                          before_days: when.date?.before_days ?? 0,
                          after_days: Number(e.target.value),
                        },
                      })
                    }
                  />
                </div>
              </div>,
            )}

            {field(
              t('reconciliation.field.similarity'),
              t('reconciliation.field.similarityHint'),
              <input
                type="number"
                min={0}
                max={1}
                step="0.05"
                className="w-28 px-3 py-2 text-sm border border-input rounded-md bg-background"
                value={when.description_similarity?.min ?? ''}
                placeholder={t('reconciliation.field.similarityOff')}
                onChange={(e) =>
                  setWhen({
                    ...when,
                    description_similarity: e.target.value
                      ? { min: e.target.value }
                      : undefined,
                  })
                }
              />,
            )}
          </Step>
        </div>

        <div className="flex justify-end gap-2 pt-2 shrink-0 border-t border-border mt-2">
          <Button variant="outline" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button onClick={() => save.mutate()} disabled={save.isPending}>
            {t('common.save')}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

export function ReconciliationRules({ canWrite }: { canWrite: boolean }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<{ node: string; rule: ReconciliationRule | null } | null>(
    null,
  )

  const { data: nodes } = useQuery<ReconciliationNode[]>({
    queryKey: ['reconciliation-rules'],
    queryFn: reconciliationApi.rules,
  })
  // Loaded once for the whole list rather than per row: a rule naming
  // three accounts should read as their names, not their ids.
  const { data: accountList = [] } = useQuery<Account[]>({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })
  const { data: payeeList = [] } = useQuery<Payee[]>({
    queryKey: ['payees'],
    queryFn: () => payeesApi.list(),
  })
  const names = { accounts: accountList, payees: payeeList }

  const toggle = useMutation({
    mutationFn: ({ node, rule }: { node: string; rule: ReconciliationRule }) =>
      reconciliationApi.updateRule(node, rule.id, { enabled: !rule.enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['reconciliation-rules'] }),
    onError: (error) => toast.error(extractApiError(error, t('common.error'))),
  })

  // Moving one rule sends the whole order, because that is what the API
  // takes: an order where some rules are placed and others fall back to
  // where we shipped them reads correctly today and rearranges itself the
  // day a default is inserted.
  const move = useMutation({
    mutationFn: ({ node, ids }: { node: string; ids: string[] }) =>
      reconciliationApi.reorderRules(node, ids),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['reconciliation-rules'] }),
    onError: (error) => toast.error(extractApiError(error, t('common.error'))),
  })

  const reset = useMutation({
    mutationFn: ({ node, id }: { node: string; id: string }) =>
      reconciliationApi.resetRule(node, id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['reconciliation-rules'] })
      toast.success(t('reconciliation.reset'))
    },
    onError: (error) => toast.error(extractApiError(error, t('common.error'))),
  })

  if (!nodes) return null

  return (
    <>
      {nodes.map((group) => (
        <SectionCard key={group.node}>
          <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-start justify-between gap-2">
            <div>
              <p className="text-sm font-semibold text-foreground">
                {t(NODE_TITLE[group.node] ?? group.node)}
                {!group.active && (
                  <span className="ml-2 text-[10px] font-semibold bg-muted text-muted-foreground px-1.5 py-0.5 rounded-full">
                    {t('reconciliation.node.inactive')}
                  </span>
                )}
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {t(NODE_HINT[group.node] ?? '')}
              </p>
            </div>
            {canWrite && (
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5 h-8"
                onClick={() => setEditing({ node: group.node, rule: null })}
              >
                <Plus size={13} />
                <span className="hidden sm:inline">{t('reconciliation.add')}</span>
              </Button>
            )}
          </div>

          <div className="divide-y divide-border">
            {group.rules.map((rule, index) => (
              <div
                key={rule.id}
                className={cn(
                  'px-4 sm:px-5 py-3 hover:bg-muted transition-colors',
                  canWrite && 'cursor-pointer',
                  !rule.enabled && 'opacity-55',
                )}
                onClick={() => { if (canWrite) setEditing({ node: group.node, rule }) }}
              >
                <div className="flex items-start justify-between gap-4">
                  {/* The position, shown because it *is* the mechanism: the
                      first rule that matches wins, so a band like "link
                      under 2%, ask between 2 and 5" is one rule placed
                      above another with no lower bound written anywhere.
                      An order you cannot see is a rule you cannot reason
                      about. */}
                  <div className="flex flex-col items-center shrink-0 pt-0.5">
                    <span className="text-xs font-semibold text-muted-foreground tabular-nums w-5 text-center">
                      {index + 1}
                    </span>
                    {canWrite && group.rules.length > 1 && (
                      <div
                        className="flex flex-col -space-y-1 mt-0.5"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <button
                          className="text-muted-foreground hover:text-foreground disabled:opacity-25 disabled:hover:text-muted-foreground"
                          disabled={index === 0 || move.isPending}
                          title={t('reconciliation.moveUp')}
                          onClick={() =>
                            move.mutate({ node: group.node, ids: swap(group.rules, index, index - 1) })
                          }
                        >
                          <ChevronUp size={13} />
                        </button>
                        <button
                          className="text-muted-foreground hover:text-foreground disabled:opacity-25 disabled:hover:text-muted-foreground"
                          disabled={index === group.rules.length - 1 || move.isPending}
                          title={t('reconciliation.moveDown')}
                          onClick={() =>
                            move.mutate({ node: group.node, ids: swap(group.rules, index, index + 1) })
                          }
                        >
                          <ChevronDown size={13} />
                        </button>
                      </div>
                    )}
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      <p className="text-sm font-semibold text-foreground">
                        {rule.name || t(SHIPPED_NAME[rule.id] ?? 'reconciliation.rule.unknown')}
                      </p>
                      <OutcomeBadge outcome={rule.outcome} />
                      {rule.customised && (
                        <span className="text-[10px] font-semibold bg-sky-50 text-sky-700 dark:bg-sky-950 dark:text-sky-300 px-1.5 py-0.5 rounded-full">
                          {t(
                            rule.origin === 'custom'
                              ? 'reconciliation.yours'
                              : 'reconciliation.changed',
                          )}
                        </span>
                      )}
                      {!rule.enabled && (
                        <span className="text-[10px] font-semibold bg-muted text-muted-foreground px-1.5 py-0.5 rounded-full">
                          {t('rules.inactive')}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground font-mono truncate">
                      {conditionSummary({ ...rule.when, trigger: rule.trigger }, t, names)}
                    </p>
                  </div>

                  {canWrite && (
                    <div
                      className="flex items-center gap-1 shrink-0"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        className="px-2 py-1 rounded-md text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-background transition-colors"
                        onClick={() => toggle.mutate({ node: group.node, rule })}
                        disabled={toggle.isPending}
                      >
                        {t(rule.enabled ? 'reconciliation.turnOff' : 'reconciliation.turnOn')}
                      </button>
                      {rule.customised && (
                        <button
                          className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-background transition-colors"
                          title={t(
                            rule.origin === 'custom'
                              ? 'common.delete'
                              : 'reconciliation.restore',
                          )}
                          onClick={() => reset.mutate({ node: group.node, id: rule.id })}
                          disabled={reset.isPending}
                        >
                          {rule.origin === 'custom' ? (
                            <Trash2 size={13} />
                          ) : (
                            <RotateCcw size={13} />
                          )}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
      ))}

      <RuleEditor
        open={editing !== null}
        node={editing?.node ?? ''}
        rule={editing?.rule ?? null}
        onClose={() => setEditing(null)}
      />
    </>
  )
}
