import { useState } from 'react'
import { Building2 } from 'lucide-react'
import { getAccountTypeConfig } from '@/lib/account-type-config'
import { cn } from '@/lib/utils'

const SIZES = {
  xs: { tile: 'w-5 h-5', icon: 11 },
  sm: { tile: 'w-6 h-6', icon: 12 },
  md: { tile: 'w-8 h-8', icon: 14 },
  lg: { tile: 'w-10 h-10', icon: 18 },
} as const

/**
 * Renders the institution logo for an account when one is available, falling
 * back to the colored account-type icon. The image's `onError` swaps to the
 * type icon so a broken/blocked logo URL never leaves an empty tile.
 */
export function AccountIcon({
  account,
  size = 'md',
  className,
}: {
  account: { type: string; institution_logo_url?: string | null }
  size?: keyof typeof SIZES
  className?: string
}) {
  const [errored, setErrored] = useState(false)
  const cfg = getAccountTypeConfig(account.type)
  const Icon = cfg.icon
  const logo = account.institution_logo_url
  const showImage = !!logo && !errored
  const { tile, icon } = SIZES[size]

  return (
    <div
      className={cn(
        tile,
        'rounded-lg flex items-center justify-center overflow-hidden shrink-0',
        showImage ? 'bg-white border border-border' : cfg.bg,
        className,
      )}
    >
      {showImage ? (
        <img
          src={logo!}
          alt=""
          className="w-full h-full object-contain"
          onError={() => setErrored(true)}
        />
      ) : (
        <Icon size={icon} className={cfg.color} />
      )}
    </div>
  )
}

/**
 * Institution logo for a bank connection header. Falls back to a generic
 * bank icon when no logo is stored or the image fails to load.
 */
export function ConnectionLogo({
  logoUrl,
  className,
}: {
  logoUrl?: string | null
  className?: string
}) {
  const [errored, setErrored] = useState(false)
  const showImage = !!logoUrl && !errored

  return (
    <div
      className={cn(
        'w-8 h-8 rounded-lg flex items-center justify-center overflow-hidden shrink-0',
        showImage ? 'bg-white border border-border' : 'bg-muted',
        className,
      )}
    >
      {showImage ? (
        <img
          src={logoUrl!}
          alt=""
          className="w-full h-full object-contain"
          onError={() => setErrored(true)}
        />
      ) : (
        <Building2 size={14} className="text-muted-foreground" />
      )}
    </div>
  )
}
