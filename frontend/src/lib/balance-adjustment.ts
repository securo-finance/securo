export type BalanceAdjustmentPreview = {
  currentInputValue: number
  targetSignedBalance: number
  adjustmentAmount: number
}

function roundCurrency(value: number): number {
  return Math.round((value + Number.EPSILON) * 100) / 100
}

/** Translate user-facing target semantics into Securo's signed ledger balance. */
export function getBalanceAdjustmentPreview(
  accountType: string,
  currentSignedBalance: number,
  targetInput: number,
): BalanceAdjustmentPreview {
  const isCreditCard = accountType === 'credit_card'
  const targetSignedBalance = isCreditCard && targetInput !== 0 ? -targetInput : targetInput
  return {
    currentInputValue: isCreditCard ? Math.max(0, -currentSignedBalance) : currentSignedBalance,
    targetSignedBalance,
    adjustmentAmount: roundCurrency(targetSignedBalance - currentSignedBalance),
  }
}
