import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronUp } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import {
  DEFAULT_LAYOUT,
  WIDGETS,
  moveWidget,
  type DashboardLayout,
  type WidgetId,
} from '@/components/dashboard/widgets'

interface CustomizeDashboardDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  layout: DashboardLayout
  onSave: (layout: DashboardLayout) => void
}

export function CustomizeDashboardDialog({ open, onOpenChange, layout, onSave }: CustomizeDashboardDialogProps) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<DashboardLayout>(layout)
  const [prevOpen, setPrevOpen] = useState(open)

  // Start every opening from the saved layout, dropping unsaved edits.
  if (open !== prevOpen) {
    setPrevOpen(open)
    if (open) setDraft(layout)
  }

  const toggle = (id: WidgetId, visible: boolean) => {
    setDraft((d) => ({
      ...d,
      hidden: visible ? d.hidden.filter((h) => h !== id) : [...d.hidden, id],
    }))
  }

  const move = (id: WidgetId, delta: -1 | 1) => {
    setDraft((d) => ({ ...d, order: moveWidget(d.order, id, delta) }))
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('dashboard.customize.title')}</DialogTitle>
          <DialogDescription>{t('dashboard.customize.description')}</DialogDescription>
        </DialogHeader>

        <ul className="divide-y divide-border rounded-lg border border-border">
          {draft.order.map((id, index) => {
            const label = t(WIDGETS[id].labelKey)
            const labelId = `dashboard-widget-${id}`
            const visible = !draft.hidden.includes(id)
            return (
              <li key={id} className="flex items-center gap-3 px-3 py-2">
                <Switch
                  checked={visible}
                  onCheckedChange={(checked) => toggle(id, checked)}
                  aria-labelledby={labelId}
                />
                <span
                  id={labelId}
                  className={`flex-1 text-sm ${visible ? 'text-foreground' : 'text-muted-foreground'}`}
                >
                  {label}
                </span>
                <div className="flex items-center gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    disabled={index === 0}
                    aria-label={t('dashboard.customize.moveUp', { widget: label })}
                    onClick={() => move(id, -1)}
                  >
                    <ChevronUp className="size-4" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    disabled={index === draft.order.length - 1}
                    aria-label={t('dashboard.customize.moveDown', { widget: label })}
                    onClick={() => move(id, 1)}
                  >
                    <ChevronDown className="size-4" />
                  </Button>
                </div>
              </li>
            )
          })}
        </ul>

        <DialogFooter className="sm:justify-between">
          <Button
            type="button"
            variant="ghost"
            onClick={() => setDraft({ order: [...DEFAULT_LAYOUT.order], hidden: [] })}
          >
            {t('dashboard.customize.reset')}
          </Button>
          <div className="flex flex-col-reverse gap-2 sm:flex-row">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              type="button"
              onClick={() => {
                onSave(draft)
                onOpenChange(false)
              }}
            >
              {t('common.save')}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
