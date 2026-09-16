import type { ElementType } from 'react'
import { Building2, PiggyBank, CreditCard, TrendingUp, Wallet } from 'lucide-react'

// Account-type → icon/color, the fallback shown when an account has no bank
// logo (manual accounts, and connected accounts whose provider exposes none).
export const ACCOUNT_TYPE_CONFIG: Record<
  string,
  { icon: ElementType; color: string; bg: string; label: string }
> = {
  checking:    { icon: Building2,   color: 'text-indigo-600',  bg: 'bg-indigo-100',  label: 'accounts.typeChecking' },
  savings:     { icon: PiggyBank,   color: 'text-emerald-600', bg: 'bg-emerald-100', label: 'accounts.typeSavings' },
  credit_card: { icon: CreditCard,  color: 'text-violet-600',  bg: 'bg-violet-100',  label: 'accounts.typeCreditCard' },
  investment:  { icon: TrendingUp,  color: 'text-amber-600',   bg: 'bg-amber-100',   label: 'accounts.typeInvestment' },
  wallet:      { icon: Wallet,      color: 'text-rose-600',    bg: 'bg-rose-100',    label: 'accounts.typeWallet' },
}

export function getAccountTypeConfig(type: string) {
  return ACCOUNT_TYPE_CONFIG[type] ?? ACCOUNT_TYPE_CONFIG['checking']
}
