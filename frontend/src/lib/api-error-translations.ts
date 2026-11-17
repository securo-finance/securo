// The app configures the default i18next instance (see lib/i18n.ts); reading
// it here avoids importing that module and its browser-only setup.
import i18n from 'i18next'

/**
 * Backend messages the user can trigger from the UI, mapped to translation
 * keys. The API answers in English; without this the toast would too.
 * Matching is on the exact message, so a backend wording change falls back
 * to the English text instead of showing the wrong translation.
 */
const KNOWN_MESSAGES: Record<string, string> = {
  'Cannot change amount or date on a synchronized transaction': 'transactions.syncedAmountDateError',
  'A category split needs at least two lines': 'categoryLines.errors.tooFewLines',
  'Each category can appear only once in a split': 'categoryLines.errors.duplicateCategory',
  'Each line needs an amount': 'categoryLines.errors.invalidLines',
  'Each line amount must be positive': 'categoryLines.errors.invalidLines',
  'Each line needs a percentage': 'categoryLines.errors.invalidLines',
  'Each line percentage must be positive': 'categoryLines.errors.invalidLines',
  'The lines must add up to the transaction amount': 'categoryLines.errors.amountMismatch',
  'The line percentages must add up to 100': 'categoryLines.errors.percentMismatch',
  'The transaction amount is too small to split this way': 'categoryLines.errors.tooSmall',
  'An ignored category cannot be used in a category split': 'categoryLines.errors.ignoredCategory',
  'A hidden category cannot be used in a category split': 'categoryLines.errors.hiddenCategory',
  'A credit on a credit card cannot have a line in a transfer category': 'categoryLines.errors.cardCreditTransfer',
  'A transfer cannot be split across categories': 'categoryLines.errors.transfer',
  'An ignored transaction cannot be split across categories': 'categoryLines.errors.ignoredTransaction',
  'This transaction cannot be split across categories': 'categoryLines.errors.notSplittable',
  'A card statement charge cannot be split across categories': 'categoryLines.errors.notSplittable',
  'A transaction with no amount cannot be split across categories': 'categoryLines.errors.noAmount',
  'Remove the split among people before splitting across categories': 'categoryLines.errors.hasGroupSplit',
  'Remove the mortgage breakdown before splitting across categories': 'categoryLines.errors.hasMortgage',
  'An installment series cannot be split across categories': 'categoryLines.errors.installments',
  'An installment in this series is split across categories; edit it on its own': 'categoryLines.errors.installmentScope',
  'Choose either a category or a split across categories': 'categoryLines.errors.categoryAndLines',
  'Undo the split across categories before choosing a single category': 'categoryLines.errors.undoFirstCategory',
  'Send the category lines along with the new amount': 'categoryLines.errors.amountNeedsLines',
  'Undo the split across categories before linking it as a transfer': 'categoryLines.errors.undoFirstTransfer',
  'Undo the split across categories before splitting among people': 'categoryLines.errors.undoFirstGroupSplit',
  'Undo the split across categories before adding a mortgage breakdown': 'categoryLines.errors.undoFirstMortgage',
  'This category is used in transactions split across categories. Change those splits first.':
    'categoryLines.errors.categoryInUse',
  'Transactions split across categories cannot move into an ignored or hidden category.':
    'categoryLines.errors.destinationInvalid',
}

export function translateApiMessage(message: string): string {
  const key = KNOWN_MESSAGES[message]
  return key && i18n.isInitialized && i18n.exists(key) ? i18n.t(key) : message
}
