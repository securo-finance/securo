import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'

interface TourStep {
  target: string
  titleKey: string
  descKey: string
}

const STEPS: TourStep[] = [
  { target: '[data-tour="sidebar"]', titleKey: 'onboarding.sidebar', descKey: 'onboarding.sidebarDesc' },
  { target: '[data-tour="nav-dashboard"]', titleKey: 'onboarding.dashboard', descKey: 'onboarding.dashboardDesc' },
  { target: '[data-tour="nav-accounts"]', titleKey: 'onboarding.accounts', descKey: 'onboarding.accountsDesc' },
  { target: '[data-tour="nav-transactions"]', titleKey: 'onboarding.transactions', descKey: 'onboarding.transactionsDesc' },
  { target: '[data-tour="nav-import"]', titleKey: 'onboarding.import', descKey: 'onboarding.importDesc' },
  { target: '[data-tour="nav-categories"]', titleKey: 'onboarding.categories', descKey: 'onboarding.categoriesDesc' },
]

interface Props {
  onComplete: () => void
}

export function OnboardingTour({ onComplete }: Props) {
  const { t } = useTranslation()
  const [current, setCurrent] = useState(0)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)

  const updateRect = useCallback(() => {
    const step = STEPS[current]
    const el = document.querySelector(step.target)
    if (el) {
      setRect(el.getBoundingClientRect())
    }
  }, [current])

  useEffect(() => {
    // Small delay to let the sidebar render
    const timer = setTimeout(updateRect, 100)
    window.addEventListener('resize', updateRect)
    window.addEventListener('scroll', updateRect, true)
    return () => {
      clearTimeout(timer)
      window.removeEventListener('resize', updateRect)
      window.removeEventListener('scroll', updateRect, true)
    }
  }, [updateRect])

  const next = () => {
    if (current < STEPS.length - 1) {
      setCurrent(current + 1)
    } else {
      onComplete()
    }
  }

  const back = () => {
    if (current > 0) {
      setCurrent(current - 1)
    }
  }

  const step = STEPS[current]
  const pad = 6

  // Tooltip position: to the right of the target element. A fixed width
  // (never wider than the viewport) so the footer wraps inside the card
  // instead of pushing a long label past its edge (issue #1036).
  const tooltipWidth = Math.min(320, window.innerWidth - 16)
  const tooltipStyle: React.CSSProperties = rect
    ? {
        position: 'fixed',
        top: Math.max(8, Math.min(rect.top, window.innerHeight - 220)),
        left: Math.max(8, rect.right + 16),
        zIndex: 10001,
        width: tooltipWidth,
      }
    : { display: 'none' }

  // If tooltip would go off screen right, put it below instead
  if (rect && rect.right + 16 + tooltipWidth > window.innerWidth) {
    tooltipStyle.left = Math.max(8, Math.min(rect.left, window.innerWidth - tooltipWidth - 8))
    tooltipStyle.top = rect.bottom + 12
  }

  return (
    <>
      {/* Overlay: 4 rectangles around the spotlight */}
      {rect && (
        <div className="fixed inset-0 z-[10000] pointer-events-none">
          {/* Top */}
          <div
            className="absolute bg-black/50 pointer-events-auto transition-all duration-300"
            style={{ top: 0, left: 0, right: 0, height: Math.max(0, rect.top - pad) }}
          />
          {/* Bottom */}
          <div
            className="absolute bg-black/50 pointer-events-auto transition-all duration-300"
            style={{ top: rect.bottom + pad, left: 0, right: 0, bottom: 0 }}
          />
          {/* Left */}
          <div
            className="absolute bg-black/50 pointer-events-auto transition-all duration-300"
            style={{
              top: Math.max(0, rect.top - pad),
              left: 0,
              width: Math.max(0, rect.left - pad),
              height: rect.height + pad * 2,
            }}
          />
          {/* Right */}
          <div
            className="absolute bg-black/50 pointer-events-auto transition-all duration-300"
            style={{
              top: Math.max(0, rect.top - pad),
              left: rect.right + pad,
              right: 0,
              height: rect.height + pad * 2,
            }}
          />
          {/* Spotlight border */}
          <div
            className="absolute rounded-lg ring-2 ring-primary/50 transition-all duration-300"
            style={{
              top: rect.top - pad,
              left: rect.left - pad,
              width: rect.width + pad * 2,
              height: rect.height + pad * 2,
            }}
          />
        </div>
      )}

      {/* Tooltip */}
      <div ref={tooltipRef} style={tooltipStyle} className="pointer-events-auto">
        <div className="bg-popover text-popover-foreground rounded-xl shadow-lg border p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-base">{t(step.titleKey)}</h3>
            <span className="text-xs text-muted-foreground tabular-nums">
              {current + 1}/{STEPS.length}
            </span>
          </div>
          <p className="text-sm text-muted-foreground leading-relaxed">{t(step.descKey)}</p>
          <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
            <Button variant="ghost" size="sm" onClick={onComplete} className="text-muted-foreground">
              {t('onboarding.skip')}
            </Button>
            <div className="ml-auto flex gap-2">
              {current > 0 && (
                <Button variant="outline" size="sm" onClick={back}>
                  {t('onboarding.back')}
                </Button>
              )}
              <Button size="sm" onClick={next}>
                {current === STEPS.length - 1 ? t('onboarding.finish') : t('onboarding.next')}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
