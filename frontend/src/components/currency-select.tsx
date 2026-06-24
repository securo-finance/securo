import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

// Shared across the setup and register flows. A compact dropdown keeps these
// forms short instead of rendering one button per currency in a tall grid.
export const CURRENCIES = [
  { code: 'USD', flag: '\u{1F1FA}\u{1F1F8}', symbol: '$' },
  { code: 'EUR', flag: '\u{1F1EA}\u{1F1FA}', symbol: '€' },
  { code: 'GBP', flag: '\u{1F1EC}\u{1F1E7}', symbol: '£' },
  { code: 'BRL', flag: '\u{1F1E7}\u{1F1F7}', symbol: 'R$' },
  { code: 'CAD', flag: '\u{1F1E8}\u{1F1E6}', symbol: 'C$' },
  { code: 'AUD', flag: '\u{1F1E6}\u{1F1FA}', symbol: 'A$' },
  { code: 'CHF', flag: '\u{1F1E8}\u{1F1ED}', symbol: 'Fr' },
  { code: 'ARS', flag: '\u{1F1E6}\u{1F1F7}', symbol: '$' },
  { code: 'DKK', flag: '\u{1F1E9}\u{1F1F0}', symbol: 'kr' },
  { code: 'NOK', flag: '\u{1F1F3}\u{1F1F4}', symbol: 'kr' },
  { code: 'PLN', flag: '\u{1F1F5}\u{1F1F1}', symbol: 'zł' },
  { code: 'CZK', flag: '\u{1F1E8}\u{1F1FF}', symbol: 'Kč' },
  { code: 'HUF', flag: '\u{1F1ED}\u{1F1FA}', symbol: 'Ft' },
  { code: 'RON', flag: '\u{1F1F7}\u{1F1F4}', symbol: 'lei' },
  { code: 'CRC', flag: '\u{1F1E8}\u{1F1F7}', symbol: '₡' },
  { code: 'IDR', flag: '\u{1F1EE}\u{1F1E9}', symbol: 'Rp' },
  { code: 'COP', flag: '\u{1F1E8}\u{1F1F4}', symbol: '$' },
  { code: 'CLP', flag: '\u{1F1E8}\u{1F1F1}', symbol: '$' },
  { code: 'DOP', flag: '\u{1F1E9}\u{1F1F4}', symbol: 'RD$' },
  { code: 'RUB', flag: '\u{1F1F7}\u{1F1FA}', symbol: '₽' },
] as const

interface CurrencySelectProps {
  value: string
  onChange: (code: string) => void
  id?: string
  className?: string
}

export function CurrencySelect({ value, onChange, id, className }: CurrencySelectProps) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger id={id} className={cn('w-full', className)}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {CURRENCIES.map(({ code, flag, symbol }) => (
          <SelectItem key={code} value={code}>
            <span className="text-base leading-none">{flag}</span>
            <span className="font-medium">{code}</span>
            <span className="text-muted-foreground text-xs">{symbol}</span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
