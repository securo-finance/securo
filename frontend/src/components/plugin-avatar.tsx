import type { CSSProperties } from 'react'

import { cn } from '@/lib/utils'

/** A stable hue for a string, so a plugin or an author keeps its colour everywhere. */
function hue(seed: string): number {
  let hash = 0
  for (const char of seed) hash = (hash * 31 + char.codePointAt(0)!) | 0
  return Math.abs(hash) % 360
}

function initials(label: string): string {
  const words = label.replace(/[^\p{L}\p{N}\s]/gu, ' ').split(/\s+/).filter(Boolean)
  const letters = words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? '?').slice(0, 2)
  return letters.toUpperCase()
}

/**
 * Generated artwork from a seed: no image is fetched, so browsing the catalog
 * never tells a third party which plugins or authors someone looked at.
 */
export function PluginAvatar({
  seed,
  label,
  shape = 'square',
  className,
}: {
  seed: string
  label: string
  shape?: 'square' | 'round'
  className?: string
}) {
  return (
    <span
      aria-hidden
      style={{ '--h': hue(seed) } as CSSProperties}
      className={cn(
        'flex shrink-0 select-none items-center justify-center font-semibold tracking-tight',
        'bg-[oklch(0.95_0.035_var(--h))] text-[oklch(0.42_0.1_var(--h))]',
        'dark:bg-[oklch(0.32_0.05_var(--h))] dark:text-[oklch(0.86_0.08_var(--h))]',
        shape === 'round' ? 'rounded-full' : 'rounded-[28%]',
        className,
      )}
    >
      {initials(label)}
    </span>
  )
}
