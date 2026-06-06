import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'
import { toast } from 'sonner'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { workspaces as workspacesApi } from '@/lib/api'
import { resolveSupportedLang } from '@/lib/i18n'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuPortal,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Check,
  ChevronsUpDown,
  Download,
  HardDriveDownload,
  KeyRound,
  Languages,
  LogOut,
  Plus,
  Settings,
  Shield,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { CategoryIcon } from '@/components/category-icon'
import type { Workspace } from '@/types'

const ROLE_LABEL_KEY: Record<string, string> = {
  owner: 'workspace.roleOwner',
  editor: 'workspace.roleEditor',
  viewer: 'workspace.roleViewer',
  manager: 'workspace.roleManager',
}

// Fallbacks when a workspace hasn't set its own icon/color yet.
const DEFAULT_ICON_BY_KIND: Record<string, string> = {
  personal: 'user',
  freelancer: 'briefcase',
  small_business: 'building-2',
  accountant_firm: 'landmark',
}
const DEFAULT_COLOR = '#6366F1'

function workspaceIcon(w: Workspace): string {
  return w.icon || DEFAULT_ICON_BY_KIND[w.kind] || 'briefcase'
}
function workspaceColor(w: Workspace): string {
  return w.color || DEFAULT_COLOR
}

interface AccountMenuProps {
  /** Backup download in progress — disables the menu item. */
  backingUp: boolean
  /** Open the change-password dialog. */
  onChangePassword: () => void
  /** Open the 2FA setup dialog. */
  onTwoFactor: () => void
  /** Trigger a backup download. */
  onBackup: () => void
  /** Open the "Update available" dialog. */
  onUpdateAvailable: () => void
  /** True when the AGENTS_ENABLED env flag is on. */
  agentsEnabled: boolean
}

/**
 * Unified account menu: workspace identity on the trigger, all
 * workspace + account actions in one dropdown. Replaces the previous
 * standalone workspace switcher + separate user dropdown.
 *
 * Dialogs (change password, 2FA, update available) stay owned by the
 * parent layout — they're shared with other surfaces and the menu
 * only needs to trigger them.
 */
export function WorkspaceSwitcher({
  backingUp,
  onChangePassword,
  onTwoFactor,
  onBackup,
  onUpdateAvailable,
  agentsEnabled,
}: AccountMenuProps) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { current, workspaces, switchWorkspace, refresh } = useWorkspace()
  const { user, logout } = useAuth()
  const [createOpen, setCreateOpen] = useState(false)
  const [newName, setNewName] = useState('')

  const currentLang = resolveSupportedLang(i18n.resolvedLanguage ?? i18n.language)

  const createMutation = useMutation({
    mutationFn: () =>
      workspacesApi.create({
        name: newName.trim(),
        self_membership: true,
      }),
    onSuccess: async (ws) => {
      toast.success(t('workspace.createSuccess', 'Workspace created'))
      await refresh()
      await switchWorkspace(ws.id)
      setCreateOpen(false)
      setNewName('')
      navigate('/workspace/settings')
    },
    onError: (e: unknown) => {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e instanceof Error ? e.message : 'Failed to create workspace')
      toast.error(detail)
    },
  })

  if (!current || !user) return null

  const hasMultipleWorkspaces = workspaces.length > 1
  const roleLabel = current.role && ROLE_LABEL_KEY[current.role]
    ? t(ROLE_LABEL_KEY[current.role])
    : null

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button className="flex items-center gap-3 w-full rounded-lg px-3 py-2.5 text-sm hover:bg-sidebar-accent transition-colors text-left">
            <CategoryIcon
              icon={workspaceIcon(current)}
              color={workspaceColor(current)}
              size="sm"
              className="shrink-0"
            />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-semibold truncate">{current.name}</p>
              <p className="text-[10px] text-sidebar-muted/70 truncate">
                {user.email}
                {roleLabel && (
                  <span className="ml-1 uppercase tracking-wide">· {roleLabel}</span>
                )}
              </p>
            </div>
            <ChevronsUpDown size={13} className="text-sidebar-muted/60 shrink-0" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-64" side="top">
          {/* Workspaces — only show the switcher list when there's more than one. */}
          {hasMultipleWorkspaces && (
            <>
              <DropdownMenuLabel className="px-2 py-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/70">
                {t('workspace.switcherTitle', 'Switch workspace')}
              </DropdownMenuLabel>
              {workspaces.map((w) => {
                const isActive = w.id === current.id
                return (
                  <DropdownMenuItem
                    key={w.id}
                    onClick={() => void switchWorkspace(w.id)}
                    className="flex items-center gap-2"
                  >
                    <CategoryIcon
                      icon={workspaceIcon(w)}
                      color={workspaceColor(w)}
                      size="sm"
                      className="shrink-0"
                    />
                    <span className="flex-1 truncate">{w.name}</span>
                    <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                      {w.role && ROLE_LABEL_KEY[w.role] && t(ROLE_LABEL_KEY[w.role])}
                    </span>
                    {isActive && <Check size={12} className="text-primary ml-1" />}
                  </DropdownMenuItem>
                )
              })}
              <DropdownMenuSeparator />
            </>
          )}

          {/* Workspace actions */}
          <DropdownMenuItem
            onClick={() => navigate('/workspace/settings')}
            className="flex items-center gap-2"
          >
            <Settings size={14} />
            <span className="flex-1">{t('workspace.settingsMenu', 'Workspace settings')}</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() => setCreateOpen(true)}
            className="flex items-center gap-2"
          >
            <Plus size={14} />
            <span className="flex-1">{t('workspace.create', 'New workspace')}</span>
          </DropdownMenuItem>

          <DropdownMenuSeparator />

          {/* Admin */}
          {user.is_superuser && (
            <DropdownMenuItem
              onClick={() => navigate('/admin')}
              className="flex items-center gap-2"
            >
              <Shield size={14} />
              {t('nav.groupAdmin')}
            </DropdownMenuItem>
          )}

          {/* Account actions */}
          <DropdownMenuItem
            onClick={onChangePassword}
            className="flex items-center gap-2"
          >
            <KeyRound size={14} />
            {t('auth.changePassword')}
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={onTwoFactor}
            className="flex items-center gap-2"
          >
            <ShieldCheck size={14} />
            {t('auth.twoFactorTitle')}
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={backingUp}
            onClick={onBackup}
            className="flex items-center gap-2"
          >
            <HardDriveDownload size={14} />
            {backingUp ? t('backup.downloading') : t('backup.button')}
          </DropdownMenuItem>

          {agentsEnabled && (
            <DropdownMenuItem
              onClick={() => navigate('/agents')}
              className="flex items-center gap-2"
            >
              <Sparkles size={14} />
              {t('nav.aiAgents')}
            </DropdownMenuItem>
          )}

          <DropdownMenuItem
            onClick={onUpdateAvailable}
            className="flex items-center gap-2"
          >
            <Download size={14} />
            {t('update.menuItem')}
          </DropdownMenuItem>

          {/* Language sub-menu */}
          <DropdownMenuSub>
            <DropdownMenuSubTrigger className="flex items-center gap-2">
              <Languages size={14} />
              <span className="flex-1">{t('setup.language')}</span>
              <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                {currentLang.split('-')[0]}
              </span>
            </DropdownMenuSubTrigger>
            <DropdownMenuPortal>
              <DropdownMenuSubContent className="w-40">
                <DropdownMenuLabel className="px-2 py-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/70">
                  {t('setup.language')}
                </DropdownMenuLabel>
                <DropdownMenuItem
                  onClick={() => i18n.changeLanguage('pt-BR')}
                  className="flex items-center gap-2"
                >
                  <span className="flex-1">Português</span>
                  {currentLang === 'pt-BR' && <Check size={13} className="text-primary" />}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => i18n.changeLanguage('en')}
                  className="flex items-center gap-2"
                >
                  <span className="flex-1">English</span>
                  {currentLang === 'en' && <Check size={13} className="text-primary" />}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => i18n.changeLanguage('es')}
                  className="flex items-center gap-2"
                >
                  <span className="flex-1">Español</span>
                  {currentLang === 'es' && <Check size={13} className="text-primary" />}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => i18n.changeLanguage('pl')}
                  className="flex items-center gap-2"
                >
                  <span className="flex-1">Polski</span>
                  {currentLang === 'pl' && <Check size={13} className="text-primary" />}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => i18n.changeLanguage('it')}
                  className="flex items-center gap-2"
                >
                  <span className="flex-1">Italiano</span>
                  {currentLang === 'it' && <Check size={13} className="text-primary" />}
                </DropdownMenuItem>
              </DropdownMenuSubContent>
            </DropdownMenuPortal>
          </DropdownMenuSub>

          <DropdownMenuSeparator />

          <DropdownMenuItem
            onClick={logout}
            className="flex items-center gap-2 text-rose-600 focus:text-rose-600"
          >
            <LogOut size={14} />
            {t('auth.logout')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {/* New workspace dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('workspace.createTitle', 'New workspace')}</DialogTitle>
            <DialogDescription>
              {t(
                'workspace.createDescription',
                'A workspace holds its own accounts, categories, budgets, and goals. You can invite people into it from the workspace settings page.',
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="ws-create-name" className="text-[13px]">
                {t('common.name', 'Name')}
              </Label>
              <Input
                id="ws-create-name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder={t('workspace.createPlaceholder', 'e.g. Side project, Family')}
                className="h-10 rounded-lg"
                autoFocus
                maxLength={100}
              />
            </div>
          </div>
          <DialogFooter className="mt-2">
            <Button
              variant="outline"
              onClick={() => setCreateOpen(false)}
              className="rounded-lg"
            >
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !newName.trim()}
              className="rounded-lg"
            >
              {createMutation.isPending ? t('common.loading') : t('common.create', 'Create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
