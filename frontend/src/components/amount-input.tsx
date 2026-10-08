import * as React from 'react'
import { Input } from '@/components/ui/input'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { useDisplayLocaleChange } from '@/hooks/use-display-locale-change'
import { convertAmountInput, parseAmountInput } from '@/lib/format'

type AmountInputProps = Omit<React.ComponentProps<typeof Input>, 'type' | 'inputMode'>

/**
 * Text input for a money amount, typed under the display locale's separators.
 *
 * A `type="number"` input only takes the browser's own decimal mark, so it
 * rejects "12,34" on comma-decimal formats (issue #1072). Keep the value as
 * the typed string, read it back with `parseAmountInput(value, locale)` and
 * seed it from a stored number with `formatAmountInput`. Input that doesn't
 * parse is flagged `aria-invalid` so the field shows it before saving.
 */
export function AmountInput({ value, ...props }: AmountInputProps) {
  const locale = useDisplayLocale()
  const text = value == null ? '' : String(value)
  const invalid = text.trim() !== '' && parseAmountInput(text, locale) == null
  return (
    <Input
      type="text"
      inputMode="decimal"
      autoComplete="off"
      value={value}
      aria-invalid={invalid || undefined}
      {...props}
    />
  )
}

type FormAmountInputProps = Omit<AmountInputProps, 'value' | 'onChange'> & {
  defaultValue?: string
}

/**
 * `AmountInput` for forms read with `FormData`: holds its own text, seeded
 * from `defaultValue`, and re-renders it for the new separators (keeping its
 * value) when the display locale resolves. The form doesn't need to remount
 * for that, so nothing else the user typed is lost.
 */
export function FormAmountInput({ defaultValue = '', ...props }: FormAmountInputProps) {
  const locale = useDisplayLocale()
  const [value, setValue] = React.useState(defaultValue)
  useDisplayLocaleChange(locale, (prev, next) => setValue((v) => convertAmountInput(v, prev, next)))
  return <AmountInput {...props} value={value} onChange={(e) => setValue(e.target.value)} />
}
