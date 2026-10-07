import { useState } from 'react'
import { ChevronDown, ChevronRight, Loader2, Wrench } from 'lucide-react'
import { cn } from '@/lib/utils'

interface Props {
  name: string
  args?: Record<string, unknown> | null
  result?: { ok?: boolean; data?: unknown; text?: string | null } | null
  pending?: boolean
  defaultOpen?: boolean
}

/**
 * Expandable tool-call inspector. Compact by default (status, name, brief
 * summary). Clicking reveals the raw JSON arguments and result — same
 * "show your work" affordance anything-llm uses for agent skills.
 */
export function ToolDebugChip({ name, args, result, pending, defaultOpen = false }: Props) {
  const [open, setOpen] = useState(defaultOpen)
  const failed = result?.ok === false
    || (!!result?.data && typeof result.data === 'object' && 'error' in (result.data as Record<string, unknown>))
  const status = pending ? 'pending' : failed ? 'error' : result ? 'ok' : 'pending'
  const summary = result?.text || (result?.data && typeof result.data === 'object'
    ? summarizeData(result.data as Record<string, unknown>)
    : null)

  return (
    <div className={cn(
      'rounded-md border text-xs',
      status === 'error' ? 'border-rose-300/50 bg-rose-50/40 dark:bg-rose-950/20'
        : status === 'pending' ? 'border-border bg-muted/40'
        : 'border-emerald-300/40 bg-emerald-50/30 dark:bg-emerald-950/15'
    )}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left"
      >
        {status === 'pending' ? (
          <Loader2 className="h-3 w-3 animate-spin text-muted-foreground shrink-0" />
        ) : status === 'error' ? (
          <span className="text-rose-600 font-mono text-[11px] shrink-0">✗</span>
        ) : (
          <span className="text-emerald-600 font-mono text-[11px] shrink-0">✓</span>
        )}
        <Wrench className="h-3 w-3 text-muted-foreground shrink-0" />
        <span className="font-mono truncate">{name}</span>
        {summary && (
          <span className="text-muted-foreground truncate flex-1 min-w-0">— {summary}</span>
        )}
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground/70 shrink-0" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/70 shrink-0" />
        )}
      </button>
      {open && (
        <div className="border-t px-2.5 py-2 space-y-2">
          <Block label="Arguments" payload={args ?? {}} />
          {result && <Block label="Result" payload={result.data} fallback={result.text} />}
        </div>
      )}
    </div>
  )
}

function Block({ label, payload, fallback }: { label: string; payload: unknown; fallback?: string | null }) {
  const json = (() => {
    if (payload === undefined || payload === null) {
      return fallback || '(empty)'
    }
    try {
      return JSON.stringify(payload, null, 2)
    } catch {
      return String(payload)
    }
  })()
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">{label}</div>
      <pre className="rounded bg-background/70 border border-border/60 p-2 overflow-x-auto text-[11px] leading-snug font-mono">
        {json}
      </pre>
    </div>
  )
}

function summarizeData(data: Record<string, unknown>): string | null {
  if (Array.isArray((data as { items?: unknown[] }).items)) {
    const n = ((data as { items: unknown[] }).items).length
    const total = (data as { total?: number }).total
    return `${total ?? n} item${(total ?? n) === 1 ? '' : 's'}`
  }
  if ('error' in data) {
    return String((data as { error: unknown }).error)
  }
  if ('kind' in data) {
    return String((data as { kind: unknown }).kind)
  }
  return null
}
