import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { cn } from '@/lib/utils'
import { Component, memo, type ReactNode } from 'react'
import { AgentChart, type ChartSpec } from '@/components/agents/agent-chart'

type NodeProps = { children?: ReactNode }

interface Props {
  children: string
  className?: string
}

/**
 * Tailwind-styled GFM markdown for chat messages.
 *
 * Deliberately conservative: no raw HTML, no images, links open in a new
 * tab with rel=noopener. Sized to feel right inside the chat bubble — not
 * a documentation page.
 *
 * Wrapped in React.memo at the bottom so static assistant messages
 * (including embedded `securo-chart` blocks) don't re-render on every
 * keystroke in the chat input — they were causing Recharts to fully
 * reanimate per character typed.
 */
function _Markdown({ children, className }: Props) {
  return (
    <div className={cn('text-sm leading-relaxed [&>:first-child]:mt-0 [&>:last-child]:mb-0', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }: NodeProps) => <p className="my-2 whitespace-pre-wrap">{children}</p>,
          ul: ({ children }: NodeProps) => <ul className="my-2 list-disc ps-5 space-y-0.5">{children}</ul>,
          ol: ({ children }: NodeProps) => <ol className="my-2 list-decimal ps-5 space-y-0.5">{children}</ol>,
          li: ({ children }: NodeProps) => <li className="marker:text-muted-foreground/70">{children}</li>,
          h1: ({ children }: NodeProps) => <h1 className="text-base font-bold mt-3 mb-1.5">{children}</h1>,
          h2: ({ children }: NodeProps) => <h2 className="text-[15px] font-semibold mt-3 mb-1.5">{children}</h2>,
          h3: ({ children }: NodeProps) => <h3 className="text-sm font-semibold mt-2.5 mb-1">{children}</h3>,
          h4: ({ children }: NodeProps) => <h4 className="text-sm font-semibold mt-2 mb-1">{children}</h4>,
          strong: ({ children }: NodeProps) => <strong className="font-semibold">{children}</strong>,
          em: ({ children }: NodeProps) => <em className="italic">{children}</em>,
          del: ({ children }: NodeProps) => <del className="opacity-70">{children}</del>,
          blockquote: ({ children }: NodeProps) => (
            <blockquote className="border-s-2 border-border ps-3 italic text-muted-foreground my-2">
              {children}
            </blockquote>
          ),
          a: ({ href, children }: NodeProps & { href?: string }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary underline underline-offset-2 hover:opacity-80"
            >
              {children}
            </a>
          ),
          hr: () => <hr className="my-3 border-border" />,
          table: ({ children }: NodeProps) => (
            <div className="my-2 overflow-x-auto">
              <table className="border-collapse text-xs">{children}</table>
            </div>
          ),
          thead: ({ children }: NodeProps) => <thead className="bg-muted/60">{children}</thead>,
          th: ({ children }: NodeProps) => (
            <th className="border border-border px-2 py-1 text-start font-semibold">{children}</th>
          ),
          td: ({ children }: NodeProps) => <td className="border border-border px-2 py-1 align-top">{children}</td>,
          code: ({ className, children, ...props }: NodeProps & { className?: string }) => {
            const isFenced = typeof className === 'string' && className.startsWith('language-')
            // Special-case our agent chart code-fence: parse the JSON
            // body and render an inline recharts figure instead of a
            // code block. Falls back to a plain pre/code on parse error
            // so the user can still see what the model emitted.
            if (className === 'language-securo-chart') {
              const raw = String(children ?? '').trim()
              try {
                const spec = JSON.parse(raw) as ChartSpec
                return (
                  <ChartErrorBoundary raw={raw}>
                    <AgentChart spec={spec} />
                  </ChartErrorBoundary>
                )
              } catch {
                // fall through to plain code rendering
              }
            }
            if (isFenced) {
              return (
                <code className={cn('font-mono text-xs', className)} {...props}>
                  {children}
                </code>
              )
            }
            return (
              <code
                className="rounded border border-border/60 bg-background/60 px-1.5 py-0.5 text-[11.5px] font-mono"
                {...props}
              >
                {children}
              </code>
            )
          },
          pre: ({ children }: NodeProps) => (
            <pre className="my-2 overflow-x-auto rounded-md border bg-background/60 p-3 text-xs leading-snug">
              {children}
            </pre>
          ),
          input: ({ checked, type }: { checked?: boolean; type?: string }) => {
            // GFM task lists.
            if (type === 'checkbox') {
              return (
                <input type="checkbox" checked={!!checked} readOnly className="me-1.5 align-middle accent-primary" />
              )
            }
            return null
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}

/** Memoized export — keystrokes in the chat input force the parent to
 *  re-render, but assistant messages don't change, so we skip rendering
 *  them entirely when `children`/`className` are referentially the
 *  same. Critical for chats that contain a recharts figure (Recharts
 *  treats new prop refs as new data and re-runs its enter animations). */
export const Markdown = memo(
  _Markdown,
  (prev, next) => prev.children === next.children && prev.className === next.className,
)

interface ChartBoundaryProps {
  children: ReactNode
  raw: string
}

interface ChartBoundaryState {
  hasError: boolean
}

/** Recharts can throw inside its layout effects (e.g. legend dispatcher
 *  hitting "Maximum update depth exceeded" in tight panel widths). A
 *  thrown error in a chart should NOT take the whole chat down — degrade
 *  to a code block so the user still sees the JSON the model produced. */
class ChartErrorBoundary extends Component<ChartBoundaryProps, ChartBoundaryState> {
  state: ChartBoundaryState = { hasError: false }

  static getDerivedStateFromError(): ChartBoundaryState {
    return { hasError: true }
  }

  render() {
    if (!this.state.hasError) return this.props.children
    return (
      <pre className="my-3 rounded-md border border-border bg-muted/40 p-3 text-[11px] leading-snug font-mono overflow-x-auto whitespace-pre-wrap">
        {this.props.raw}
      </pre>
    )
  }
}
