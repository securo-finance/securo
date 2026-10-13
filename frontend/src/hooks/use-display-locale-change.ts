import { useState } from 'react'

/**
 * Call `onChange(previous, next)` during render when the display locale
 * changes, so a form can re-render its amount text for the new separators
 * with `convertAmountInput`. The locale can change while a form is open: it
 * falls back to the UI language until the number-format setting loads.
 */
export function useDisplayLocaleChange(
  locale: string,
  onChange: (previous: string, next: string) => void,
): void {
  const [seen, setSeen] = useState(locale)
  if (seen !== locale) {
    setSeen(locale)
    onChange(seen, locale)
  }
}
