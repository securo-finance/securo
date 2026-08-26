import { useTranslation } from 'react-i18next'
import { Receipt } from 'lucide-react'

/**
 * Placeholder until the invoices ledger lands. Deliberately honest
 * about being empty rather than showing a fake table: the nav entry
 * exists so the module boundary is real and testable.
 */
export default function InvoicesPage() {
  const { t } = useTranslation()

  return (
    <div className="container max-w-5xl py-8">
      <div className="rounded-xl border bg-card p-10 flex flex-col items-center text-center gap-3">
        <div className="h-12 w-12 rounded-xl bg-primary/10 flex items-center justify-center">
          <Receipt className="h-6 w-6 text-primary" />
        </div>
        <h1 className="text-xl font-semibold">
          {t('invoices.title', 'Invoices')}
        </h1>
        <p className="text-sm text-muted-foreground max-w-md leading-relaxed">
          {t(
            'invoices.comingNext',
            'Bill your clients and keep track of what has been paid and what is still open. This is the next thing being built.',
          )}
        </p>
      </div>
    </div>
  )
}
