import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Download, FileSpreadsheet } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { accountantExport } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { todayInTimezone } from '@/lib/date-utils'
import { useEffectiveTimezone } from '@/hooks/use-timezone'

/** First day of the current month, the usual window an accountant asks for. */
function monthStart(today: string): string {
  return `${today.slice(0, 7)}-01`
}

/**
 * The accountant package: a period, then one zip with the categorized
 * transactions and the profit and loss of the same period.
 */
export function AccountantExportSection() {
  const { t } = useTranslation()
  const today = todayInTimezone(useEffectiveTimezone())
  const [startDate, setStartDate] = useState(() => monthStart(today))
  const [endDate, setEndDate] = useState(today)
  const [downloading, setDownloading] = useState(false)

  const rangeValid = startDate !== '' && endDate !== '' && endDate >= startDate

  const download = async () => {
    if (!rangeValid) return
    setDownloading(true)
    try {
      await accountantExport.download(startDate, endDate)
    } catch (error) {
      toast.error(extractApiError(error, t('accountantExport.error')))
    } finally {
      setDownloading(false)
    }
  }

  return (
    <section className="space-y-4 rounded-xl border bg-card p-6">
      <div className="flex items-center gap-2">
        <FileSpreadsheet className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-base font-semibold">{t('accountantExport.title')}</h2>
      </div>
      <p className="text-sm text-muted-foreground">{t('accountantExport.description')}</p>
      <div className="flex flex-wrap items-end gap-4">
        <div className="space-y-1.5">
          <Label htmlFor="accountant-start" className="text-[13px]">
            {t('accountantExport.startDate')}
          </Label>
          <Input
            id="accountant-start"
            type="date"
            value={startDate}
            max={endDate || today}
            onChange={(event) => setStartDate(event.target.value)}
            className="h-10 rounded-lg"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="accountant-end" className="text-[13px]">
            {t('accountantExport.endDate')}
          </Label>
          <Input
            id="accountant-end"
            type="date"
            value={endDate}
            min={startDate || undefined}
            max={today}
            onChange={(event) => setEndDate(event.target.value)}
            className="h-10 rounded-lg"
          />
        </div>
        <Button
          onClick={download}
          disabled={!rangeValid || downloading}
          className="rounded-lg"
          size="sm"
        >
          <Download className="me-2 h-4 w-4" />
          {downloading ? t('common.loading') : t('accountantExport.download')}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{t('accountantExport.formatHint')}</p>
    </section>
  )
}
