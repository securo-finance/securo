import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Users } from 'lucide-react'
import { toast } from 'sonner'

import { groups as groupsApi, type GroupCreatePayload } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import type { Group, GroupKind, ShareType, TransactionSplitsInput } from '@/types'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { GroupForm } from '@/components/group-form'
import { MemberForm } from '@/components/member-form'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'

interface RowState {
  member_id: string
  selected: boolean
  amount: string
  percent: string
}

function buildRows(group: Group | null | undefined, current: TransactionSplitsInput | null): RowState[] {
  if (!group) return []
  // Pydantic serializes Decimal as a string, so values arriving from
  // the API may be either number or string. Coerce both shapes.
  const toNum = (v: unknown): number | null => {
    if (v == null) return null
    const n = typeof v === 'number' ? v : Number(v)
    return Number.isFinite(n) ? n : null
  }
  const byMember = new Map<string, { amount: number | null; pct: number | null }>()
  for (const split of current?.splits ?? []) {
    byMember.set(split.group_member_id, {
      amount: toNum(split.share_amount),
      pct: toNum(split.share_pct),
    })
  }
  return group.members.map((m) => {
    const existing = byMember.get(m.id)
    return {
      member_id: m.id,
      selected: !!existing,
      amount: existing?.amount != null ? existing.amount.toFixed(2) : '',
      percent: existing?.pct != null ? existing.pct.toString() : '',
    }
  })
}

export function TransactionSplitsSection({
  amount,
  currency,
  value,
  onChange,
  onValidityChange,
}: {
  amount: number
  currency: string
  value: TransactionSplitsInput | null
  onChange: (next: TransactionSplitsInput | null) => void
  onValidityChange?: (valid: boolean) => void
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const [enabled, setEnabled] = useState(value !== null)
  const [groupId, setGroupId] = useState<string>('')
  const [shareType, setShareType] = useState<ShareType>(value?.share_type ?? 'equal')
  const [rows, setRows] = useState<RowState[]>([])
  // Snapshot of the initial value so row hydration survives the
  // first push-state-up cycle (which zeros the parent before the
  // group has finished loading).
  const seedRef = useRef<TransactionSplitsInput | null>(value)
  // Once rows have been hydrated for the seeded value, stop applying
  // it — further edits are user-driven.
  const hydratedRef = useRef(false)

  const queryClient = useQueryClient()
  const [isCreatingGroup, setIsCreatingGroup] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')
  const [newGroupKind, setNewGroupKind] = useState<GroupKind>('social')
  const [newGroupCurrency, setNewGroupCurrency] = useState(currency)
  const [newGroupNotes, setNewGroupNotes] = useState('')

  // Sync newGroupCurrency default value if currency prop changes.
  useEffect(() => {
    setNewGroupCurrency(currency)
  }, [currency])

  const createGroupMutation = useMutation({
    mutationFn: (payload: GroupCreatePayload) => groupsApi.create(payload),
    onSuccess: (newGroup) => {
      queryClient.invalidateQueries({ queryKey: ['groups'] })
      setGroupId(newGroup.id)
      setIsCreatingGroup(false)
      setNewGroupName('')
      setNewGroupNotes('')
      toast.success(t('splitGroups.created'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const handleCreateGroup = () => {
    if (!newGroupName.trim()) return
    createGroupMutation.mutate({
      name: newGroupName.trim(),
      kind: newGroupKind,
      default_currency: newGroupCurrency,
      notes: newGroupNotes.trim() || null,
    })
  }

  const [isAddingMember, setIsAddingMember] = useState(false)
  const [newMemberName, setNewMemberName] = useState('')
  const [newMemberEmail, setNewMemberEmail] = useState('')
  const [newMemberLinkedUserId, setNewMemberLinkedUserId] = useState<string | null>(null)

  const createMemberMutation = useMutation({
    mutationFn: (payload: { name: string; email?: string | null; linked_user_id?: string | null }) =>
      groupsApi.members.create(groupId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups', groupId] })
      setIsAddingMember(false)
      setNewMemberName('')
      setNewMemberEmail('')
      setNewMemberLinkedUserId(null)
      toast.success(t('splitGroups.memberAdded'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const handleCreateMember = () => {
    if (!newMemberName.trim()) return
    createMemberMutation.mutate({
      name: newMemberName.trim(),
      email: newMemberEmail.trim() || null,
      linked_user_id: newMemberLinkedUserId,
    })
  }

  // Reset creation state if splits are disabled
  useEffect(() => {
    if (!enabled) {
      setIsCreatingGroup(false)
      setIsAddingMember(false)
    }
  }, [enabled])

  const { data: groups } = useQuery({
    queryKey: ['groups'],
    queryFn: () => groupsApi.list(false),
  })

  const { data: group } = useQuery({
    queryKey: ['groups', groupId],
    queryFn: () => groupsApi.get(groupId),
    enabled: !!groupId && !isCreatingGroup && !isAddingMember,
  })

  // Auto-pick the group when splits are enabled. If the parent seeded a
  // value (edit flow), look up which group the existing split members
  // belong to so the dialog opens on the right one. Otherwise fall back
  // to the first group.
  useEffect(() => {
    if (!enabled || groupId || !groups || groups.length === 0) return
    const seededIds = new Set((seedRef.current?.splits ?? []).map((s) => s.group_member_id))
    if (seededIds.size > 0) {
      const match = groups.find((g) => g.members.some((m) => seededIds.has(m.id)))
      if (match) {
        setGroupId(match.id)
        return
      }
    }
    setGroupId(groups[0].id)
  }, [enabled, groupId, groups])

  const lastGroupIdRef = useRef<string | null>(null)

  // Rebuild rows when the group changes or when members are added.
  // Use the seed snapshot only on the first hydration so the parent's
  // value doesn't get zeroed by the push-state-up effect.
  useEffect(() => {
    if (!group) return

    const groupChanged = lastGroupIdRef.current !== group.id
    lastGroupIdRef.current = group.id

    setRows((prevRows) => {
      // If first hydration or switched groups, rebuild completely
      if (!hydratedRef.current || groupChanged) {
        const source = hydratedRef.current ? null : seedRef.current
        return buildRows(group, source)
      }

      // Otherwise, merge new group members into existing rows state to preserve user selections
      const prevMap = new Map(prevRows.map((r) => [r.member_id, r]))
      return group.members.map((m) => {
        const existing = prevMap.get(m.id)
        if (existing) return existing
        return {
          member_id: m.id,
          selected: false,
          amount: '',
          percent: '',
        }
      })
    })

    hydratedRef.current = true
  }, [group])

  // Push state up whenever it changes meaningfully.
  useEffect(() => {
    if (!enabled) {
      onChange(null)
      return
    }
    const selected = rows.filter((r) => r.selected)
    if (selected.length === 0) {
      onChange(null)
      return
    }
    const splits = selected.map((r) => {
      if (shareType === 'exact') {
        return {
          group_member_id: r.member_id,
          share_amount: r.amount ? parseFloat(r.amount) : 0,
        }
      }
      if (shareType === 'percent') {
        return {
          group_member_id: r.member_id,
          share_pct: r.percent ? parseFloat(r.percent) : 0,
        }
      }
      return { group_member_id: r.member_id }
    })
    onChange({ share_type: shareType, splits })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, shareType, rows])

  // Validation summary
  const total = useMemo(() => {
    if (!enabled) return null
    const selected = rows.filter((r) => r.selected)
    if (selected.length === 0) return null
    if (shareType === 'equal') {
      return amount
    }
    if (shareType === 'exact') {
      return selected.reduce((sum, r) => sum + (parseFloat(r.amount) || 0), 0)
    }
    return selected.reduce((sum, r) => sum + (parseFloat(r.percent) || 0), 0)
  }, [enabled, shareType, rows, amount])

  // True when the splits payload is acceptable for the backend. Equal mode
  // always materializes correctly; exact must sum to the parent amount;
  // percent must sum to exactly 100. Reported up so the parent dialog can
  // gate its save button instead of relying on a 400 round-trip.
  const isValid = useMemo(() => {
    if (!enabled) return true
    const selected = rows.filter((r) => r.selected)
    if (selected.length === 0) return false
    if (shareType === 'equal') return true
    if (shareType === 'exact') {
      const sum = selected.reduce((s, r) => s + (parseFloat(r.amount) || 0), 0)
      return Math.abs(sum - Math.abs(amount)) < 0.005
    }
    const pctSum = selected.reduce((s, r) => s + (parseFloat(r.percent) || 0), 0)
    return Math.abs(pctSum - 100) < 0.005
  }, [enabled, shareType, rows, amount])

  useEffect(() => {
    onValidityChange?.(isValid)
  }, [isValid, onValidityChange])

  const updateRow = (memberId: string, patch: Partial<RowState>) => {
    setRows((prev) => prev.map((r) => (r.member_id === memberId ? { ...r, ...patch } : r)))
  }

  return (
    <div className="space-y-3 pt-2 border-t border-border">
      <label className="text-sm font-medium inline-flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="h-4 w-4 rounded border-border accent-primary"
        />
        <Users size={14} />
        {t('splitGroups.splitTransaction')}
      </label>

      {enabled && (
        <div className="space-y-3 pl-6">
          {!groups || groups.length === 0 ? (
            <div className="space-y-2 py-2">
              <p className="text-xs text-muted-foreground font-semibold">
                {t('splitGroups.splitNoGroups')}
              </p>
              <p className="text-xs text-muted-foreground">
                {t('splitGroups.splitNoGroupsLinkPrefix')}
                <button
                  type="button"
                  onClick={() => {
                    setIsCreatingGroup(true)
                    setNewGroupName('')
                    setNewGroupKind('social')
                    setNewGroupCurrency(currency)
                    setNewGroupNotes('')
                  }}
                  className="text-primary hover:underline font-semibold"
                >
                  {t('splitGroups.splitNoGroupsLinkSuffix')}
                </button>
                .
              </p>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs">{t('splitGroups.group')}</Label>
                    <button
                      type="button"
                      onClick={() => {
                        setIsCreatingGroup(true)
                        setNewGroupName('')
                        setNewGroupKind('social')
                        setNewGroupCurrency(currency)
                        setNewGroupNotes('')
                      }}
                      className="text-xs text-primary hover:underline font-medium"
                    >
                      + {t('splitGroups.add')}
                    </button>
                  </div>
                  <select
                    className="w-full border border-border rounded-md px-2 py-1.5 text-sm bg-background"
                    value={groupId}
                    onChange={(e) => setGroupId(e.target.value)}
                  >
                    {groups.map((g) => (
                      <option key={g.id} value={g.id}>
                        {g.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">{t('splitGroups.shareType')}</Label>
                  <select
                    className="w-full border border-border rounded-md px-2 py-1.5 text-sm bg-background"
                    value={shareType}
                    onChange={(e) => setShareType(e.target.value as ShareType)}
                  >
                    <option value="equal">{t('splitGroups.shareEqual')}</option>
                    <option value="exact">{t('splitGroups.shareExact')}</option>
                    <option value="percent">{t('splitGroups.sharePercent')}</option>
                  </select>
                </div>
              </div>

              {group && (
                <div className="space-y-2">
                  {group.members.length > 0 && (
                    <div className="flex items-center justify-between border-t border-border pt-2 mt-2">
                      <Label className="text-xs">{t('splitGroups.members')}</Label>
                      <button
                        type="button"
                        onClick={() => {
                          setIsAddingMember(true)
                          setNewMemberName('')
                          setNewMemberEmail('')
                          setNewMemberLinkedUserId(null)
                        }}
                        className="text-xs text-primary hover:underline font-medium"
                      >
                        + {t('splitGroups.addMember')}
                      </button>
                    </div>
                  )}
                  {group.members.length === 0 ? (
                    <div className="py-2 text-center">
                      <p className="text-xs text-muted-foreground mb-2">
                        {t('splitGroups.splitNoMembers')}
                      </p>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          setIsAddingMember(true)
                          setNewMemberName('')
                          setNewMemberEmail('')
                          setNewMemberLinkedUserId(null)
                        }}
                      >
                        + {t('splitGroups.addMember')}
                      </Button>
                    </div>
                  ) : (
                    (() => {
                      const selectedCount = rows.filter((r) => r.selected).length
                      const absAmount = Math.abs(amount)
                      return group.members.map((m) => {
                        const row = rows.find((r) => r.member_id === m.id)
                        if (!row) return null
                        const computed: number | null = !row.selected
                          ? null
                          : shareType === 'equal'
                            ? selectedCount > 0
                              ? absAmount / selectedCount
                              : null
                            : shareType === 'percent'
                              ? (parseFloat(row.percent) || 0) * absAmount / 100
                              : null
                        return (
                          <div key={m.id} className="flex items-center gap-2">
                            <label className="flex items-center gap-2 flex-1 min-w-0 cursor-pointer">
                              <input
                                type="checkbox"
                                checked={row.selected}
                                onChange={(e) =>
                                  updateRow(m.id, { selected: e.target.checked })
                                }
                                className="h-4 w-4 rounded border-border accent-primary"
                              />
                              <span className="text-sm flex-1 min-w-0 truncate">
                                {m.name}
                                {m.is_self && (
                                  <span className="ml-1.5 text-xs text-primary">
                                    ({t('splitGroups.you')})
                                  </span>
                                )}
                              </span>
                            </label>
                            {computed !== null && (
                              <span className="text-xs text-muted-foreground tabular-nums">
                                {formatCurrency(computed, currency, locale)}
                              </span>
                            )}
                            {shareType === 'exact' && row.selected && (
                              <Input
                                type="number"
                                step="0.01"
                                className="w-24 h-8 text-sm"
                                value={row.amount}
                                onChange={(e) => updateRow(m.id, { amount: e.target.value })}
                              />
                            )}
                            {shareType === 'percent' && row.selected && (
                              <div className="flex items-center gap-1">
                                <Input
                                  type="number"
                                  step="0.01"
                                  className="w-20 h-8 text-sm"
                                  value={row.percent}
                                  onChange={(e) => updateRow(m.id, { percent: e.target.value })}
                                />
                                <span className="text-xs text-muted-foreground">%</span>
                              </div>
                            )}
                          </div>
                        )
                      })
                    })()
                  )}
                </div>
              )}

              {total !== null && (
                <div className="text-xs text-muted-foreground">
                  {shareType === 'percent' ? (
                    <span className={total === 100 ? 'text-emerald-600' : 'text-amber-600'}>
                      {t('splitGroups.percentSum', { total: total.toFixed(2) })}
                    </span>
                  ) : shareType === 'exact' ? (
                    <span
                      className={
                        Math.abs(total - Math.abs(amount)) < 0.005
                          ? 'text-emerald-600'
                          : 'text-amber-600'
                      }
                    >
                      {t('splitGroups.amountSum', {
                        total: total.toFixed(2),
                        target: Math.abs(amount).toFixed(2),
                        currency,
                      })}
                    </span>
                  ) : (
                    <span>{t('splitGroups.equalHint')}</span>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}

      <Dialog open={isCreatingGroup} onOpenChange={setIsCreatingGroup}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t('splitGroups.add')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <GroupForm
              name={newGroupName}
              onChangeName={setNewGroupName}
              kind={newGroupKind}
              onChangeKind={setNewGroupKind}
              defaultCurrency={newGroupCurrency}
              onChangeDefaultCurrency={setNewGroupCurrency}
              notes={newGroupNotes}
              onChangeNotes={setNewGroupNotes}
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setIsCreatingGroup(false)}
              disabled={createGroupMutation.isPending}
            >
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button
              type="button"
              onClick={handleCreateGroup}
              disabled={!newGroupName.trim() || createGroupMutation.isPending}
            >
              {createGroupMutation.isPending ? t('common.saving') : t('splitGroups.add')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isAddingMember} onOpenChange={setIsAddingMember}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t('splitGroups.addMember')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <MemberForm
              name={newMemberName}
              onChangeName={setNewMemberName}
              email={newMemberEmail}
              onChangeEmail={setNewMemberEmail}
              linkedUserId={newMemberLinkedUserId}
              onChangeLinkedUserId={setNewMemberLinkedUserId}
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setIsAddingMember(false)}
              disabled={createMemberMutation.isPending}
            >
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button
              type="button"
              onClick={handleCreateMember}
              disabled={!newMemberName.trim() || createMemberMutation.isPending}
            >
              {createMemberMutation.isPending ? t('common.saving') : t('common.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
