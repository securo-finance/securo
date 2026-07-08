import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  Link2,
  Receipt,
  TrendingDown,
  TrendingUp,
  Trash2,
  UserPlus,
  Wallet,
} from 'lucide-react'
import {
  Bar,
  BarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
} from 'recharts'

import {
  groups as groupsApi,
  users as usersApi,
  accounts as accountsApi,
  transactions as transactionsApi,
  type GroupMemberPayload,
  type GroupSettlementPayload,
} from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { CategoryIcon } from '@/components/category-icon'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { PageHeader } from '@/components/page-header'
import type { GroupMember, GroupSettlement, Transaction } from '@/types'

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      {children}
    </div>
  )
}

function SectionHeader({
  title,
  description,
  action,
}: {
  title: string
  description?: string
  action?: React.ReactNode
}) {
  return (
    <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-2">
      <div className="min-w-0">
        <p className="text-sm font-semibold text-foreground">{title}</p>
        {description && (
          <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
        )}
      </div>
      {action}
    </div>
  )
}

interface KpiBreakdownItem {
  name: string
  amountText: string
}

function KpiCard({
  label,
  value,
  icon: Icon,
  tone,
  breakdown,
}: {
  label: string
  value: string
  icon: React.ComponentType<{ size?: number; className?: string }>
  tone?: 'positive' | 'negative' | 'neutral'
  breakdown?: KpiBreakdownItem[]
}) {
  const [open, setOpen] = useState(false)
  const toneClass =
    tone === 'positive'
      ? 'text-emerald-600'
      : tone === 'negative'
        ? 'text-rose-500'
        : 'text-foreground'
  const hasBreakdown = !!breakdown && breakdown.length > 0
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm p-3 sm:p-4">
      <button
        type="button"
        className={`w-full text-left ${hasBreakdown ? 'cursor-pointer' : 'cursor-default'}`}
        onClick={() => hasBreakdown && setOpen((o) => !o)}
        disabled={!hasBreakdown}
        aria-expanded={open}
      >
        <div className="flex items-center justify-between">
          <p className="text-[10px] sm:text-xs font-medium text-muted-foreground uppercase tracking-wide">
            {label}
          </p>
          <div className="flex items-center gap-1 text-muted-foreground">
            {hasBreakdown && (
              <ChevronDown
                size={14}
                className={`transition-transform ${open ? 'rotate-180' : ''}`}
              />
            )}
            <Icon size={14} />
          </div>
        </div>
        <p className={`text-base sm:text-2xl font-bold tabular-nums mt-1 ${toneClass}`}>
          {value}
        </p>
      </button>
      {open && hasBreakdown && (
        <ul className="mt-2 pt-2 border-t border-border space-y-1 text-xs text-muted-foreground">
          {breakdown!.map((b, i) => (
            <li key={i} className="flex justify-between gap-2">
              <span className="truncate">{b.name}</span>
              <span className="tabular-nums whitespace-nowrap text-foreground">
                {b.amountText}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function GroupDetailPage() {
  const { id } = useParams<{ id: string }>()
  const groupId = id ?? ''
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()

  const { data: group, isLoading: loadingGroup } = useQuery({
    queryKey: ['groups', groupId],
    queryFn: () => groupsApi.get(groupId),
    enabled: !!groupId,
  })

  // Linked members get a read-only view of the group.
  const isOwner = group?.is_owner ?? false
  // The member that represents the current viewer (when linked).
  const viewerMember = useMemo(
    () => group?.members.find((m) => user && m.linked_user_id === user.id),
    [group?.members, user],
  )
  // The "self" member is the owner-payer of the group's transactions.
  const ownerMember = useMemo(
    () => group?.members.find((m) => m.is_self),
    [group?.members],
  )

  const { data: balances } = useQuery({
    queryKey: ['groups', groupId, 'balances'],
    queryFn: () => groupsApi.balances(groupId),
    enabled: !!groupId,
  })

  const { data: settlements } = useQuery({
    queryKey: ['groups', groupId, 'settlements'],
    queryFn: () => groupsApi.settlements.list(groupId),
    enabled: !!groupId,
  })

  const { data: groupTxs } = useQuery({
    queryKey: ['groups', groupId, 'transactions'],
    queryFn: () => groupsApi.transactions(groupId, 20),
    enabled: !!groupId,
  })

  // ── Member management ────────────────────────────────────────
  const [memberDialogOpen, setMemberDialogOpen] = useState(false)
  const [editingMember, setEditingMember] = useState<GroupMember | null>(null)
  const [memberName, setMemberName] = useState('')
  const [memberEmail, setMemberEmail] = useState('')
  // The Securo user this member should be linked to (if any). When set,
  // name+email are derived from that user and the inputs are locked —
  // is_self is auto-inferred (true iff the linked user is the viewer).
  const [memberLinkedUserId, setMemberLinkedUserId] = useState<string | null>(null)

  const memberMutation = useMutation({
    mutationFn: (payload: GroupMemberPayload) =>
      editingMember
        ? groupsApi.members.update(groupId, editingMember.id, payload)
        : groupsApi.members.create(groupId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups', groupId] })
      queryClient.invalidateQueries({ queryKey: ['groups', groupId, 'balances'] })
      setMemberDialogOpen(false)
      setEditingMember(null)
      toast.success(editingMember ? t('splitGroups.memberUpdated') : t('splitGroups.memberAdded'))
    },
    onError: (err: unknown) => {
      const detail =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
          : undefined
      toast.error(detail ?? t('common.error'))
    },
  })

  const deleteMemberMutation = useMutation({
    mutationFn: (memberId: string) => groupsApi.members.delete(groupId, memberId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups', groupId] })
      queryClient.invalidateQueries({ queryKey: ['groups', groupId, 'balances'] })
      setMemberDialogOpen(false)
      setEditingMember(null)
      toast.success(t('splitGroups.memberDeleted'))
    },
    onError: (err: unknown) => {
      const detail =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
          : undefined
      toast.error(detail ?? t('common.error'))
    },
  })

  const openCreateMember = () => {
    setEditingMember(null)
    setMemberName('')
    setMemberEmail('')
    setMemberLinkedUserId(null)
    setMemberDialogOpen(true)
  }

  const openEditMember = (member: GroupMember) => {
    setEditingMember(member)
    setMemberName(member.name)
    setMemberEmail(member.email ?? '')
    setMemberLinkedUserId(member.linked_user_id)
    setMemberDialogOpen(true)
  }

  const saveMember = () => {
    // is_self is derived: a linked-to-viewer member is always "you".
    // Unlinked members can still represent the viewer if explicitly the
    // first member of their own group (legacy data) — we preserve that
    // flag during edits via the existing record.
    const linkedToViewer =
      memberLinkedUserId !== null && memberLinkedUserId === user?.id
    const is_self = linkedToViewer || (!memberLinkedUserId && (editingMember?.is_self ?? false))
    memberMutation.mutate({
      name: memberName.trim(),
      email: memberEmail.trim() || null,
      is_self,
    })
  }

  // Directory of all Securo users on the instance — populates the
  // member-picker dropdown so the host can pick an existing account
  // rather than typing its email.
  const { data: userDirectory } = useQuery({
    queryKey: ['users', 'directory'],
    queryFn: () => usersApi.directory(),
    enabled: memberDialogOpen,
    staleTime: 60_000,
  })

  // Resolve a typed email to an existing Securo user, kept as a
  // fallback for the "I know their email but they aren't in my list
  // for some reason" path.
  const trimmedEmail = memberEmail.trim()
  const { data: lookupResult } = useQuery({
    queryKey: ['users', 'lookup', trimmedEmail.toLowerCase()],
    queryFn: () => usersApi.lookupByEmail(trimmedEmail),
    enabled: trimmedEmail.length >= 3 && trimmedEmail.includes('@'),
    staleTime: 60_000,
    retry: false,
  })

  // ── Settle-up ────────────────────────────────────────────────
  const [settleOpen, setSettleOpen] = useState(false)
  const [settleFrom, setSettleFrom] = useState('')
  const [settleTo, setSettleTo] = useState('')
  const [settleAmount, setSettleAmount] = useState('')
  const [settleDate, setSettleDate] = useState(new Date().toISOString().split('T')[0])
  const [settleNotes, setSettleNotes] = useState('')
  const [settleCurrency, setSettleCurrency] = useState('USD')
  // Optional ledger integration for the payer: 'none' records the
  // settlement only, 'create' makes a fresh debit, 'existing' links a
  // transaction the payer already has.
  const [settleTxMode, setSettleTxMode] = useState<'none' | 'create' | 'existing'>('none')
  const [settleAccountId, setSettleAccountId] = useState('')
  // The transaction picked to link, plus the search box state. We keep
  // the whole object so the selection stays visible even after the
  // search term changes and it drops out of the result list.
  const [settlePickedTx, setSettlePickedTx] = useState<Transaction | null>(null)
  const [settleTxSearch, setSettleTxSearch] = useState('')
  const [settleTxQuery, setSettleTxQuery] = useState('')

  // Accounts of the requesting user — needed only when the optional
  // "create transaction" toggle is enabled.
  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
    enabled: settleOpen,
  })

  // Debounce the transaction search so we don't hit the API on every
  // keystroke (mirrors the transactions page pattern).
  useEffect(() => {
    const id = setTimeout(() => setSettleTxQuery(settleTxSearch), 300)
    return () => clearTimeout(id)
  }, [settleTxSearch])

  // The payer's debit transactions, searched server-side and capped —
  // offered when linking an existing transaction instead of creating one.
  const { data: settleTxOptions } = useQuery({
    queryKey: ['settle-tx-options', settleTxQuery],
    queryFn: () =>
      transactionsApi.list({
        type: 'debit',
        q: settleTxQuery || undefined,
        limit: 20,
        sort_by: 'date',
        sort_dir: 'desc',
      }),
    enabled: settleOpen && settleTxMode === 'existing',
  })

  const settlementMutation = useMutation({
    mutationFn: (payload: GroupSettlementPayload) =>
      groupsApi.settlements.create(groupId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups', groupId, 'settlements'] })
      queryClient.invalidateQueries({ queryKey: ['groups', groupId, 'balances'] })
      setSettleOpen(false)
      toast.success(t('splitGroups.settled'))
    },
    onError: (err: unknown) => {
      const detail =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
          : undefined
      toast.error(detail ?? t('common.error'))
    },
  })

  const deleteSettlementMutation = useMutation({
    mutationFn: (settlementId: string) =>
      groupsApi.settlements.delete(groupId, settlementId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups', groupId, 'settlements'] })
      queryClient.invalidateQueries({ queryKey: ['groups', groupId, 'balances'] })
    },
  })

  const openSettleUp = (
    from?: string,
    to?: string,
    amount?: number,
    currency?: string,
  ) => {
    setSettleFrom(from ?? '')
    setSettleTo(to ?? '')
    setSettleAmount(amount != null ? amount.toFixed(2) : '')
    setSettleDate(new Date().toISOString().split('T')[0])
    setSettleNotes('')
    // Use the line's currency when settling a specific debt, falling
    // back to the group's default for free-form settlements. This
    // matters when the same group has cross-currency debts.
    setSettleCurrency(currency ?? group?.default_currency ?? 'USD')
    setSettleTxMode('none')
    setSettleAccountId('')
    setSettlePickedTx(null)
    setSettleTxSearch('')
    setSettleTxQuery('')
    setSettleOpen(true)
  }

  const saveSettlement = () => {
    if (!settleFrom || !settleTo || !settleAmount) return
    const payload: GroupSettlementPayload = {
      from_member_id: settleFrom,
      to_member_id: settleTo,
      amount: parseFloat(settleAmount),
      currency: settleCurrency,
      date: settleDate,
      notes: settleNotes.trim() || null,
    }
    if (settleTxMode === 'create' && settleAccountId) {
      payload.account_id = settleAccountId
    } else if (settleTxMode === 'existing' && settlePickedTx) {
      payload.transaction_id = settlePickedTx.id
    }
    settlementMutation.mutate(payload)
  }

  // Lookup helpers
  const memberById = useMemo(() => {
    const map = new Map<string, GroupMember>()
    for (const m of group?.members ?? []) map.set(m.id, m)
    return map
  }, [group?.members])

  const memberName_ = (memberId: string) => memberById.get(memberId)?.name ?? '—'

  // ── KPIs ─────────────────────────────────────────────────────
  const groupCurrency = group?.default_currency ?? 'USD'

  // Sum cross-currency rows in the group's primary terms — using
  // amount_primary when available, otherwise the native amount. Without
  // this, EUR rows would silently add as USD (a €100 hotel would count
  // as $100, throwing off the KPI vs. spending-by-category breakdown).
  const totalMoved = useMemo(() => {
    if (!groupTxs) return 0
    return groupTxs.reduce(
      (sum, tx) => sum + Number(tx.amount_primary ?? tx.amount),
      0,
    )
  }, [groupTxs])

  // KPIs roll up across currencies using each line's
  // amount_in_default_currency (FX-converted server-side). Filtering by
  // a single currency would otherwise hide debts in another currency
  // — e.g. a EUR-only line wouldn't show up for a USD-default group.
  const owedToViewer = useMemo(() => {
    if (!balances) return 0
    if (isOwner) {
      return balances.lines
        .filter((l) => l.amount > 0)
        .reduce((s, l) => s + Number(l.amount_in_default_currency), 0)
    }
    if (!viewerMember) return 0
    return balances.lines
      .filter((l) => l.member_id === viewerMember.id && l.amount < 0)
      .reduce((s, l) => s + Math.abs(Number(l.amount_in_default_currency)), 0)
  }, [balances, isOwner, viewerMember])

  const viewerOwes = useMemo(() => {
    if (!balances) return 0
    if (isOwner) {
      return Math.abs(
        balances.lines
          .filter((l) => l.amount < 0)
          .reduce((s, l) => s + Number(l.amount_in_default_currency), 0),
      )
    }
    if (!viewerMember) return 0
    return balances.lines
      .filter((l) => l.member_id === viewerMember.id && l.amount > 0)
      .reduce((s, l) => s + Number(l.amount_in_default_currency), 0)
  }, [balances, isOwner, viewerMember])

  // Per-line breakdown for the two debt KPIs. Each row shows the other
  // party's name and the amount in its native currency — so a EUR line
  // stays "€100" instead of being lossy-rolled into the USD KPI total.
  const memberNameById = useMemo(() => {
    const map = new Map<string, string>()
    for (const m of group?.members ?? []) map.set(m.id, m.name)
    return map
  }, [group?.members])

  const owedToViewerBreakdown = useMemo<KpiBreakdownItem[]>(() => {
    if (!balances) return []
    if (isOwner) {
      return balances.lines
        .filter((l) => l.amount > 0)
        .map((l) => ({
          name: memberNameById.get(l.member_id) ?? '—',
          amountText: formatCurrency(Number(l.amount), l.currency, locale),
        }))
    }
    if (!viewerMember || !ownerMember) return []
    return balances.lines
      .filter((l) => l.member_id === viewerMember.id && l.amount < 0)
      .map((l) => ({
        name: ownerMember.name,
        amountText: formatCurrency(Math.abs(Number(l.amount)), l.currency, locale),
      }))
  }, [balances, isOwner, viewerMember, ownerMember, memberNameById, locale])

  const viewerOwesBreakdown = useMemo<KpiBreakdownItem[]>(() => {
    if (!balances) return []
    if (isOwner) {
      return balances.lines
        .filter((l) => l.amount < 0)
        .map((l) => ({
          name: memberNameById.get(l.member_id) ?? '—',
          amountText: formatCurrency(Math.abs(Number(l.amount)), l.currency, locale),
        }))
    }
    if (!viewerMember || !ownerMember) return []
    return balances.lines
      .filter((l) => l.member_id === viewerMember.id && l.amount > 0)
      .map((l) => ({
        name: ownerMember.name,
        amountText: formatCurrency(Number(l.amount), l.currency, locale),
      }))
  }, [balances, isOwner, viewerMember, ownerMember, memberNameById, locale])

  const monthlyData = useMemo(() => {
    if (!groupTxs || groupTxs.length === 0) return []
    const byMonth = new Map<string, number>()
    for (const tx of groupTxs) {
      const m = tx.date.slice(0, 7)
      byMonth.set(m, (byMonth.get(m) ?? 0) + Number(tx.amount_primary ?? tx.amount))
    }
    return Array.from(byMonth.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([month, total]) => ({
        month: new Date(month + '-01').toLocaleString(dateLocale, { month: 'short' }),
        total: Number(total.toFixed(2)),
      }))
  }, [groupTxs, locale, dateLocale])

  // Group spending broken down by category — for the stacked horizontal
  // bar. We sum debits only (income/credits aren't "spending"). When a
  // tx has amount_primary we use that so cross-currency rows are
  // comparable; otherwise fall back to the native amount, which is fine
  // for single-currency groups.
  const categoryBreakdown = useMemo(() => {
    if (!groupTxs || groupTxs.length === 0) return [] as { id: string; name: string; color: string; total: number }[]
    const map = new Map<string, { id: string; name: string; color: string; total: number }>()
    for (const tx of groupTxs) {
      if (tx.type !== 'debit') continue
      const id = tx.category?.id ?? 'uncategorized'
      const name = tx.category?.name ?? t('splitGroups.uncategorized')
      const color = tx.category?.color ?? '#6B7280'
      const value = Number(tx.amount_primary ?? tx.amount)
      const existing = map.get(id)
      if (existing) {
        existing.total += value
      } else {
        map.set(id, { id, name, color, total: value })
      }
    }
    return Array.from(map.values()).sort((a, b) => b.total - a.total)
  }, [groupTxs, t])

  const categoryBreakdownTotal = useMemo(
    () => categoryBreakdown.reduce((s, c) => s + c.total, 0),
    [categoryBreakdown],
  )

  if (loadingGroup) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-12 w-64" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }
  if (!group) {
    return <div className="text-muted-foreground">{t('splitGroups.notFound')}</div>
  }

  return (
    <div className="space-y-4">
      <PageHeader
        section={t('splitGroups.section')}
        title={group.name}
        action={
          <div className="flex items-center gap-2">
            {!isOwner && (
              <span className="text-xs bg-muted text-muted-foreground px-2 py-1 rounded-full">
                {t('splitGroups.sharedWithYou')}
              </span>
            )}
            <Button variant="outline" onClick={() => navigate('/groups')}>
              <ArrowLeft size={14} className="mr-1" />
              {t('common.back')}
            </Button>
          </div>
        }
      />

      {/* KPI row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4">
        <KpiCard
          label={t('splitGroups.kpiTotalMoved')}
          value={formatCurrency(totalMoved, groupCurrency, locale)}
          icon={Wallet}
        />
        <KpiCard
          label={t(isOwner ? 'splitGroups.kpiOwedToYou' : 'splitGroups.kpiOwedToYouAsMember')}
          value={formatCurrency(owedToViewer, groupCurrency, locale)}
          icon={TrendingUp}
          tone={owedToViewer > 0 ? 'positive' : 'neutral'}
          breakdown={owedToViewerBreakdown}
        />
        <KpiCard
          label={t('splitGroups.kpiYouOwe')}
          value={formatCurrency(viewerOwes, groupCurrency, locale)}
          icon={TrendingDown}
          tone={viewerOwes > 0 ? 'negative' : 'neutral'}
          breakdown={viewerOwesBreakdown}
        />
      </div>

      {/* Spending trend (compact) */}
      {monthlyData.length > 1 && (
        <SectionCard>
          <SectionHeader
            title={t('splitGroups.spendingTrend')}
            description={t('splitGroups.spendingTrendHint')}
          />
          <div className="px-2 py-3">
            <ResponsiveContainer width="100%" height={120}>
              <BarChart data={monthlyData}>
                <XAxis
                  dataKey="month"
                  tick={{ fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  cursor={{ fill: 'var(--muted)' }}
                  contentStyle={{
                    fontSize: 12,
                    borderRadius: 8,
                    border: '1px solid var(--border)',
                    background: 'var(--card)',
                  }}
                  formatter={(v) => formatCurrency(Number(v ?? 0), groupCurrency, locale)}
                />
                <Bar dataKey="total" fill={group.color} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </SectionCard>
      )}

      {/* Category distribution — single horizontal stacked bar split
          by category, with a legend showing absolute and % per slice. */}
      {categoryBreakdownTotal > 0 && (
        <SectionCard>
          <SectionHeader
            title={t('splitGroups.byCategory')}
            description={t('splitGroups.byCategoryHint')}
          />
          <div className="px-4 py-3 space-y-3">
            <div className="flex h-3 w-full overflow-hidden rounded-full bg-muted">
              {categoryBreakdown.map((c) => {
                const pct = (c.total / categoryBreakdownTotal) * 100
                return (
                  <div
                    key={c.id}
                    style={{ width: `${pct}%`, backgroundColor: c.color }}
                    title={`${c.name} · ${formatCurrency(c.total, groupCurrency, locale)} (${pct.toFixed(1)}%)`}
                  />
                )
              })}
            </div>
            <ul className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 text-xs">
              {categoryBreakdown.map((c) => {
                const pct = (c.total / categoryBreakdownTotal) * 100
                return (
                  <li key={c.id} className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-2 min-w-0">
                      <span
                        className="h-2.5 w-2.5 rounded-full shrink-0"
                        style={{ backgroundColor: c.color }}
                      />
                      <span className="truncate">{c.name}</span>
                    </span>
                    <span className="tabular-nums whitespace-nowrap text-muted-foreground">
                      {formatCurrency(c.total, groupCurrency, locale)} · {pct.toFixed(0)}%
                    </span>
                  </li>
                )
              })}
            </ul>
          </div>
        </SectionCard>
      )}

      {/* 2×2 grid: members + balances on top, transactions + settlements below */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 sm:gap-4">
      {/* Members */}
      <SectionCard>
        <SectionHeader
          title={t('splitGroups.members')}
          action={
            isOwner && canWrite ? (
              <Button size="sm" className="gap-1.5 h-8" onClick={openCreateMember}>
                <UserPlus size={13} />
                {t('splitGroups.addMember')}
              </Button>
            ) : undefined
          }
        />
        {group.members.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground text-sm">
            {t('splitGroups.noMembers')}
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {group.members.map((member) => (
              <li key={member.id} className="flex items-center justify-between px-4 py-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{member.name}</span>
                    {/* "(you)" only marks the actual viewer. is_self
                        identifies the group owner/payer; for non-owner
                        viewers we show that as "(owner)" instead so
                        the badge isn't misleading. */}
                    {viewerMember?.id === member.id ? (
                      <span className="text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full">
                        {t('splitGroups.you')}
                      </span>
                    ) : member.is_self && isOwner ? (
                      <span className="text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full">
                        {t('splitGroups.you')}
                      </span>
                    ) : member.is_self ? (
                      <span className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded-full">
                        {t('splitGroups.ownerBadge')}
                      </span>
                    ) : null}
                  </div>
                  {member.email && (
                    <p className="text-xs text-muted-foreground inline-flex items-center gap-1">
                      {member.linked_user_id && <Link2 size={10} />}
                      {member.email}
                    </p>
                  )}
                </div>
                {isOwner && canWrite && (
                  <Button variant="ghost" size="sm" onClick={() => openEditMember(member)}>
                    {t('common.edit')}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      {/* Balances */}
      <SectionCard>
        <SectionHeader
          title={t('splitGroups.balances')}
          description={t('splitGroups.balancesHint')}
        />
        {balances && balances.lines.length > 0 ? (
          <ul className="divide-y divide-border">
            {balances.lines.map((line, idx) => {
              const positive = line.amount > 0
              // Reframe the line per viewer:
              //   - Owner sees "X owes you" / "you owe X" (their direct relationship).
              //   - A linked member sees their own line as "you owe / owes you {owner}",
              //     and other lines as "{name} owes / is owed by {owner}".
              const ownerName = ownerMember?.name ?? '—'
              const otherName = memberName_(line.member_id)
              const isViewerLine = viewerMember?.id === line.member_id
              const label = isOwner
                ? positive
                  ? t('splitGroups.owesYou', { name: otherName })
                  : t('splitGroups.youOwe', { name: otherName })
                : isViewerLine
                  ? positive
                    ? t('splitGroups.youOwe', { name: ownerName })
                    : t('splitGroups.ownerOwesYou', { name: ownerName })
                  : positive
                    ? t('splitGroups.thirdPartyOwes', { name: otherName, owner: ownerName })
                    : t('splitGroups.thirdPartyOwed', { name: otherName, owner: ownerName })
              return (
                <li
                  key={`${line.member_id}-${line.currency}-${idx}`}
                  className="flex items-center justify-between px-4 py-3"
                >
                  <div className="text-sm">{label}</div>
                  <div className="flex items-center gap-3">
                    <span
                      className={`text-sm font-semibold tabular-nums ${
                        positive ? 'text-emerald-600' : 'text-rose-500'
                      }`}
                    >
                      {formatCurrency(Math.abs(line.amount), line.currency, locale)}
                    </span>
                    {(() => {
                      // Show "Acertar" if the viewer can act on this line:
                      // - Owner can act on any line
                      // - Linked member can only act on their own debt line
                      //   (positive amount = they owe the owner)
                      const canActLinked = !isOwner && isViewerLine && positive
                      if (!isOwner && !canActLinked) return null
                      if (!canWrite) return null
                      return (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            if (!balances.self_member_id) return
                            if (positive) {
                              // Member owes the owner → from = member, to = owner
                              openSettleUp(line.member_id, balances.self_member_id, Math.abs(line.amount), line.currency)
                            } else {
                              // Owner owes the member → from = owner, to = member
                              openSettleUp(balances.self_member_id, line.member_id, Math.abs(line.amount), line.currency)
                            }
                          }}
                        >
                          {canActLinked ? t('splitGroups.payNow') : t('splitGroups.settleUp')}
                        </Button>
                      )
                    })()}
                  </div>
                </li>
              )
            })}
          </ul>
        ) : (
          <div className="text-center py-6 text-muted-foreground text-sm">
            {t('splitGroups.allSettled')}
          </div>
        )}
      </SectionCard>

      {/* Recent transactions */}
      <SectionCard>
        <SectionHeader
          title={t('splitGroups.recentTransactions')}
          description={t('splitGroups.recentTransactionsHint')}
          action={
            groupTxs && groupTxs.length > 0 ? (
              <Button
                variant="ghost"
                size="sm"
                className="gap-1 h-8 text-xs"
                onClick={() => navigate(`/transactions?group_id=${groupId}`)}
              >
                {t('splitGroups.viewAllTransactions')}
                <ArrowRight size={12} />
              </Button>
            ) : undefined
          }
        />
        {!groupTxs ? (
          <div className="p-4 space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : groupTxs.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground text-sm flex flex-col items-center gap-2">
            <Receipt size={20} className="opacity-50" />
            {t('splitGroups.noTransactions')}
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {groupTxs.slice(0, 8).map((tx) => (
              <li
                key={tx.id}
                className="flex items-center gap-3 px-4 py-3 hover:bg-muted cursor-pointer transition-colors"
                onClick={() => navigate(`/transactions?group_id=${groupId}&highlight=${tx.id}`)}
              >
                <CategoryIcon
                  icon={tx.category?.icon}
                  color={tx.category?.color}
                  size="md"
                />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-foreground truncate">
                    {tx.description}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {new Date(tx.date + 'T00:00:00').toLocaleDateString(dateLocale)}
                    {tx.category?.name ? ` · ${tx.category.name}` : ''}
                    {tx.splits && tx.splits.length > 0
                      ? ` · ${t('splitGroups.splitWays', { count: tx.splits.length })}`
                      : ''}
                  </p>
                </div>
                <span
                  className={`text-sm font-semibold tabular-nums ml-3 ${
                    tx.type === 'debit' ? 'text-rose-500' : 'text-emerald-600'
                  }`}
                >
                  {formatCurrency(Number(tx.amount), tx.currency, locale)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      {/* Settlements */}
      <SectionCard>
        <SectionHeader
          title={t('splitGroups.settlements')}
          action={
            isOwner && canWrite ? (
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5 h-8"
                onClick={() => openSettleUp()}
              >
                {t('splitGroups.recordSettlement')}
              </Button>
            ) : undefined
          }
        />
        {settlements && settlements.length > 0 ? (
          <ul className="divide-y divide-border">
            {settlements.map((s: GroupSettlement) => (
              <li key={s.id} className="flex items-center justify-between px-4 py-3">
                <div className="flex-1 min-w-0">
                  <div className="text-sm flex items-center gap-1.5">
                    <span className="font-medium">{memberName_(s.from_member_id)}</span>
                    <ArrowRight size={12} className="text-muted-foreground" />
                    <span className="font-medium">{memberName_(s.to_member_id)}</span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {new Date(s.date + 'T00:00:00').toLocaleDateString(dateLocale)}
                    {s.notes ? ` · ${s.notes}` : ''}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-sm font-semibold tabular-nums">
                    {formatCurrency(s.amount, s.currency, locale)}
                  </span>
                  {isOwner && canWrite && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => deleteSettlementMutation.mutate(s.id)}
                      title={t('common.delete')}
                      aria-label={t('common.delete')}
                    >
                      <Trash2 size={14} />
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-center py-6 text-muted-foreground text-sm">
            {t('splitGroups.noSettlements')}
          </div>
        )}
      </SectionCard>
      </div>

      {/* Member dialog */}
      <Dialog open={memberDialogOpen} onOpenChange={setMemberDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {editingMember ? t('splitGroups.editMember') : t('splitGroups.addMember')}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            {/* Pick an existing Securo user, or leave as "non-user" to
                add someone without an account (the most common case for
                roommates/relatives who don't use the app yet). When a
                user is picked, name+email come from their account and
                the inputs are locked — switch back to "Sem vínculo" to
                edit them by hand. */}
            <div className="space-y-2">
              <Label>{t('splitGroups.linkedUser')}</Label>
              <select
                className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background h-9 focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
                value={memberLinkedUserId ?? ''}
                onChange={(e) => {
                  const id = e.target.value || null
                  setMemberLinkedUserId(id)
                  if (id) {
                    const picked = userDirectory?.find((u) => u.id === id)
                    if (picked) {
                      // Always reflect the currently picked user — the
                      // inputs are locked, so the source of truth is
                      // whatever account is selected here.
                      setMemberEmail(picked.email)
                      setMemberName(picked.email.split('@')[0])
                    }
                  }
                }}
              >
                <option value="">{t('splitGroups.linkedUserNone')}</option>
                {(userDirectory ?? []).map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.email}
                    {u.id === user?.id ? ` (${t('splitGroups.you')})` : ''}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">
                {t('splitGroups.linkedUserHint')}
              </p>
            </div>
            <div className="space-y-2">
              <Label>{t('splitGroups.memberName')}</Label>
              <Input
                value={memberName}
                onChange={(e) => setMemberName(e.target.value)}
                disabled={memberLinkedUserId !== null}
              />
            </div>
            <div className="space-y-2">
              <Label>{t('splitGroups.memberEmail')}</Label>
              <Input
                type="email"
                value={memberEmail}
                onChange={(e) => setMemberEmail(e.target.value)}
                disabled={memberLinkedUserId !== null}
              />
              {memberLinkedUserId !== null ? (
                <p className="text-xs text-emerald-600 inline-flex items-center gap-1">
                  <Link2 size={11} />
                  {t('splitGroups.willLinkToUser', { email: memberEmail })}
                </p>
              ) : lookupResult ? (
                <p className="text-xs text-emerald-600 inline-flex items-center gap-1">
                  <Link2 size={11} />
                  {t('splitGroups.willLinkToUser', { email: lookupResult.email })}
                </p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {t('splitGroups.memberEmailHint')}
                </p>
              )}
            </div>
          </div>
          <DialogFooter className={editingMember ? 'flex justify-between sm:justify-between' : ''}>
            {editingMember && (
              <Button
                variant="destructive"
                onClick={() => deleteMemberMutation.mutate(editingMember.id)}
                disabled={deleteMemberMutation.isPending}
              >
                <Trash2 size={14} className="mr-1" />
                {t('common.delete')}
              </Button>
            )}
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setMemberDialogOpen(false)}>
                {t('common.cancel')}
              </Button>
              <Button
                onClick={saveMember}
                disabled={!memberName.trim() || memberMutation.isPending}
              >
                {t('common.save')}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Settle dialog */}
      <Dialog open={settleOpen} onOpenChange={setSettleOpen}>
        <DialogContent className="sm:max-w-md max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{t('splitGroups.recordSettlement')}</DialogTitle>
          </DialogHeader>
          {(() => {
            const myMemberId = viewerMember?.id ?? (isOwner ? ownerMember?.id : null)
            const viewerIsPayer = !!myMemberId && settleFrom === myMemberId
            return (
          <div className="space-y-4 min-w-0">
            <div className="space-y-2">
              <Label>{t('splitGroups.from')}</Label>
              <select
                className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background"
                value={settleFrom}
                onChange={(e) => {
                  setSettleFrom(e.target.value)
                  // Reset the ledger-side options: only meaningful
                  // when the viewer is the payer.
                  setSettleTxMode('none')
                  setSettleAccountId('')
                  setSettlePickedTx(null)
                  setSettleTxSearch('')
                }}
              >
                <option value="">{t('splitGroups.selectMember')}</option>
                {group.members.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <Label>{t('splitGroups.to')}</Label>
              <select
                className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background"
                value={settleTo}
                onChange={(e) => setSettleTo(e.target.value)}
              >
                <option value="">{t('splitGroups.selectMember')}</option>
                {group.members.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </div>
            {/* Transaction action — placed right after the members so
                the payer can decide upfront whether a real transaction
                will back this settlement. When linking an existing
                transaction, the amount/currency/date below mirror that
                transaction and lock so the two records can't disagree. */}
            {viewerIsPayer && (
                <div className="space-y-2">
                  <Label>{t('splitGroups.txAction')}</Label>
                  <select
                    className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background"
                    value={settleTxMode}
                    onChange={(e) => {
                      setSettleTxMode(e.target.value as 'none' | 'create' | 'existing')
                      setSettleAccountId('')
                      setSettlePickedTx(null)
                      setSettleTxSearch('')
                    }}
                  >
                    <option value="none">{t('splitGroups.txActionNone')}</option>
                    <option value="create">{t('splitGroups.txActionCreate')}</option>
                    <option value="existing">{t('splitGroups.txActionExisting')}</option>
                  </select>
                  {settleTxMode === 'create' && (
                    <div className="space-y-1">
                      <select
                        className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background"
                        value={settleAccountId}
                        onChange={(e) => setSettleAccountId(e.target.value)}
                      >
                        <option value="">{t('splitGroups.selectAccount')}</option>
                        {(accountsList ?? []).map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.display_name || a.name}
                          </option>
                        ))}
                      </select>
                      <p className="text-xs text-muted-foreground">
                        {t('splitGroups.affectAccountHint')}
                      </p>
                    </div>
                  )}
                  {settleTxMode === 'existing' && (
                    <div className="space-y-1.5">
                      <Input
                        type="text"
                        value={settleTxSearch}
                        onChange={(e) => setSettleTxSearch(e.target.value)}
                        placeholder={t('splitGroups.searchTransaction')}
                      />
                      <div className="max-h-44 overflow-y-auto rounded-md border border-border divide-y divide-border">
                        {(settleTxOptions?.items ?? []).length === 0 ? (
                          <p className="text-xs text-muted-foreground px-3 py-4 text-center">
                            {t('splitGroups.noTransactions')}
                          </p>
                        ) : (
                          (settleTxOptions?.items ?? []).map((tx) => {
                            const picked = settlePickedTx?.id === tx.id
                            return (
                              <button
                                key={tx.id}
                                type="button"
                                onClick={() => {
                                  // Picking an existing transaction *as* the
                                  // settlement: align amount, currency and
                                  // date so the two records can't disagree.
                                  setSettlePickedTx(tx)
                                  setSettleAmount(Number(tx.amount).toFixed(2))
                                  setSettleCurrency(tx.currency)
                                  setSettleDate(tx.date)
                                }}
                                className={`w-full text-left px-3 py-2 text-sm flex items-center justify-between gap-3 ${
                                  picked ? 'bg-primary/10' : 'hover:bg-muted/50'
                                }`}
                              >
                                <span className="min-w-0 truncate">
                                  <span className="text-muted-foreground">{tx.date}</span> ·{' '}
                                  {tx.description}
                                </span>
                                <span className="shrink-0 tabular-nums text-muted-foreground">
                                  {tx.amount} {tx.currency}
                                </span>
                              </button>
                            )
                          })
                        )}
                      </div>
                      {settlePickedTx && (
                        <p className="text-xs text-muted-foreground truncate">
                          {t('splitGroups.selectedTransaction')}: {settlePickedTx.date} ·{' '}
                          {settlePickedTx.description}
                        </p>
                      )}
                    </div>
                  )}
                </div>
            )}
            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-2 col-span-2">
                <Label>{t('splitGroups.amount')}</Label>
                <Input
                  type="number"
                  step="0.01"
                  value={settleAmount}
                  onChange={(e) => setSettleAmount(e.target.value)}
                  disabled={settleTxMode === 'existing'}
                />
              </div>
              <div className="space-y-2">
                <Label>{t('splitGroups.currency')}</Label>
                <Input
                  value={settleCurrency}
                  maxLength={3}
                  onChange={(e) => setSettleCurrency(e.target.value.toUpperCase())}
                  disabled={settleTxMode === 'existing'}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t('splitGroups.date')}</Label>
              <DatePickerInput
                value={settleDate}
                onChange={setSettleDate}
                className="w-full justify-start"
                disabled={settleTxMode === 'existing'}
              />
            </div>
            <div className="space-y-2">
              <Label>{t('splitGroups.notes')}</Label>
              <textarea
                className="w-full border border-input rounded-md px-3 py-2 text-sm bg-background resize-none"
                rows={2}
                value={settleNotes}
                onChange={(e) => setSettleNotes(e.target.value)}
              />
            </div>
          </div>
            )
          })()}
          <DialogFooter>
            <Button variant="outline" onClick={() => setSettleOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={saveSettlement}
              disabled={
                !settleFrom ||
                !settleTo ||
                settleFrom === settleTo ||
                !settleAmount ||
                (settleTxMode === 'create' && !settleAccountId) ||
                (settleTxMode === 'existing' && !settlePickedTx) ||
                settlementMutation.isPending
              }
            >
              {t('common.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
