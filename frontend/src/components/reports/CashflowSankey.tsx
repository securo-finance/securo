import { ltrAmount } from '@/lib/format'
import {
  LABEL_LINE_HEIGHT,
  columnLabelSpan,
  placeColumnLabels,
  wrapLabelLines,
  type ColumnLabelPlacement,
} from '@/lib/sankey-labels'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { sankey as d3Sankey, sankeyLinkHorizontal, sankeyJustify, type SankeyGraph } from 'd3-sankey'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import type { ReportCompositionItem } from '@/types'

// Colour carries MEANING, not category identity: green = money in, red = money
// out, gray = uncategorised/folded. Tinting by category (the old approach) made
// a green "Groceries" expense look like green "Salary" income — the direction of
// flow was invisible. Category identity is carried by the always-on labels
// instead. Links fade from their source colour to their target colour, so an
// expense flow visibly turns from green (cash flow) to red at the category.
const INCOME_COLOR = 'var(--chart-income)' // emerald: money in
const EXPENSE_COLOR = 'var(--chart-expense)' // rose: money out
const INVEST_COLOR = 'var(--chart-5)' // sky blue: money set aside (investments)
const CENTER_COLOR = 'color-mix(in srgb, var(--chart-income), black 18%)' // deeper emerald: the cash-flow hub
const SURPLUS_COLOR = 'var(--chart-income)' // emerald: leftover income
const DEFICIT_COLOR = 'var(--chart-2)' // amber: overspend drawn from reserves
const NEUTRAL_COLOR = 'var(--chart-muted)' // gray: uncategorised / folded long tail

// Keep the diagram legible: personal-finance Sankeys read best at ~a dozen
// streams per side. Beyond this we fold the smallest categories into "Other".
const MAX_NODES_PER_SIDE = 9

const NODE_WIDTH = 16
const NODE_PADDING = 16
const TOP_GUTTER = 30 // header room above the nodes for the centre label

/** Shared empty map, so a chart with no width keeps a stable reference. */
const NO_LABELS = new Map<number, ColumnLabelPlacement>()

interface SankeyNodeDatum {
  id: string
  name: string
  color: string
  side: 'income' | 'center' | 'expense' | 'investment'
}

interface SankeyLinkDatum {
  source: number
  target: number
  value: number
}

// What the cursor is isolating. A plain node hover lights its own flows; the
// two halves of the centre bar light *all* expenses or the surplus at once —
// the "show me everything I spent" gesture the single bar couldn't offer.
type Hover = { kind: 'node'; index: number } | { kind: 'expenses' } | { kind: 'investments' } | { kind: 'surplus' }

function formatMoney(value: number, currency: string, locale: string, compact: boolean) {
  return ltrAmount(
    new Intl.NumberFormat(locale, {
      style: 'currency',
      currency,
      notation: compact ? 'compact' : 'standard',
      maximumFractionDigits: compact ? 1 : 0,
    }).format(value),
  )
}

/** Sort largest-first, then fold everything past the cap into a single "Other". */
function collapse(items: ReportCompositionItem[], otherLabel: string): ReportCompositionItem[] {
  const sorted = [...items].sort((a, b) => b.value - a.value)
  if (sorted.length <= MAX_NODES_PER_SIDE) return sorted
  const top = sorted.slice(0, MAX_NODES_PER_SIDE - 1)
  const rest = sorted.slice(MAX_NODES_PER_SIDE - 1)
  const otherValue = rest.reduce((s, c) => s + c.value, 0)
  return [...top, { key: 'other', label: otherLabel, value: otherValue, color: NEUTRAL_COLOR, group: rest[0].group }]
}

interface CashflowSankeyProps {
  composition: ReportCompositionItem[]
  currency: string
  locale: string
}

export function CashflowSankey({ composition, currency, locale }: CashflowSankeyProps) {
  const { t } = useTranslation()
  const { privacyMode, MASK } = usePrivacyMode()
  const containerRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const [hover, setHover] = useState<Hover | null>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const observer = new ResizeObserver((entries) => {
      setWidth(entries[0].contentRect.width)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const {
    nodes: rawNodes,
    links: rawLinks,
    hasData,
  } = useMemo(() => {
    const income = collapse(
      composition.filter((c) => c.group === 'income' && c.value > 0),
      t('reports.other'),
    )
    const expense = collapse(
      composition.filter((c) => c.group === 'expenses' && c.value > 0),
      t('reports.other'),
    )
    // Investments are a third outflow lane — money set aside, neither spent nor
    // surplus. Treated like Sure's "Investment Contributions" node.
    const investment = collapse(
      composition.filter((c) => c.group === 'investments' && c.value > 0),
      t('reports.other'),
    )

    // Money taken out of investments pays for the month without being income
    // (#1418). It enters as one source beside income, so spending it shows as
    // drawn from savings rather than as a deficit.
    const totalWithdraw = composition
      .filter((c) => c.group === 'withdrawals' && c.value > 0)
      .reduce((s, c) => s + c.value, 0)

    if (income.length === 0 && expense.length === 0 && investment.length === 0 && totalWithdraw === 0) {
      return { nodes: [] as SankeyNodeDatum[], links: [] as SankeyLinkDatum[], hasData: false }
    }

    const totalIncome = income.reduce((s, c) => s + c.value, 0)
    const totalExpense = expense.reduce((s, c) => s + c.value, 0)
    const totalInvest = investment.reduce((s, c) => s + c.value, 0)
    // Surplus is what's left after BOTH spending and investing — so investing
    // shrinks surplus instead of silently inflating it.
    const net = totalIncome + totalWithdraw - totalExpense - totalInvest

    const nodes: SankeyNodeDatum[] = []
    const links: SankeyLinkDatum[] = []
    const indexOf = new Map<string, number>()
    const pushNode = (n: SankeyNodeDatum) => {
      indexOf.set(n.id, nodes.length)
      nodes.push(n)
      return nodes.length - 1
    }

    const labelFor = (c: ReportCompositionItem) =>
      c.key === 'uncategorized' ? t('reports.uncategorized') : c.key === 'other' ? t('reports.other') : c.label
    const isNeutral = (c: ReportCompositionItem) => c.key === 'uncategorized' || c.key === 'other'

    income.forEach((c, i) =>
      pushNode({
        id: `in-${c.key}-${i}`,
        name: labelFor(c),
        color: isNeutral(c) ? NEUTRAL_COLOR : INCOME_COLOR,
        side: 'income',
      }),
    )

    if (totalWithdraw > 0) {
      pushNode({
        id: 'withdrawals',
        name: t('reports.investmentWithdrawals'),
        color: INVEST_COLOR,
        side: 'income',
      })
    }

    // Deficit appears as an inflow on the income side so the centre balances.
    if (net < 0) {
      pushNode({ id: 'deficit', name: t('reports.deficit'), color: DEFICIT_COLOR, side: 'income' })
    }

    const centerIdx = pushNode({
      id: 'center',
      name: t('reports.cashFlowNode'),
      color: CENTER_COLOR,
      side: 'center',
    })

    // Right column, top → bottom: expenses (red), investments (teal), surplus
    // (green) — matching the centre bar's stacked split.
    expense.forEach((c, i) =>
      pushNode({
        id: `ex-${c.key}-${i}`,
        name: labelFor(c),
        color: isNeutral(c) ? NEUTRAL_COLOR : EXPENSE_COLOR,
        side: 'expense',
      }),
    )

    investment.forEach((c, i) =>
      pushNode({
        id: `inv-${c.key}-${i}`,
        name: c.key === 'other' ? t('reports.other') : c.label,
        color: c.key === 'other' ? NEUTRAL_COLOR : INVEST_COLOR,
        side: 'investment',
      }),
    )

    if (net > 0) {
      pushNode({ id: 'surplus', name: t('reports.surplus'), color: SURPLUS_COLOR, side: 'expense' })
    }

    income.forEach((c, i) =>
      links.push({ source: indexOf.get(`in-${c.key}-${i}`)!, target: centerIdx, value: c.value }),
    )
    if (totalWithdraw > 0) {
      links.push({ source: indexOf.get('withdrawals')!, target: centerIdx, value: totalWithdraw })
    }
    if (net < 0) {
      links.push({ source: indexOf.get('deficit')!, target: centerIdx, value: -net })
    }
    expense.forEach((c, i) =>
      links.push({ source: centerIdx, target: indexOf.get(`ex-${c.key}-${i}`)!, value: c.value }),
    )
    investment.forEach((c, i) =>
      links.push({ source: centerIdx, target: indexOf.get(`inv-${c.key}-${i}`)!, value: c.value }),
    )
    if (net > 0) {
      links.push({ source: centerIdx, target: indexOf.get('surplus')!, value: net })
    }

    return { nodes, links, hasData: true }
  }, [composition, t])

  // Tall enough that the busier side's nodes don't crowd; grows with node count.
  const maxSide = useMemo(() => {
    const incomeCount = rawNodes.filter((n) => n.side === 'income').length
    const outflowCount = rawNodes.filter((n) => n.side === 'expense' || n.side === 'investment').length
    return Math.max(incomeCount, outflowCount, 1)
  }, [rawNodes])

  // Always-on labels: wrap each name into the gap it has before the centre bar
  // and stack the blocks so a long name neither runs under the bar nor into
  // the label below it. On a phone the gap is about 100px, narrower than
  // "Investment withdrawals", which used to come out cut (#287). The labels
  // hang off their nodes, so the chart is laid out, measured and laid out again
  // when the columns need more height than the node spacing asked for.
  const chart = useMemo(() => {
    if (!hasData || width === 0) return null
    const build = (chartHeight: number) => {
      const generator = d3Sankey<SankeyNodeDatum, SankeyLinkDatum>()
        .nodeWidth(NODE_WIDTH)
        .nodePadding(NODE_PADDING)
        .nodeAlign(sankeyJustify)
        .extent([
          [8, TOP_GUTTER],
          [width - 8, chartHeight - 14],
        ])
      // d3-sankey mutates its input — clone so re-renders stay pure.
      return generator({
        nodes: rawNodes.map((n) => ({ ...n })),
        links: rawLinks.map((l) => ({ ...l })),
      })
    }
    const columnsFor = (current: SankeyGraph<SankeyNodeDatum, SankeyLinkDatum>) => {
      const centre = current.nodes.find((n) => n.id === 'center')
      const sideNodes = current.nodes.filter((n) => n.id !== 'center')
      return [true, false].map((onLeft) => {
        const column = sideNodes
          .filter((n) => (n.x0 ?? 0) < width / 2 === onLeft)
          .sort((a, b) => (a.y0! + a.y1!) / 2 - (b.y0! + b.y1!) / 2)
        // The label starts 8px off the node and the centre bar gets 8px of its
        // own, so the usable width is the distance between them minus 16.
        const gutter = onLeft
          ? (centre?.x0 ?? width / 2) - Math.max(...column.map((n) => n.x1 ?? 0)) - 16
          : Math.min(...column.map((n) => n.x0 ?? width)) - (centre?.x1 ?? width / 2) - 16
        return {
          indexes: column.map((n) => (n as SankeyNodeDatum & { index: number }).index),
          items: column.map((n) => ({
            center: (n.y0! + n.y1!) / 2,
            lines: wrapLabelLines(n.name, gutter),
          })),
        }
      })
    }

    let height = Math.min(780, Math.max(380, maxSide * 60))
    let layout = build(height)
    let columns = columnsFor(layout)
    // Room for the tallest label column, the top gutter and the bottom margin.
    const needed = Math.max(...columns.map((column) => columnLabelSpan(column.items))) + TOP_GUTTER + 20
    if (needed > height) {
      height = needed
      layout = build(height)
      columns = columnsFor(layout)
    }

    const labels = new Map<number, ColumnLabelPlacement>()
    for (const column of columns) {
      const placements = placeColumnLabels(column.items, LABEL_LINE_HEIGHT / 2 + 2, height - 4)
      column.indexes.forEach((index, i) => labels.set(index, placements[i]))
    }
    return { layout, height, labels }
  }, [rawNodes, rawLinks, width, maxSide, hasData])

  const layout = chart?.layout ?? null
  const height = chart?.height ?? 0
  const labels = chart?.labels ?? NO_LABELS

  // Resolve the current hover into the exact set of links/nodes to keep lit.
  const { activeLinks, activeNodes } = useMemo(() => {
    const links = new Set<number>()
    const nodes = new Set<number>()
    if (layout && hover) {
      layout.links.forEach((lk, i) => {
        const s = lk.source as SankeyNodeDatum & { index: number }
        const tg = lk.target as SankeyNodeDatum & { index: number }
        let on = false
        if (hover.kind === 'node') on = s.index === hover.index || tg.index === hover.index
        else if (hover.kind === 'expenses') on = s.id === 'center' && tg.side === 'expense' && tg.id !== 'surplus'
        else if (hover.kind === 'investments') on = s.id === 'center' && tg.side === 'investment'
        else if (hover.kind === 'surplus') on = tg.id === 'surplus'
        if (on) {
          links.add(i)
          nodes.add(s.index)
          nodes.add(tg.index)
        }
      })
    }
    return { activeLinks: links, activeNodes: nodes }
  }, [layout, hover])

  if (!hasData) {
    return <p className="text-muted-foreground text-sm text-center py-16">{t('reports.noData')}</p>
  }

  const linkPath = sankeyLinkHorizontal<SankeyNodeDatum, SankeyLinkDatum>()
  // Percentages are relative to total cash-flow throughput — the centre node's
  // value (d3-sankey sets it to the larger of its in/out sums).
  const total = layout?.nodes.find((n) => n.id === 'center')?.value ?? 0
  const fmtAmount = (v: number) => (privacyMode ? MASK : formatMoney(v, currency, locale, true))

  // The centre bar's outflow split: spent (red) vs invested (teal) vs kept
  // (green). Links attach top→bottom in node order (expenses, investments,
  // surplus), so stacking the bar's colours in that order lines up with where
  // the flows actually leave the hub.
  const outBy = (pred: (n: SankeyNodeDatum) => boolean) =>
    layout
      ? layout.links
          .filter((l) => (l.source as SankeyNodeDatum).id === 'center' && pred(l.target as SankeyNodeDatum))
          .reduce((s, l) => s + l.value, 0)
      : 0
  const expenseOut = outBy((n) => n.side === 'expense' && n.id !== 'surplus')
  const investOut = outBy((n) => n.side === 'investment')
  const surplusOut = outBy((n) => n.id === 'surplus')

  const linkDimmed = (i: number) => hover !== null && !activeLinks.has(i)
  const nodeDimmed = (idx: number) => hover !== null && !activeNodes.has(idx)

  return (
    <div ref={containerRef} className="w-full privacy-sensitive">
      {layout && (
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={t('reports.flowChartAria')}
          onMouseLeave={() => setHover(null)}
        >
          <defs>
            {layout.links.map((link, i) => {
              const src = link.source as SankeyNodeDatum & { x1: number }
              const tgt = link.target as SankeyNodeDatum & { x0: number }
              return (
                <linearGradient key={i} id={`flow-grad-${i}`} gradientUnits="userSpaceOnUse" x1={src.x1} x2={tgt.x0}>
                  <stop offset="0%" stopColor={src.color} />
                  <stop offset="100%" stopColor={tgt.color} />
                </linearGradient>
              )
            })}
          </defs>

          {/* Links — gradient from source colour to target colour */}
          <g fill="none">
            {layout.links.map((link, i) => {
              const dimmed = linkDimmed(i)
              const active = hover !== null && activeLinks.has(i)
              const pct = total > 0 ? ((link.value / total) * 100).toFixed(1) : '0'
              return (
                <path
                  key={i}
                  d={linkPath(link) ?? undefined}
                  stroke={`url(#flow-grad-${i})`}
                  strokeOpacity={dimmed ? 0.07 : active ? 0.6 : 0.4}
                  strokeWidth={Math.max(1.5, link.width ?? 1)}
                  style={{ transition: 'stroke-opacity 0.2s ease' }}
                >
                  <title>
                    {(link.target as SankeyNodeDatum).id === 'center'
                      ? (link.source as SankeyNodeDatum).name
                      : (link.target as SankeyNodeDatum).name}
                    : {fmtAmount(link.value)} ({pct}%)
                  </title>
                </path>
              )
            })}
          </g>

          {/* Nodes + always-on labels */}
          <g>
            {layout.nodes.map((node, i) => {
              const x0 = node.x0 ?? 0
              const x1 = node.x1 ?? 0
              const y0 = node.y0 ?? 0
              const y1 = node.y1 ?? 0
              const nodeHeight = Math.max(1, y1 - y0)
              const isCenter = node.id === 'center'
              const dimmed = nodeDimmed(i)
              const nodeWidthPx = Math.max(1, x1 - x0)

              // Centre bar is split into spent (red), invested (teal) and kept
              // (green) zones, each its own hover target so the cursor can
              // isolate ALL of that flow type at once. Label sits in the top
              // gutter. Segments are stacked in the same order the links leave
              // the hub (expenses, investments, surplus).
              if (isCenter) {
                const cx = (x0 + x1) / 2
                const out = expenseOut + investOut + surplusOut || 1
                const redH = nodeHeight * (expenseOut / out)
                const tealH = nodeHeight * (investOut / out)
                const segments = [
                  {
                    h: redH,
                    fill: EXPENSE_COLOR,
                    hover: { kind: 'expenses' } as Hover,
                    title: t('reports.expenses'),
                    val: expenseOut,
                  },
                  {
                    h: tealH,
                    fill: INVEST_COLOR,
                    hover: { kind: 'investments' } as Hover,
                    title: t('reports.investments'),
                    val: investOut,
                  },
                  {
                    h: Math.max(0, nodeHeight - redH - tealH),
                    fill: SURPLUS_COLOR,
                    hover: { kind: 'surplus' } as Hover,
                    title: t('reports.surplus'),
                    val: surplusOut,
                  },
                ]
                let segY = y0
                return (
                  <g key={i} style={{ transition: 'opacity 0.2s ease', opacity: dimmed ? 0.4 : 1 }}>
                    {segments
                      .filter((seg) => seg.val > 0)
                      .map((seg, si) => {
                        const yy = segY
                        segY += seg.h
                        return (
                          <rect
                            key={si}
                            x={x0}
                            y={yy}
                            width={nodeWidthPx}
                            height={Math.max(1, seg.h)}
                            fill={seg.fill}
                            rx={4}
                            style={{ cursor: 'pointer' }}
                            onMouseEnter={() => setHover(seg.hover)}
                          >
                            <title>
                              {seg.title}: {fmtAmount(seg.val)}
                            </title>
                          </rect>
                        )
                      })}
                    <text
                      x={cx}
                      y={10}
                      textAnchor="middle"
                      className="fill-foreground pointer-events-none"
                      style={{ fontSize: 11, fontWeight: 600 }}
                    >
                      {node.name}
                      <tspan
                        x={cx}
                        dy={13}
                        className="fill-muted-foreground"
                        style={{ fontSize: 10, fontFamily: 'var(--font-mono, monospace)', fontWeight: 400 }}
                      >
                        {fmtAmount(node.value ?? 0)}
                      </tspan>
                    </text>
                  </g>
                )
              }

              const onLeft = x0 < width / 2
              const labelX = onLeft ? x1 + 8 : x0 - 8
              const label = labels.get(i)
              const labelLines = label?.lines ?? [node.name]
              const labelY = label?.y ?? (y0 + y1) / 2
              return (
                <g
                  key={i}
                  style={{ transition: 'opacity 0.2s ease', opacity: dimmed ? 0.35 : 1 }}
                  onMouseEnter={() => setHover({ kind: 'node', index: i })}
                >
                  <rect x={x0} y={y0} width={Math.max(1, x1 - x0)} height={nodeHeight} fill={node.color} rx={3}>
                    <title>
                      {node.name}: {fmtAmount(node.value ?? 0)}
                    </title>
                  </rect>
                  <text
                    x={labelX}
                    y={labelY}
                    textAnchor={onLeft ? 'start' : 'end'}
                    dominantBaseline="middle"
                    className="fill-foreground"
                    style={{ fontSize: 11, fontWeight: 500 }}
                  >
                    {labelLines.map((line, li) => (
                      <tspan key={li} x={labelX} dy={li === 0 ? 0 : LABEL_LINE_HEIGHT}>
                        {line}
                      </tspan>
                    ))}
                    <tspan
                      x={labelX}
                      dy={LABEL_LINE_HEIGHT}
                      className="fill-muted-foreground"
                      style={{ fontSize: 10, fontFamily: 'var(--font-mono, monospace)' }}
                    >
                      {fmtAmount(node.value ?? 0)}
                    </tspan>
                  </text>
                </g>
              )
            })}
          </g>
        </svg>
      )}

      {/* Accessible table fallback — also a non-visual summary of the flows.
          `sr-only` goes on a wrapper, never on the table: `overflow` does not
          apply to table boxes, so a clipped-looking table kept its cells at
          their natural width and made the whole page 504px wide on a 390px
          screen. */}
      <div className="sr-only">
        <table>
          <caption>{t('reports.flowChartAria')}</caption>
          <thead>
            <tr>
              <th>{t('reports.category')}</th>
              <th>{t('reports.amount')}</th>
            </tr>
          </thead>
          <tbody>
            {rawNodes
              .filter((n) => n.side !== 'center')
              .map((n, i) => {
                const idx = rawNodes.indexOf(n)
                const value = layout?.nodes[idx]?.value ?? 0
                return (
                  <tr key={i}>
                    <td>{n.name}</td>
                    <td>{fmtAmount(value)}</td>
                  </tr>
                )
              })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
