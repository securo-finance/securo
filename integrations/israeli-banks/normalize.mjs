// Shape every scraper's output the same way for the Securo backend.
//
// The library returns each date as the instant of midnight in Asia/Jerusalem,
// serialized in UTC (e.g. 2026-11-09T22:00:00Z for 10/11). Securo wants the
// calendar date, so convert in Israel time rather than slicing the string.
export const israelDate = (iso) =>
  iso ? new Date(iso).toLocaleDateString('en-CA', { timeZone: 'Asia/Jerusalem' }) : null;

export function normalizeAccounts(accounts) {
  return accounts.map((acc) => ({
    accountNumber: String(acc.accountNumber),
    currency: acc.currency || 'ILS',
    balance: acc.balance ?? null,
    creditLimit: acc.cardFrame ?? null,
    savingsAccount: Boolean(acc.savingsAccount),
    txns: (acc.txns || []).map((t) => ({
      date: israelDate(t.date),
      processedDate: israelDate(t.processedDate),
      amount: t.chargedAmount,
      currency: t.chargedCurrency || t.originalCurrency || acc.currency || 'ILS',
      originalAmount: t.originalAmount ?? null,
      originalCurrency: t.originalCurrency || null,
      description: t.description || '',
      memo: t.memo || '',
      identifier: t.identifier == null ? null : String(t.identifier),
      status: t.status === 'pending' ? 'pending' : 'posted',
      installments: t.installments ? { number: t.installments.number, total: t.installments.total } : null,
    })),
  }));
}
