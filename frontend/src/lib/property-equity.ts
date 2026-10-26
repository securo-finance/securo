import type { Account, Asset } from '@/types'
import { getAssetValuePrimary } from '@/lib/asset-portfolio-share'

/** Asset types a loan can be secured against (a mortgage, a car financing). */
export const LOAN_COLLATERAL_TYPES: readonly string[] = ['real_estate', 'vehicle']

type ValuedProperty = Pick<Asset, 'id' | 'currency' | 'current_value' | 'current_value_primary'>
type SecuredLoan = Pick<Account, 'type' | 'secured_asset_id' | 'current_balance' | 'balance_primary'>

/** Loan debt in the user's primary currency. An overpaid loan owes nothing. */
export function getLoanDebtPrimary(loan: Pick<Account, 'current_balance' | 'balance_primary'>): number {
  return Math.max(0, -Number(loan.balance_primary ?? loan.current_balance))
}

/** Value, linked debt and equity of a property, in the user's primary currency. */
export function getPropertyEquity(
  property: ValuedProperty,
  accounts: SecuredLoan[],
  displayCurrency: string,
): { value: number; debt: number; equity: number } | null {
  const value = getAssetValuePrimary(property, displayCurrency)
  if (value == null) return null
  const debt = accounts
    .filter(account => account.type === 'loan' && account.secured_asset_id === property.id)
    .reduce((total, loan) => total + getLoanDebtPrimary(loan), 0)
  return { value, debt, equity: value - debt }
}
