import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { groups as groupsApi, type GroupCreatePayload } from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { GroupKind } from '@/types'
import { GroupForm } from '@/components/group-form'
import { MemberForm } from '@/components/member-form'

type BulkShareType = 'equal' | 'percent'

interface MemberSelection {
  selected: boolean
  percent: string
}

export interface BulkAddToGroupSubmission {
  groupId: string
  share_type: BulkShareType
  member_splits: { group_member_id: string; share_pct?: number }[]
}

export function BulkAddToGroupDialog({
  open,
  onClose,
  selectedCount,
  onSubmit,
  isPending,
}: {
  open: boolean
  onClose: () => void
  selectedCount: number
  onSubmit: (payload: BulkAddToGroupSubmission) => void
  isPending: boolean
}) {
  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose() }}>
      <DialogContent className="sm:max-w-md">
        {/* DialogContent only renders while open, so the form below
            mounts fresh on every open and unmounts on close — no
            reset effects needed. */}
        <BulkAddToGroupForm
          selectedCount={selectedCount}
          onCancel={onClose}
          onSubmit={onSubmit}
          isPending={isPending}
        />
      </DialogContent>
    </Dialog>
  )
}

function BulkAddToGroupForm({
  selectedCount,
  onCancel,
  onSubmit,
  isPending,
}: {
  selectedCount: number
  onCancel: () => void
  onSubmit: (payload: BulkAddToGroupSubmission) => void
  isPending: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'

  // Track group creation state and fields
  const [isCreatingGroup, setIsCreatingGroup] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')
  const [newGroupKind, setNewGroupKind] = useState<GroupKind>('social')
  const [newGroupCurrency, setNewGroupCurrency] = useState(userCurrency)
  const [newGroupNotes, setNewGroupNotes] = useState('')

  // Track member creation state and fields
  const [isAddingMember, setIsAddingMember] = useState(false)
  const [newMemberName, setNewMemberName] = useState('')
  const [newMemberEmail, setNewMemberEmail] = useState('')
  const [newMemberLinkedUserId, setNewMemberLinkedUserId] = useState<string | null>(null)

  // Track only the user's explicit group choice — the effective `groupId`
  // is derived below so we don't need an effect to "auto-pick" the first
  // group when data arrives.
  const [explicitGroupId, setExplicitGroupId] = useState<string | null>(null)
  const [shareType, setShareType] = useState<BulkShareType>('equal')
  // Per-member selection state, keyed by member id. Members not present in
  // the map use defaults (selected, empty percent) so we don't need an
  // effect to seed the rows when the group query resolves.
  const [selectionByMember, setSelectionByMember] = useState<Record<string, MemberSelection>>({})

  const { data: groups } = useQuery({
    queryKey: ['groups'],
    queryFn: () => groupsApi.list(false),
  })

  const ownedGroups = useMemo(
    () => (groups ?? []).filter((g) => g.is_owner && !g.is_archived),
    [groups],
  )

  const createGroupMutation = useMutation({
    mutationFn: (payload: GroupCreatePayload) => groupsApi.create(payload),
    onSuccess: (newGroup) => {
      queryClient.invalidateQueries({ queryKey: ['groups'] })
      setExplicitGroupId(newGroup.id)
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

  const showCreateGroupForm = isCreatingGroup
  const showCreateMemberForm = isAddingMember
  const groupId = explicitGroupId ?? ownedGroups[0]?.id ?? ''

  const { data: group } = useQuery({
    queryKey: ['groups', groupId],
    queryFn: () => groupsApi.get(groupId),
    enabled: !!groupId && !showCreateGroupForm && !showCreateMemberForm,
  })

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

  const rows = useMemo(
    () =>
      (group?.members ?? []).map((m) => ({
        member_id: m.id,
        selected: selectionByMember[m.id]?.selected ?? true,
        percent: selectionByMember[m.id]?.percent ?? '',
      })),
    [group, selectionByMember],
  )

  const updateRow = (memberId: string, patch: Partial<MemberSelection>) => {
    setSelectionByMember((prev) => ({
      ...prev,
      [memberId]: {
        selected: prev[memberId]?.selected ?? true,
        percent: prev[memberId]?.percent ?? '',
        ...patch,
      },
    }))
  }

  const selectedRows = rows.filter((r) => r.selected)

  const percentSum = useMemo(() => {
    if (shareType !== 'percent') return null
    return selectedRows.reduce((s, r) => s + (parseFloat(r.percent) || 0), 0)
  }, [shareType, selectedRows])

  const isValid = useMemo(() => {
    if (!groupId) return false
    if (selectedRows.length === 0) return false
    if (shareType === 'equal') return true
    return Math.abs((percentSum ?? 0) - 100) < 0.005
  }, [groupId, selectedRows, shareType, percentSum])

  const handleSubmit = () => {
    if (!isValid) return
    onSubmit({
      groupId,
      share_type: shareType,
      member_splits: selectedRows.map((r) => {
        if (shareType === 'percent') {
          return {
            group_member_id: r.member_id,
            share_pct: parseFloat(r.percent) || 0,
          }
        }
        return { group_member_id: r.member_id }
      }),
    })
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {showCreateGroupForm
            ? t('splitGroups.add')
            : showCreateMemberForm
              ? t('splitGroups.addMember')
              : t('transactions.bulkAddToGroupTitle', { count: selectedCount })}
        </DialogTitle>
      </DialogHeader>

      {showCreateGroupForm ? (
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
      ) : showCreateMemberForm ? (
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
      ) : ownedGroups.length === 0 ? (
        <div className="space-y-2 py-4">
          <p className="text-sm text-muted-foreground font-semibold">
            {t('splitGroups.splitNoGroups')}
          </p>
          <p className="text-sm text-muted-foreground">
            {t('splitGroups.splitNoGroupsLinkPrefix')}
            <button
              type="button"
              onClick={() => {
                setIsCreatingGroup(true)
                setNewGroupName('')
                setNewGroupCurrency(userCurrency)
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
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <Label className="text-xs">{t('splitGroups.group')}</Label>
                <button
                  type="button"
                  onClick={() => {
                    setIsCreatingGroup(true)
                    setNewGroupName('')
                    setNewGroupCurrency(userCurrency)
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
                onChange={(e) => setExplicitGroupId(e.target.value)}
              >
                {ownedGroups.map((g) => (
                  <option key={g.id} value={g.id}>{g.name}</option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">{t('splitGroups.shareType')}</Label>
              <select
                className="w-full border border-border rounded-md px-2 py-1.5 text-sm bg-background"
                value={shareType}
                onChange={(e) => setShareType(e.target.value as BulkShareType)}
              >
                <option value="equal">{t('splitGroups.shareEqual')}</option>
                <option value="percent">{t('splitGroups.sharePercent')}</option>
              </select>
            </div>
          </div>

          <p className="text-xs text-muted-foreground">
            {t('transactions.bulkAddToGroupExactHint')}
          </p>

          <div className="flex items-center justify-between border-t border-border pt-3">
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

          {group && (
            <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
              {group.members.length === 0 ? (
                <div className="py-4 text-center">
                  <p className="text-sm text-muted-foreground mb-3">
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
                group.members.map((m) => {
                  const row = rows.find((r) => r.member_id === m.id)
                  if (!row) return null
                  return (
                    <div key={m.id} className="flex items-center gap-2">
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
              )}
            </div>
          )}

          {shareType === 'percent' && percentSum !== null && selectedRows.length > 0 && (
            <div className="text-xs">
              <span
                className={
                  Math.abs(percentSum - 100) < 0.005
                    ? 'text-emerald-600'
                    : 'text-amber-600'
                }
              >
                {t('splitGroups.percentSum', { total: percentSum.toFixed(2) })}
              </span>
            </div>
          )}

          {shareType === 'equal' && selectedRows.length > 0 && (
            <p className="text-xs text-muted-foreground">
              {t('splitGroups.equalHint')}
            </p>
          )}
        </div>
      )}

      <DialogFooter>
        {showCreateGroupForm ? (
          <>
            <Button variant="ghost" onClick={() => setIsCreatingGroup(false)} disabled={createGroupMutation.isPending}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button
              onClick={handleCreateGroup}
              disabled={!newGroupName.trim() || createGroupMutation.isPending}
            >
              {createGroupMutation.isPending ? t('common.saving') : t('splitGroups.add')}
            </Button>
          </>
        ) : showCreateMemberForm ? (
          <>
            <Button variant="ghost" onClick={() => setIsAddingMember(false)} disabled={createMemberMutation.isPending}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button
              onClick={handleCreateMember}
              disabled={!newMemberName.trim() || createMemberMutation.isPending}
            >
              {createMemberMutation.isPending ? t('common.saving') : t('common.save')}
            </Button>
          </>
        ) : (
          <>
            <Button variant="ghost" onClick={onCancel} disabled={isPending}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button
              onClick={handleSubmit}
              disabled={!isValid || isPending || ownedGroups.length === 0}
            >
              {t('transactions.bulkAddToGroupSubmit')}
            </Button>
          </>
        )}
      </DialogFooter>
    </>
  )
}
