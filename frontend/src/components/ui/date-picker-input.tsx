import { useId, useState } from 'react'
import { format } from 'date-fns'
import { CalendarIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useDateLocale } from '@/hooks/use-display-locale'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { Calendar } from '@/components/ui/calendar'
import { resolveDateFnsLocale } from '@/lib/date-fns-locale'
import { cn } from '@/lib/utils'

interface DatePickerInputProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
  disabled?: boolean
  align?: 'start' | 'center' | 'end'
  /** The id of the trigger button, so a `<Label htmlFor>` names this field. */
  id?: string
  /**
   * The id of the label naming this field. The trigger is named after it and
   * the date it shows, so a screen reader announces both on focus.
   */
  labelledBy?: string
}

function DatePickerInput({
  value,
  onChange,
  placeholder,
  className,
  disabled,
  align = 'start',
  id,
  labelledBy,
}: DatePickerInputProps) {
  const valueId = useId()
  const { i18n } = useTranslation()
  const [open, setOpen] = useState(false)
  const dateFnsLocale = resolveDateFnsLocale(i18n.resolvedLanguage ?? i18n.language)
  const dateLocale = useDateLocale()

  const selectedDate = value ? new Date(value + 'T00:00:00') : undefined
  const displayText = selectedDate
    ? selectedDate.toLocaleDateString(dateLocale)
    : placeholder || 'dd/mm/yyyy'

  return (
    <Popover open={open} onOpenChange={disabled ? undefined : setOpen}>
      <PopoverTrigger asChild>
        <button
          id={id}
          type="button"
          disabled={disabled}
          aria-labelledby={labelledBy ? `${labelledBy} ${valueId}` : undefined}
          className={cn(
            'inline-flex items-center gap-2 border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground hover:bg-muted/50 transition-colors cursor-pointer disabled:opacity-50 disabled:pointer-events-none min-w-[120px]',
            !value && 'text-muted-foreground',
            className,
          )}
        >
          <CalendarIcon className="size-3.5 text-muted-foreground shrink-0" />
          <span id={valueId}>{displayText}</span>
        </button>
      </PopoverTrigger>
      <PopoverContent align={align} className="w-auto p-0">
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
      </PopoverContent>
    </Popover>
  )
}

export { DatePickerInput }
