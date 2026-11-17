import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { CURRENCIES } from '@/lib/currencies'
import { cn } from '@/lib/utils'

// Shared across the setup and register flows. A compact dropdown keeps these
// forms short instead of rendering one button per currency in a tall grid.
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
