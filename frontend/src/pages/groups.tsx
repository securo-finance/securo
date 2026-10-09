import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { groups as groupsApi, type GroupCreatePayload } from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { Button } from '@/components/ui/button'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { GroupForm } from '@/components/group-form'
import { PageHeader } from '@/components/page-header'
import { Archive, ChevronRight, Plus, Search, Trash2, Users, X } from 'lucide-react'
import type { Group, GroupKind } from '@/types'

type StatusFilter = 'active' | 'archived' | 'all'


export default function GroupsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const statusParam = searchParams.get('status')
  const statusFilter: StatusFilter = statusParam === 'archived' || statusParam === 'all' ? statusParam : 'active'
  const search = searchParams.get('q') ?? ''
  const [searchQuery, setSearchQuery] = useState(search.trim())
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Group | null>(null)
  const [deletingGroup, setDeletingGroup] = useState<Group | null>(null)

  useEffect(() => {
    const timer = setTimeout(() => setSearchQuery(search.trim()), 300)
    return () => clearTimeout(timer)
  }, [search])

  const updateFilter = (key: 'q' | 'status', value: string) => {
    if (key === 'status') setSearchQuery(search.trim())
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous)
      if (value && !(key === 'status' && value === 'active')) next.set(key, value)
      else next.delete(key)
      return next
    }, { replace: key === 'q' })
  }

  const [name, setName] = useState('')
  const [kind, setKind] = useState<GroupKind>('social')
  const [defaultCurrency, setDefaultCurrency] = useState(userCurrency)
  const [notes, setNotes] = useState('')


  const { data: list = [], isLoading } = useQuery({
    queryKey: ['groups', { q: searchQuery, status: statusFilter }],
    queryFn: () => groupsApi.list(statusFilter !== 'active', { q: searchQuery || undefined, status: statusFilter }),
  })

  const createMutation = useMutation({
    mutationFn: (payload: GroupCreatePayload) => groupsApi.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups'] })
      setDialogOpen(false)
      toast.success(t('splitGroups.created'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Partial<GroupCreatePayload> & { is_archived?: boolean } }) =>
      groupsApi.update(id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups'] })
      setDialogOpen(false)
      setEditing(null)
      toast.success(t('splitGroups.updated'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => groupsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups'] })
      setDialogOpen(false)
      setEditing(null)
      setDeletingGroup(null)
      toast.success(t('splitGroups.deleted'))
    },
    onError: (err: unknown) => {
      const detail =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
          : undefined
      toast.error(detail ?? t('common.error'))
    },
  })

  const openCreate = () => {
    setEditing(null)
    setName('')
    setKind('social')
    setDefaultCurrency(userCurrency)
    setNotes('')
    setDialogOpen(true)
  }

  const openEdit = (group: Group) => {
    setEditing(group)
    setName(group.name)
    setKind(group.kind)
    setDefaultCurrency(group.default_currency)
    setNotes(group.notes ?? '')
    setDialogOpen(true)
  }

  // Dismissing the confirmation (cancel, Esc, X, overlay) puts the user back in
  // the edit dialog they opened it from, instead of dropping them on the list.
  const returnToEditDialog = () => {
    setDeletingGroup(null)
    setDialogOpen(true)
  }

  const handleSave = () => {
    const payload: GroupCreatePayload = {
      name: name.trim(),
      kind,
      default_currency: defaultCurrency,
      notes: notes.trim() || null,
    }
    if (editing) {
      updateMutation.mutate({ id: editing.id, payload })
    } else {
      createMutation.mutate(payload)
    }
  }

  return (
    <div>
      <PageHeader
        section={t('splitGroups.section')}
        title={t('splitGroups.title')}
        action={
          canWrite ? (
            <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
              <Button size="sm" className="h-8 gap-1.5" onClick={openCreate}>
                <Plus size={13} />
                <span>{t('splitGroups.add')}</span>
              </Button>
            </div>
          ) : undefined
        }
      />

      {/* Status filter — pill buttons matching goals.tsx */}
      <div className="flex items-center gap-2 mb-4">
        {(['active', 'archived', 'all'] as StatusFilter[]).map((s) => (
          <button
            key={s}
            onClick={() => updateFilter('status', s)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              statusFilter === s
                ? 'bg-primary text-primary-foreground'
                : 'bg-muted text-muted-foreground hover:text-foreground'
            }`}
          >
            {t(`splitGroups.filter.${s}`)}
          </button>
        ))}
      </div>

      <div className="rounded-xl border border-border bg-card shadow-sm transition-colors mb-4 focus-within:border-primary/40 focus-within:ring-[3px] focus-within:ring-primary/10">
        <div className="flex items-center gap-1.5 px-2 py-1.5">
          <div className="relative flex min-w-0 flex-1 items-center gap-1 px-2.5 py-1 min-h-9">
            <Search size={15} className="pointer-events-none shrink-0 text-muted-foreground/70" />
            <input
              type="search"
              aria-label={t('splitGroups.searchPlaceholder')}
              placeholder={t('splitGroups.searchPlaceholder')}
              value={search}
              onChange={(e) => updateFilter('q', e.target.value)}
              className="min-w-0 flex-1 bg-transparent px-1.5 text-[13.5px] outline-none placeholder:text-muted-foreground/75"
            />
          </div>
          {search && (
            <Button variant="ghost" size="icon-sm" aria-label={t('splitGroups.clearSearch')} onClick={() => updateFilter('q', '')}>
              <X size={15} />
            </Button>
          )}
        </div>
      </div>

      <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden mb-4">
        {isLoading ? (
          <div className="p-6 space-y-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        ) : list.length === 0 ? (
          <div className="text-center py-16 text-muted-foreground">
            <Users size={32} className="mx-auto mb-2 opacity-50" />
            <p>{t(searchQuery ? 'common.noResults' : 'splitGroups.empty')}</p>
            {!searchQuery && (
              <p className="text-xs mt-1">{t('splitGroups.emptyHint')}</p>
            )}
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {list.map((group) => (
              <li
                key={group.id}
                className="flex items-center gap-3 px-4 py-3.5 hover:bg-muted cursor-pointer transition-colors"
                onClick={() => navigate(`/groups/${group.id}`)}
              >
                {/* Avatar circle — colored using the group's `color` field */}
                <div
                  className="h-10 w-10 rounded-full flex items-center justify-center shrink-0"
                  style={{ backgroundColor: `${group.color}22`, color: group.color }}
                  aria-hidden
                >
                  <Users size={18} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-semibold text-foreground truncate">{group.name}</span>
                    <span className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded-full">
                      {t(`splitGroups.kind.${group.kind}`)}
                    </span>
                    {group.is_archived && (
                      <span className="text-xs bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200 px-2 py-0.5 rounded-full inline-flex items-center gap-1">
                        <Archive size={10} />
                        {t('splitGroups.archived')}
                      </span>
                    )}
                    {!group.is_owner && (
                      <span className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded-full">
                        {t('splitGroups.sharedWithYou')}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {t('splitGroups.memberCount', { count: group.members.length })} · {group.default_currency}
                  </p>
                </div>
                {group.is_owner && canWrite && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={(e) => {
                      e.stopPropagation()
                      openEdit(group)
                    }}
                  >
                    {t('common.edit')}
                  </Button>
                )}
                <ChevronRight size={16} className="text-muted-foreground shrink-0" />
              </li>
            ))}
          </ul>
        )}
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{editing ? t('splitGroups.edit') : t('splitGroups.add')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <GroupForm
              name={name}
              onChangeName={setName}
              kind={kind}
              onChangeKind={setKind}
              defaultCurrency={defaultCurrency}
              onChangeDefaultCurrency={setDefaultCurrency}
              notes={notes}
              onChangeNotes={setNotes}
            />
            {editing && (
              <label className="text-sm text-muted-foreground inline-flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={editing.is_archived}
                  onChange={(e) =>
                    updateMutation.mutate({
                      id: editing.id,
                      payload: { is_archived: e.target.checked },
                    })
                  }
                  className="h-4 w-4 rounded border-border accent-primary"
                />
                {t('splitGroups.archived')}
              </label>
            )}
          </div>
          <DialogFooter className={editing ? 'flex justify-between sm:justify-between' : ''}>
            {editing && (
              <Button
                variant="destructive"
                onClick={() => {
                  setDialogOpen(false)
                  setDeletingGroup(editing)
                }}
                disabled={deleteMutation.isPending}
              >
                <Trash2 size={14} className="mr-1" />
                {t('common.delete')}
              </Button>
            )}
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>
                {t('common.cancel')}
              </Button>
              <Button
                onClick={handleSave}
                disabled={!name.trim() || createMutation.isPending || updateMutation.isPending}
              >
                {t('common.save')}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DeleteConfirmationDialog
        open={!!deletingGroup}
        title={t('splitGroups.confirmDeleteTitle')}
        description={t('splitGroups.confirmDeleteDescription', { name: deletingGroup?.name })}
        isPending={deleteMutation.isPending}
        onClose={returnToEditDialog}
        onConfirm={() => deletingGroup && deleteMutation.mutate(deletingGroup.id)}
      />
    </div>
  )
}
