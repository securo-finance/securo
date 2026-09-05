import { ICON_MAP, isEmoji } from '@/lib/category-icons'
import { CircleHelp } from 'lucide-react'
import { cn } from '@/lib/utils'

const SIZES = {
  xs: { box: 'w-4 h-4 rounded', icon: 10 },
  sm: { box: 'w-6 h-6 rounded-md', icon: 14 },
  md: { box: 'w-8 h-8 rounded-lg', icon: 16 },
  lg: { box: 'w-10 h-10 rounded-xl', icon: 20 },
  xl: { box: 'w-11 h-11 rounded-xl', icon: 22 },
} as const

interface CategoryIconProps {
  icon: string | null | undefined
  color: string | null | undefined
  size?: keyof typeof SIZES
  className?: string
}

export function CategoryIcon({ icon, color, size = 'md', className }: CategoryIconProps) {
  const { box, icon: iconSize } = SIZES[size]
  const bgColor = color || '#6B7280'
  const iconStr = icon || 'circle-help'

  // Emoji fallback for backward compatibility
  if (isEmoji(iconStr)) {
    return (
      <div
        className={cn(box, 'flex items-center justify-center shrink-0', className)}
        style={{ backgroundColor: bgColor }}
      >
        <span style={{ fontSize: iconSize - 2, lineHeight: 1 }}>{iconStr}</span>
      </div>
    )
  }

  const LucideIcon = ICON_MAP[iconStr] ?? CircleHelp

  return (
    <div
      className={cn(box, 'flex items-center justify-center shrink-0', className)}
      style={{ backgroundColor: bgColor }}
    >
      <LucideIcon size={iconSize} className="text-white" strokeWidth={2} />
    </div>
  )
}
