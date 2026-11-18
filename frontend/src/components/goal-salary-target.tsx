import { useTranslation } from 'react-i18next'
import { formatCurrency } from '@/lib/format'
import { SALARY_MONTHS, monthlySalary, salaryTarget } from '@/lib/goal-salary'
import type { GoalSalaryTarget } from '@/hooks/use-goal-salary-target'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Label } from '@/components/ui/label'
import type { Goal } from '@/types'

const SELECT_CLASS =
  'w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary'

function Segmented<T extends string | number>({
  value,
  options,
  onChange,
}: {
  value: T
  options: { value: T; label: string }[]
  onChange: (value: T) => void
}) {
  return (
    <div className="flex items-center gap-2">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
            value === option.value
              ? 'bg-primary text-primary-foreground'
              : 'bg-muted text-muted-foreground hover:text-foreground'
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/** The target mode switch and, in salary mode, the salary fields. The form shows its fixed fields only in fixed mode. */
export function GoalTargetFields({ salary }: { salary: GoalSalaryTarget }) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const locale = useDisplayLocale()
  const { selected, months } = salary

  return (
    <>
      <div className="space-y-2">
        <Label>{t('goals.targetMode')}</Label>
        <Segmented
          value={salary.mode}
          onChange={salary.setMode}
          options={[
            { value: 'fixed', label: t('goals.targetModeFixed') },
            { value: 'salary', label: t('goals.targetModeSalary') },
          ]}
        />
      </div>

      {salary.mode === 'salary' &&
        (selected ? (
          <>
            <div className="space-y-2">
              <Label htmlFor="goal-salary-source">{t('goals.salarySource')}</Label>
              <select
                id="goal-salary-source"
                value={selected.id}
                onChange={(e) => salary.setSalaryRecurringId(e.target.value)}
                className={SELECT_CLASS}
              >
                {salary.options.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.description} · {mask(formatCurrency(Number(r.amount), r.currency, locale))}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <Label>{t('goals.salaryMonths')}</Label>
              <Segmented
                value={months}
                onChange={salary.setMonths}
                options={SALARY_MONTHS.map((m) => ({ value: m, label: String(m) }))}
              />
              <p className="text-xs text-muted-foreground tabular-nums">
                {months} × {mask(formatCurrency(monthlySalary(selected), selected.currency, locale))}
                {' = '}
                <span className="font-semibold text-foreground">
                  {mask(formatCurrency(salaryTarget(selected, months), selected.currency, locale))}
                </span>
              </p>
            </div>
          </>
        ) : (
          <p className="text-xs text-muted-foreground">{t('goals.salaryNoIncome')}</p>
        ))}
    </>
  )
}

/** How many salaries a salary goal targets and how many it has saved. */
export function GoalSalaryBadges({ goal }: { goal: Goal }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  if (goal.salary_months == null || goal.monthly_salary == null) return null

  return (
    <>
      <span>{t('goals.salaryTarget', { months: goal.salary_months })}</span>
      <span className="tabular-nums">
        {t('goals.salaryCoverage', {
          covered: (Number(goal.current_amount) / Number(goal.monthly_salary)).toLocaleString(locale, {
            minimumFractionDigits: 1,
            maximumFractionDigits: 1,
          }),
          months: goal.salary_months,
        })}
      </span>
    </>
  )
}
