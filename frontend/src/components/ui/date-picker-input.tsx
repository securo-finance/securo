import { useEffect, useMemo, useState } from 'react'
import { format } from 'date-fns'
import { ptBR, enUS } from 'date-fns/locale'
import { CalendarIcon, ChevronLeftIcon, ChevronRightIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { Calendar } from '@/components/ui/calendar'
import { cn } from '@/lib/utils'

interface DatePickerInputProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
  disabled?: boolean
  align?: 'start' | 'center' | 'end'
  mode?: 'date' | 'month'
}

function DatePickerInput({
  value,
  onChange,
  placeholder,
  className,
  disabled,
  align = 'start',
  mode = 'date',
}: DatePickerInputProps) {
  const { i18n } = useTranslation()
  const [open, setOpen] = useState(false)
  const dateFnsLocale = i18n.language === 'pt-BR' ? ptBR : enUS
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language
  const fallbackDate = mode === 'month' ? `${value || '2000-01'}-01` : value

  const selectedDate = value ? new Date(`${fallbackDate}T00:00:00`) : undefined
  const [viewMonth, setViewMonth] = useState(selectedDate ?? new Date())

  useEffect(() => {
    if (selectedDate) {
      setViewMonth(selectedDate)
    }
  }, [selectedDate?.getTime()])

  const monthOptions = useMemo(
    () =>
      Array.from({ length: 12 }, (_, index) => {
        const date = new Date(viewMonth.getFullYear(), index, 1)
        return {
          value: `${viewMonth.getFullYear()}-${String(index + 1).padStart(2, '0')}`,
          label: date.toLocaleDateString(locale, { month: 'long' }),
        }
      }),
    [locale, viewMonth],
  )

  const displayText = selectedDate
    ? selectedDate.toLocaleDateString(locale, mode === 'month' ? { month: 'long', year: 'numeric' } : undefined)
    : placeholder || (mode === 'month' ? 'mm/yyyy' : 'dd/mm/yyyy')

  return (
    <Popover open={open} onOpenChange={disabled ? undefined : setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          className={cn(
            'inline-flex items-center gap-2 border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground hover:bg-muted/50 transition-colors cursor-pointer disabled:opacity-50 disabled:pointer-events-none min-w-[120px]',
            !value && 'text-muted-foreground',
            className,
          )}
        >
          <CalendarIcon className="size-3.5 text-muted-foreground shrink-0" />
          {displayText}
        </button>
      </PopoverTrigger>
      <PopoverContent align={align} className="w-auto p-0">
        {mode === 'month' ? (
          <div className="w-[280px] p-3">
            <div className="mb-3 flex items-center justify-between">
              <button
                type="button"
                onClick={() => setViewMonth(new Date(viewMonth.getFullYear() - 1, viewMonth.getMonth(), 1))}
                className="size-7 inline-flex items-center justify-center rounded-lg border border-border bg-transparent text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
              >
                <ChevronLeftIcon className="size-4" />
              </button>
              <span className="text-sm font-medium text-foreground">{viewMonth.getFullYear()}</span>
              <button
                type="button"
                onClick={() => setViewMonth(new Date(viewMonth.getFullYear() + 1, viewMonth.getMonth(), 1))}
                className="size-7 inline-flex items-center justify-center rounded-lg border border-border bg-transparent text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
              >
                <ChevronRightIcon className="size-4" />
              </button>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {monthOptions.map((month) => {
                const isSelected = value === month.value
                return (
                  <button
                    key={month.value}
                    type="button"
                    onClick={() => {
                      onChange(month.value)
                      setOpen(false)
                    }}
                    className={cn(
                      'rounded-lg border px-3 py-2 text-sm capitalize transition-colors',
                      isSelected
                        ? 'border-primary bg-primary text-primary-foreground'
                        : 'border-border bg-card text-foreground hover:bg-muted/50',
                    )}
                  >
                    {month.label}
                  </button>
                )
              })}
            </div>
          </div>
        ) : (
          <Calendar
            mode="single"
            locale={dateFnsLocale}
            selected={selectedDate}
            defaultMonth={selectedDate ?? new Date()}
            onSelect={(date) => {
              if (!date) return
              onChange(format(date, 'yyyy-MM-dd'))
              setOpen(false)
            }}
          />
        )}
      </PopoverContent>
    </Popover>
  )
}

export { DatePickerInput }
