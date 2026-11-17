import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ImageOff } from 'lucide-react'

type NodeProps = { children?: ReactNode }

/**
 * A plugin's README. Raw HTML is never rendered, and images are shown as
 * links instead of loaded: an image would be fetched from wherever its
 * author hosts it, telling that host who opened the plugin.
 */
export function PluginReadme({ markdown, base }: { markdown: string; base: string }) {
  /** Relative links point into the repository at the pinned commit, not into Securo. */
  const resolve = (href: unknown): string | undefined => {
    if (typeof href !== 'string' || !href) return undefined
    if (href.startsWith('#')) return href
    try {
      const url = new URL(href, base)
      return ['http:', 'https:', 'mailto:'].includes(url.protocol) ? url.toString() : undefined
    } catch {
      return undefined
    }
  }
  return (
    <div className="text-sm leading-relaxed text-foreground/90 [&>:first-child]:mt-0 [&>:last-child]:mb-0">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }: NodeProps) => <h2 className="mb-2 mt-6 text-lg font-semibold text-foreground">{children}</h2>,
          h2: ({ children }: NodeProps) => (
            <h3 className="mb-2 mt-6 border-b pb-1.5 text-base font-semibold text-foreground">{children}</h3>
          ),
          h3: ({ children }: NodeProps) => <h4 className="mb-1.5 mt-4 text-sm font-semibold text-foreground">{children}</h4>,
          h4: ({ children }: NodeProps) => <h5 className="mb-1 mt-3 text-sm font-semibold text-foreground">{children}</h5>,
          p: ({ children }: NodeProps) => <p className="my-3">{children}</p>,
          ul: ({ children }: NodeProps) => <ul className="my-3 list-disc space-y-1 pl-5">{children}</ul>,
          ol: ({ children }: NodeProps) => <ol className="my-3 list-decimal space-y-1 pl-5">{children}</ol>,
          li: ({ children }: NodeProps) => <li className="marker:text-muted-foreground/70">{children}</li>,
          strong: ({ children }: NodeProps) => <strong className="font-semibold text-foreground">{children}</strong>,
          blockquote: ({ children }: NodeProps) => (
            <blockquote className="my-3 border-l-2 pl-3 text-muted-foreground">{children}</blockquote>
          ),
          a: ({ href, children }: NodeProps & { href?: string }) => (
            <a href={resolve(href)} target="_blank" rel="noopener noreferrer nofollow" className="text-primary underline underline-offset-2 hover:opacity-80">
              {children}
            </a>
          ),
          img: ({ src, alt }: { src?: unknown; alt?: string }) => (
            <a
              href={resolve(src)}
              target="_blank"
              rel="noopener noreferrer nofollow"
              className="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs text-muted-foreground hover:text-foreground"
            >
              <ImageOff size={12} />
              {alt || src?.toString()}
            </a>
          ),
          hr: () => <hr className="my-5 border-border" />,
          code: ({ children, className }: NodeProps & { className?: string }) =>
            className ? (
              <code className={className}>{children}</code>
            ) : (
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">{children}</code>
            ),
          pre: ({ children }: NodeProps) => (
            <pre className="my-3 overflow-x-auto rounded-lg border bg-muted/50 p-3 font-mono text-xs leading-relaxed">{children}</pre>
          ),
          table: ({ children }: NodeProps) => (
            <div className="my-3 overflow-x-auto rounded-lg border">
              <table className="w-full text-xs">{children}</table>
            </div>
          ),
          th: ({ children }: NodeProps) => <th className="border-b bg-muted/50 px-3 py-1.5 text-left font-medium">{children}</th>,
          td: ({ children }: NodeProps) => <td className="border-b px-3 py-1.5 last:border-b-0">{children}</td>,
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  )
}
