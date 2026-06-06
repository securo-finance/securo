import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useDateLocale } from '@/hooks/use-display-locale'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { auth as authApi, currencies as currenciesApi, workspaces as workspacesApi } from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { IconPicker } from '@/components/icon-picker'
import { CategoryIcon } from '@/components/category-icon'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { AlertTriangle, Archive, Plus, Save, Trash2, Users } from 'lucide-react'
import type { WorkspaceMember, WorkspaceRole } from '@/types'

function labelForRole(role: WorkspaceRole, t: (key: string) => string): string {
  return {
    owner: t('workspace.roleOwner'),
    editor: t('workspace.roleEditor'),
    viewer: t('workspace.roleViewer'),
    manager: t('workspace.roleManager'),
  }[role]
}

function hintForRole(role: WorkspaceRole, t: (key: string) => string): string {
  return {
    owner: t('workspace.roleOwnerHint'),
    editor: t('workspace.roleEditorHint'),
    viewer: t('workspace.roleViewerHint'),
    manager: t('workspace.roleManagerHint'),
  }[role]
}

function formatDate(iso: string, locale: string): string {
  try {
    return new Date(iso).toLocaleDateString(locale, {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    })
  } catch {
    return iso.slice(0, 10)
  }
}

const DEFAULT_WORKSPACE_COLOR = '#6366F1'
const DEFAULT_WORKSPACE_ICON = 'briefcase'

export default function WorkspaceSettingsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const localeForFormat = useDateLocale()
  const { current, canManage, workspaces: allWorkspaces, refresh, switchWorkspace } = useWorkspace()
  const { user: currentUser, updateUser } = useAuth()
  const queryClient = useQueryClient()

  const [editName, setEditName] = useState('')
  const [editCurrency, setEditCurrency] = useState('')
  const [editLocale, setEditLocale] = useState('')
  const [editIcon, setEditIcon] = useState(DEFAULT_WORKSPACE_ICON)
  const [editColor, setEditColor] = useState(DEFAULT_WORKSPACE_COLOR)
  const [inviteOpen, setInviteOpen] = useState(false)
  const [inviteEmail, setInviteEmail] = useState('')
  const [invitePassword, setInvitePassword] = useState('')
  const [inviteRole, setInviteRole] = useState<WorkspaceRole>('editor')
  const [removeTarget, setRemoveTarget] = useState<WorkspaceMember | null>(null)
  const [archiveOpen, setArchiveOpen] = useState(false)

  useEffect(() => {
    if (!current) return
    setEditName(current.name)
    setEditCurrency(current.default_currency)
    setEditLocale(current.locale ?? '')
    setEditIcon(current.icon ?? DEFAULT_WORKSPACE_ICON)
    setEditColor(current.color ?? DEFAULT_WORKSPACE_COLOR)
  }, [current?.id, current?.name, current?.default_currency, current?.locale, current?.icon, current?.color])

  const membersQuery = useQuery({
    queryKey: ['workspace-members', current?.id],
    queryFn: () => (current ? workspacesApi.listMembers(current.id) : Promise.resolve([])),
    enabled: !!current,
  })

  const { data: supportedCurrencies } = useQuery({
    queryKey: ['currencies'],
    queryFn: currenciesApi.list,
    staleTime: Infinity,
  })

  const statsQuery = useQuery({
    queryKey: ['workspace-stats', current?.id],
    queryFn: () => (current ? workspacesApi.stats(current.id) : Promise.resolve({ members: 0, accounts: 0, transactions: 0 })),
    enabled: !!current,
  })

  const updateMutation = useMutation({
    mutationFn: () => {
      if (!current) throw new Error('No workspace')
      return workspacesApi.update(current.id, {
        name: editName,
        default_currency: editCurrency,
        locale: editLocale || (null as unknown as string),
        icon: editIcon,
        color: editColor,
      })
    },
    onSuccess: () => {
      toast.success(t('workspace.saveSuccess'))
      void refresh()
      // Changing the workspace currency also updates the acting user's
      // display currency server-side; refresh the cached user so the
      // whole app re-renders in the new currency, then drop currency-
      // dependent queries.
      void authApi.me().then(updateUser).catch(() => {})
      void queryClient.invalidateQueries()
    },
    onError: (e: unknown) => {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e instanceof Error ? e.message : t('workspace.saveError'))
      toast.error(detail)
    },
  })

  const archiveMutation = useMutation({
    mutationFn: () => {
      if (!current) throw new Error('No workspace')
      return workspacesApi.archive(current.id)
    },
    onSuccess: async () => {
      toast.success(t('workspace.archiveSuccess', 'Workspace arquivado'))
      setArchiveOpen(false)
      await refresh()
      // Switch into another accessible workspace, then redirect home.
      const remaining = allWorkspaces.filter((w) => w.id !== current?.id)
      if (remaining.length > 0) {
        await switchWorkspace(remaining[0].id)
      }
      navigate('/')
    },
    onError: (e: unknown) => {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e instanceof Error ? e.message : 'Failed')
      toast.error(detail)
    },
  })

  const inviteMutation = useMutation({
    mutationFn: () => {
      if (!current) throw new Error('No workspace')
      return workspacesApi.invite(current.id, {
        email: inviteEmail.trim(),
        role: inviteRole,
        password: invitePassword || undefined,
      })
    },
    onSuccess: () => {
      toast.success(t('workspace.addSuccess'))
      setInviteOpen(false)
      setInviteEmail('')
      setInvitePassword('')
      setInviteRole('editor')
      queryClient.invalidateQueries({ queryKey: ['workspace-members', current?.id] })
      queryClient.invalidateQueries({ queryKey: ['workspace-stats', current?.id] })
    },
    onError: (e: unknown) => {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e instanceof Error ? e.message : 'Failed')
      toast.error(detail)
    },
  })

  const removeMutation = useMutation({
    mutationFn: (member: WorkspaceMember) => {
      if (!current) throw new Error('No workspace')
      return workspacesApi.removeMember(current.id, member.user_id)
    },
    onSuccess: () => {
      toast.success(t('workspace.removeSuccess'))
      setRemoveTarget(null)
      queryClient.invalidateQueries({ queryKey: ['workspace-members', current?.id] })
      queryClient.invalidateQueries({ queryKey: ['workspace-stats', current?.id] })
    },
    onError: (e: unknown) => {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e instanceof Error ? e.message : 'Failed')
      toast.error(detail)
    },
  })

  const roleChangeMutation = useMutation({
    mutationFn: ({ member, role }: { member: WorkspaceMember; role: WorkspaceRole }) => {
      if (!current) throw new Error('No workspace')
      return workspacesApi.changeRole(current.id, member.user_id, role)
    },
    onSuccess: () => {
      toast.success(t('workspace.roleUpdated'))
      queryClient.invalidateQueries({ queryKey: ['workspace-members', current?.id] })
    },
    onError: (e: unknown) => {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e instanceof Error ? e.message : 'Failed')
      toast.error(detail)
    },
  })

  if (!current) {
    return (
      <div className="container max-w-5xl py-8 space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  const members = membersQuery.data ?? []
  const stats = statsQuery.data ?? { members: 1, accounts: 0, transactions: 0 }
  const isManaged = !!current.managed_by_user_id
  const isManagerSelf = isManaged && current.managed_by_user_id === currentUser?.id

  return (
    <div className="container max-w-5xl py-8 space-y-6">
      {/* Header card — identity + role + stats strip */}
      <section className="rounded-xl border bg-card overflow-hidden">
        <div className="p-6 flex items-center gap-5 border-b">
          <CategoryIcon
            icon={current.icon ?? DEFAULT_WORKSPACE_ICON}
            color={current.color ?? DEFAULT_WORKSPACE_COLOR}
            size="lg"
            className="shrink-0"
          />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-xl font-semibold truncate">{current.name}</h1>
              {current.role && (
                <Badge variant="secondary" className="text-[11px]">
                  {labelForRole(current.role, t)}
                </Badge>
              )}
              {isManaged && (
                <Badge variant="outline" className="text-[11px]">
                  {isManagerSelf
                    ? t('workspace.youManageThis')
                    : t('workspace.externallyManaged')}
                </Badge>
              )}
            </div>
            <p className="text-sm text-muted-foreground mt-1">
              {t('workspace.settingsDescription')}
            </p>
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 divide-x divide-border">
          <StatTile label={t('workspace.members')} value={String(stats.members)} />
          <StatTile label={t('workspace.statAccounts', 'Contas')} value={String(stats.accounts)} />
          <StatTile label={t('workspace.statTransactions', 'Transações')} value={String(stats.transactions)} />
          <StatTile label={t('workspace.statCreatedAt', 'Criado em')} value={formatDate(current.created_at, localeForFormat)} />
        </div>
      </section>

      {/* Details card — 4-column form */}
      <section className="space-y-4 rounded-xl border bg-card p-6">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">{t('workspace.details')}</h2>
          {canManage && (
            <Button
              onClick={() => updateMutation.mutate()}
              disabled={updateMutation.isPending}
              className="rounded-lg"
              size="sm"
            >
              <Save className="mr-2 h-4 w-4" />
              {updateMutation.isPending ? t('common.loading') : t('common.save')}
            </Button>
          )}
        </div>
        <div className="space-y-4">
          {/* Identity row — icon button + color swatch align with the
              input (not the label) so the label sits over the name
              field only. */}
          <div className="flex items-end gap-2">
            {canManage ? (
              <>
                <div className="space-y-1.5">
                  <Label className="text-[13px]">
                    {t('workspace.icon', 'Ícone')}
                  </Label>
                  <Popover>
                    <PopoverTrigger asChild>
                      <button
                        type="button"
                        className="h-10 w-10 rounded-lg border border-input flex items-center justify-center hover:bg-muted/40 transition-colors shrink-0"
                        title={t('workspace.icon', 'Ícone')}
                      >
                        <CategoryIcon icon={editIcon} color={editColor} size="sm" />
                      </button>
                    </PopoverTrigger>
                    <PopoverContent className="w-80 p-3" align="start">
                      <IconPicker value={editIcon} color={editColor} onChange={setEditIcon} />
                    </PopoverContent>
                  </Popover>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="ws-color" className="text-[13px]">
                    {t('groups.color', 'Cor')}
                  </Label>
                  <input
                    id="ws-color"
                    type="color"
                    value={editColor}
                    onChange={(e) => setEditColor(e.target.value)}
                    className="h-10 w-10 p-1 rounded-lg cursor-pointer border border-input bg-background shrink-0"
                    title={t('groups.color', 'Cor')}
                  />
                </div>
              </>
            ) : (
              <div className="h-10 w-10 rounded-lg flex items-center justify-center shrink-0">
                <CategoryIcon icon={editIcon} color={editColor} size="sm" />
              </div>
            )}
            <div className="space-y-1.5 flex-1">
              <Label htmlFor="ws-name" className="text-[13px]">
                {t('workspace.name')}
              </Label>
              <Input
                id="ws-name"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                disabled={!canManage}
                maxLength={100}
                className="h-10 rounded-lg w-full"
              />
            </div>
          </div>

          {/* Region row — currency + locale */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label htmlFor="ws-currency" className="text-[13px]">
                {t('workspace.defaultCurrency')}
              </Label>
              <Select
                value={editCurrency}
                onValueChange={setEditCurrency}
                disabled={!canManage}
              >
                <SelectTrigger id="ws-currency" className="h-10 rounded-lg w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(supportedCurrencies ?? [{ code: editCurrency, symbol: editCurrency, name: editCurrency, flag: '' }]).map((c) => (
                    <SelectItem key={c.code} value={c.code}>
                      <span className="mr-2">{c.flag}</span>
                      {c.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ws-locale" className="text-[13px]">
                {t('workspace.locale')}
              </Label>
              <Select
                value={editLocale || '__none__'}
                onValueChange={(v) => setEditLocale(v === '__none__' ? '' : v)}
                disabled={!canManage}
              >
                <SelectTrigger id="ws-locale" className="h-10 rounded-lg w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">—</SelectItem>
                  <SelectItem value="en">English</SelectItem>
                  <SelectItem value="pt-BR">Português (BR)</SelectItem>
                  <SelectItem value="es">Español</SelectItem>
                  <SelectItem value="pl">Polski</SelectItem>
                  <SelectItem value="it">Italiano</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>
      </section>

      {/* Members card */}
      <section className="space-y-4 rounded-xl border bg-card p-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Users className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-base font-semibold">{t('workspace.members')}</h2>
            <Badge variant="outline" className="text-[11px]">
              {members.length}
            </Badge>
          </div>
          {canManage && (
            <Button onClick={() => setInviteOpen(true)} size="sm" className="rounded-lg">
              <Plus className="mr-2 h-4 w-4" />
              {t('workspace.addMember')}
            </Button>
          )}
        </div>

        {membersQuery.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : members.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t('workspace.noMembers')} {canManage && t('workspace.noMembersHint')}
          </p>
        ) : (
          <ul className="divide-y rounded-lg border">
            {members.map((m) => {
              const isMe = m.user_id === currentUser?.id
              return (
                <li
                  key={m.id}
                  className="py-3 px-4 flex items-center gap-3 hover:bg-muted/30 transition-colors"
                >
                  <Avatar className="h-9 w-9">
                    <AvatarFallback className="bg-primary/15 text-primary text-xs font-semibold">
                      {(m.display_name || m.email).slice(0, 2).toUpperCase()}
                    </AvatarFallback>
                  </Avatar>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">
                      {m.display_name || m.email}
                      {isMe && (
                        <span className="ml-2 text-xs text-muted-foreground">
                          ({t('workspace.you')})
                        </span>
                      )}
                    </p>
                    {m.display_name && (
                      <p className="text-xs text-muted-foreground truncate">{m.email}</p>
                    )}
                  </div>
                  {canManage && !isMe ? (
                    <select
                      value={m.role}
                      onChange={(e) =>
                        roleChangeMutation.mutate({
                          member: m,
                          role: e.target.value as WorkspaceRole,
                        })
                      }
                      className="h-9 w-32 rounded-lg border border-input bg-background px-2 text-sm"
                    >
                      {(['owner', 'editor', 'viewer'] as WorkspaceRole[]).map((r) => (
                        <option key={r} value={r}>
                          {labelForRole(r, t)}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <Badge variant="secondary" className="text-[11px]">
                      {labelForRole(m.role, t)}
                    </Badge>
                  )}
                  {canManage && !isMe && (
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => setRemoveTarget(m)}
                      title={t('workspace.remove')}
                      className="rounded-lg"
                    >
                      <Trash2 className="h-4 w-4 text-destructive" />
                    </Button>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </section>

      {/* Danger zone — owners only */}
      {canManage && (
        <section className="space-y-4 rounded-xl border bg-card p-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-muted-foreground" />
              <h2 className="text-base font-semibold">
                {t('workspace.dangerZone', 'Zona de perigo')}
              </h2>
            </div>
          </div>
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-medium">
                {t('workspace.archiveAction', 'Arquivar workspace')}
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {t(
                  'workspace.archiveHint',
                  'O workspace fica oculto da lista e do switcher. Os dados continuam preservados.',
                )}
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="rounded-lg text-destructive hover:bg-destructive/10 hover:text-destructive"
              onClick={() => setArchiveOpen(true)}
            >
              <Archive className="mr-2 h-4 w-4" />
              {t('workspace.archive', 'Arquivar')}
            </Button>
          </div>
        </section>
      )}

      {/* Invite dialog */}
      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('workspace.addMemberTitle')}</DialogTitle>
            <DialogDescription>{t('workspace.addMemberDescription')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-1">
            <div className="space-y-1.5">
              <Label htmlFor="invite-email" className="text-[13px]">
                {t('admin.users.email', 'Email')}
              </Label>
              <Input
                id="invite-email"
                type="email"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                autoFocus
                className="h-10 rounded-lg"
                placeholder="user@example.com"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="invite-role" className="text-[13px]">
                {t('workspace.role')}
              </Label>
              <select
                id="invite-role"
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value as WorkspaceRole)}
                className="w-full h-10 rounded-lg border border-input bg-background px-3 text-sm"
              >
                {(['owner', 'editor', 'viewer'] as WorkspaceRole[]).map((r) => (
                  <option key={r} value={r}>
                    {labelForRole(r, t)} — {hintForRole(r, t)}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="invite-password" className="text-[13px]">
                {t('workspace.passwordForNewUsers')}
              </Label>
              <Input
                id="invite-password"
                type="password"
                value={invitePassword}
                onChange={(e) => setInvitePassword(e.target.value)}
                className="h-10 rounded-lg"
                placeholder=""
              />
              <p className="text-[11px] text-muted-foreground leading-relaxed">
                {t('workspace.passwordHint')}
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setInviteOpen(false)}
              className="rounded-lg"
            >
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => inviteMutation.mutate()}
              disabled={inviteMutation.isPending || !inviteEmail.trim()}
              className="rounded-lg"
            >
              {inviteMutation.isPending ? t('common.loading') : t('common.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Remove member dialog */}
      <Dialog
        open={!!removeTarget}
        onOpenChange={(open) => !open && setRemoveTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('workspace.removeConfirmTitle')}</DialogTitle>
            <DialogDescription>
              {t('workspace.removeConfirmDescription', { email: removeTarget?.email })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRemoveTarget(null)}
              className="rounded-lg"
            >
              {t('common.cancel')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => removeTarget && removeMutation.mutate(removeTarget)}
              disabled={removeMutation.isPending}
              className="rounded-lg"
            >
              {removeMutation.isPending ? t('common.loading') : t('workspace.remove')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Archive workspace dialog */}
      <Dialog open={archiveOpen} onOpenChange={setArchiveOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t('workspace.archiveConfirmTitle', 'Arquivar workspace?')}
            </DialogTitle>
            <DialogDescription>
              {t(
                'workspace.archiveConfirmDescription',
                'O workspace "{{name}}" será removido da sua lista. Os dados ficam preservados e um admin pode restaurar mais tarde.',
                { name: current.name },
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setArchiveOpen(false)}
              className="rounded-lg"
            >
              {t('common.cancel')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => archiveMutation.mutate()}
              disabled={archiveMutation.isPending}
              className="rounded-lg"
            >
              <Archive className="mr-2 h-4 w-4" />
              {archiveMutation.isPending ? t('common.loading') : t('workspace.archive', 'Arquivar')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="px-6 py-4">
      <p className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold mt-1">{value}</p>
    </div>
  )
}
