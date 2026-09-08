import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Download } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { InvoiceDocumentView } from '@/components/invoice-document'
import { publicInvoices } from '@/lib/api'

/**
 * An invoice someone was sent a link to.
 *
 * Outside the app shell entirely: no sidebar, no workspace, no session.
 * The recipient is a client, not a user, and the page shows them one
 * document and a way to save it. A bad or revoked token is a plain
 * not-found — the same answer a link that never existed gets, because
 * "this used to be here" is itself information.
 */
export default function SharedInvoicePage() {
  const { token = '' } = useParams()
  const { t } = useTranslation()

  const { data, isLoading, isError } = useQuery({
    queryKey: ['shared-invoice', token],
    queryFn: () => publicInvoices.get(token),
    enabled: Boolean(token),
    retry: false,
  })

  if (isLoading) {
    return (
      <div className="min-h-screen grid place-items-center text-sm text-muted-foreground">
        {t('common.loading')}
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="min-h-screen grid place-items-center px-6">
        <div className="text-center space-y-2">
          <h1 className="text-lg font-semibold">{t('invoices.shared.notFoundTitle')}</h1>
          <p className="text-sm text-muted-foreground max-w-sm">
            {t('invoices.shared.notFoundBody')}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background py-8 px-4">
      <div className="mx-auto max-w-3xl space-y-3">
        <div className="flex justify-end">
          <Button asChild size="sm" variant="outline" data-testid="shared-download">
            <a href={publicInvoices.pdfUrl(token)} target="_blank" rel="noopener noreferrer">
              <Download className="h-4 w-4 mr-1.5" />
              {t('invoices.action.downloadPdf')}
            </a>
          </Button>
        </div>
        <InvoiceDocumentView document={data} />
      </div>
    </div>
  )
}
