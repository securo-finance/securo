import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { ArrowLeft, Check, Copy, KeySquare, Loader2, Plus, TriangleAlert, X } from 'lucide-react'
import { accessTokens, workspaces } from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { useDateLocale } from '@/hooks/use-display-locale'
import { formatDate } from '@/lib/date-range-format'
import type { AccessToken, AccessTokenCreated } from '@/types'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

interface AccessTokensDialogProps {
  open: boolean
  onClose: () => void
}

const EXPIRY_OPTIONS = ['30', '90', '365', 'never'] as const
type ExpiryOption = (typeof EXPIRY_OPTIONS)[number]

type Level = 'read' | 'write'

interface ScopeResource {
  name: string
  /** False for resources the API only ever reads, such as reports. */
  writable: boolean
}

const FORM_ID = 'access-token-form'
const CHECKBOX_CLASS = 'rounded border-border text-primary focus:ring-primary disabled:opacity-50'

export function AccessTokensDialog(props: AccessTokensDialogProps) {
  return props.open ? <AccessTokensSession {...props} /> : null
}

/** Group the scope catalog by resource, keeping the server's order. */
function toResources(scopes: string[]): ScopeResource[] {
  const resources = new Map<string, ScopeResource>()
  for (const scope of scopes) {
    const [name, level] = scope.split(':')
    const resource = resources.get(name) ?? { name, writable: false }
    if (level === 'write') resource.writable = true
    resources.set(name, resource)
  }
  return [...resources.values()]
}

function isExpired(token: AccessToken): boolean {
  return token.expires_at !== null && new Date(token.expires_at).getTime() <= Date.now()
}

function AccessTokensSession({ open, onClose }: AccessTokensDialogProps) {
  const { t, i18n } = useTranslation()
  const { user } = useAuth()
  const { current, canWrite } = useWorkspace()
  const dateLocale = useDateLocale()
  // The dialog shows the token list, or the form for a new token in its place.
  const [view, setView] = useState<'list' | 'create'>('list')
  const [tokens, setTokens] = useState<AccessToken[]>([])
  const [resources, setResources] = useState<ScopeResource[]>([])
  const [memberNames, setMemberNames] = useState<Record<string, string>>({})
  const [name, setName] = useState('')
  const [expiry, setExpiry] = useState<ExpiryOption>('90')
  const [selected, setSelected] = useState<Record<string, Level>>({})
  const [created, setCreated] = useState<AccessTokenCreated | null>(null)
  const [copied, setCopied] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [saving, setSaving] = useState(false)
  const [revokingId, setRevokingId] = useState<string | null>(null)
  const [confirmRevokeId, setConfirmRevokeId] = useState<string | null>(null)

  const workspaceId = current?.id
  const userId = user?.id

  const load = useCallback(async () => {
    setLoading(true)
    setLoadFailed(false)
    try {
      const [scopes, list] = await Promise.all([accessTokens.scopes(), accessTokens.list()])
      setResources(toResources(scopes))
      setTokens(list)
      // An owner's list includes other members' tokens. Their names are only
      // a label, so a failed lookup leaves the rows without one.
      if (workspaceId && list.some((token) => token.user_id !== userId)) {
        try {
          const members = await workspaces.listMembers(workspaceId)
          setMemberNames(
            Object.fromEntries(members.map((m) => [m.user_id, m.display_name || m.email])),
          )
        } catch {
          setMemberNames({})
        }
      }
    } catch {
      setLoadFailed(true)
    } finally {
      setLoading(false)
    }
  }, [workspaceId, userId])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  const resourceLabel = (resource: string) =>
    t(`plugins.code.resource.${resource}`, { defaultValue: resource })

  /** What a token may do, as one sentence per level: "Read and change accounts, rules". */
  const describeScopes = (scopes: string[]): string[] => {
    const list = new Intl.ListFormat(i18n.language, { style: 'short', type: 'unit' })
    const labelsFor = (level: Level) =>
      scopes.filter((scope) => scope.endsWith(`:${level}`)).map((scope) => resourceLabel(scope.split(':')[0]))
    const write = labelsFor('write')
    const read = labelsFor('read')
    return [
      ...(write.length > 0 ? [t('plugins.code.write', { what: list.format(write) })] : []),
      ...(read.length > 0 ? [t('plugins.code.read', { what: list.format(read) })] : []),
    ]
  }

  const canWriteResource = (resource: ScopeResource) => resource.writable && canWrite

  // Write includes read, so a resource needs only its highest scope.
  const chosenScopes = useMemo(
    () => resources.flatMap((r) => (selected[r.name] ? [`${r.name}:${selected[r.name]}`] : [])),
    [resources, selected],
  )

  const setLevel = (resource: string, level: Level | null) =>
    setSelected((existing) => {
      const next = { ...existing }
      if (level) next[resource] = level
      else delete next[resource]
      return next
    })

  const writableResources = resources.filter(canWriteResource)
  const allRead = resources.length > 0 && resources.every((r) => selected[r.name])
  const allWrite =
    writableResources.length > 0 && writableResources.every((r) => selected[r.name] === 'write')

  const toggleAllRead = () =>
    setSelected(allRead ? {} : Object.fromEntries(resources.map((r) => [r.name, selected[r.name] ?? 'read'])))

  const toggleAllWrite = () =>
    setSelected((existing) => {
      const next = { ...existing }
      for (const r of writableResources) next[r.name] = allWrite ? 'read' : 'write'
      return next
    })

  const openCreate = () => {
    setConfirmRevokeId(null)
    setView('create')
  }

  const handleCreate = async (event: React.FormEvent) => {
    event.preventDefault()
    const tokenName = name.trim()
    if (!tokenName || chosenScopes.length === 0) return
    setSaving(true)
    try {
      const result = await accessTokens.create({
        name: tokenName,
        scopes: chosenScopes,
        expires_in_days: expiry === 'never' ? null : Number(expiry),
      })
      setTokens((existing) => [result, ...existing])
      setCreated(result)
      setCopied(false)
      setName('')
      setExpiry('90')
      setSelected({})
      setView('list')
    } catch {
      toast.error(t('accessTokens.createError'))
    } finally {
      setSaving(false)
    }
  }

  const handleCopy = async () => {
    if (!created) return
    try {
      await navigator.clipboard.writeText(created.token)
      setCopied(true)
    } catch {
      toast.error(t('accessTokens.copyError'))
    }
  }

  const handleRevoke = async (token: AccessToken) => {
    setRevokingId(token.id)
    try {
      await accessTokens.revoke(token.id)
      // The API keeps revoked tokens in the list, so the row stays.
      const revokedAt = new Date().toISOString()
      setTokens((existing) =>
        existing.map((item) =>
          item.id === token.id ? { ...item, revoked_at: revokedAt, is_active: false } : item,
        ),
      )
      if (created?.id === token.id) setCreated(null)
      setConfirmRevokeId(null)
      toast.success(t('accessTokens.revoked'))
    } catch {
      toast.error(t('accessTokens.revokeError'))
    } finally {
      setRevokingId(null)
    }
  }

  const createView = (
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="-ml-2 h-7 w-7"
            onClick={() => setView('list')}
            disabled={saving}
            aria-label={t('common.back')}
          >
            <ArrowLeft size={16} />
          </Button>
          {t('accessTokens.newToken')}
        </DialogTitle>
      </DialogHeader>

      <form id={FORM_ID} onSubmit={handleCreate} className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-[1fr_11rem]">
          <div className="space-y-1.5">
            <Label htmlFor="access-token-name">{t('accessTokens.name')}</Label>
            <Input
              id="access-token-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={t('accessTokens.namePlaceholder')}
              maxLength={100}
              disabled={saving}
              autoFocus
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="access-token-expiry">{t('accessTokens.expiry')}</Label>
            <Select value={expiry} onValueChange={(value) => setExpiry(value as ExpiryOption)}>
              <SelectTrigger id="access-token-expiry" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {EXPIRY_OPTIONS.map((option) => (
                  <SelectItem key={option} value={option}>
                    {option === 'never'
                      ? t('accessTokens.expiryNever')
                      : t('accessTokens.expiryDays', { days: option })}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="space-y-1.5">
          <div className="overflow-hidden rounded-lg border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 text-left text-sm font-medium text-foreground">
                    {t('accessTokens.scopes')}
                  </th>
                  <th className="w-24 px-3 py-2 font-normal">
                    <label className="flex items-center justify-end gap-1.5">
                      {t('accessTokens.scopeRead')}
                      <input
                        type="checkbox"
                        className={CHECKBOX_CLASS}
                        checked={allRead}
                        onChange={toggleAllRead}
                        disabled={saving}
                      />
                    </label>
                  </th>
                  <th className="w-24 px-3 py-2 font-normal">
                    <label className="flex items-center justify-end gap-1.5">
                      {t('accessTokens.scopeWrite')}
                      <input
                        type="checkbox"
                        className={CHECKBOX_CLASS}
                        checked={allWrite}
                        onChange={toggleAllWrite}
                        disabled={saving || writableResources.length === 0}
                      />
                    </label>
                  </th>
                </tr>
              </thead>
              <tbody>
                {resources.map((resource) => {
                  const level = selected[resource.name]
                  const label = resourceLabel(resource.name)
                  return (
                    <tr key={resource.name} className="border-t">
                      <td className="px-3 py-1.5 first-letter:uppercase">{label}</td>
                      <td className="px-3 text-right">
                        <input
                          type="checkbox"
                          className={CHECKBOX_CLASS}
                          aria-label={`${label}: ${t('accessTokens.scopeRead')}`}
                          checked={Boolean(level)}
                          onChange={() => setLevel(resource.name, level ? null : 'read')}
                          disabled={saving || level === 'write'}
                        />
                      </td>
                      <td className="px-3 text-right">
                        {resource.writable && (
                          <input
                            type="checkbox"
                            className={CHECKBOX_CLASS}
                            aria-label={`${label}: ${t('accessTokens.scopeWrite')}`}
                            checked={level === 'write'}
                            onChange={() => setLevel(resource.name, level === 'write' ? 'read' : 'write')}
                            disabled={saving || !canWrite}
                          />
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {!canWrite && <p className="text-xs text-muted-foreground">{t('accessTokens.viewerNote')}</p>}
          {current && (
            <p className="text-xs text-muted-foreground">
              {t('accessTokens.workspaceNote', { workspace: current.name })}
            </p>
          )}
        </div>
      </form>

      <DialogFooter>
        <Button type="button" variant="outline" onClick={() => setView('list')} disabled={saving}>
          {t('common.cancel')}
        </Button>
        <Button
          type="submit"
          form={FORM_ID}
          disabled={saving || !name.trim() || chosenScopes.length === 0}
        >
          {saving && <Loader2 size={15} className="animate-spin" />}
          {t('accessTokens.create')}
        </Button>
      </DialogFooter>
    </>
  )

  const listView = (
    <>
      <DialogHeader>
        <DialogTitle>{t('accessTokens.title')}</DialogTitle>
      </DialogHeader>

      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">{t('accessTokens.description')}</p>

        {created && (
          <div className="space-y-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3">
            <div className="flex items-start gap-2 text-sm text-amber-700 dark:text-amber-300">
              <TriangleAlert size={16} className="mt-0.5 shrink-0" />
              <p>{t('accessTokens.copyNow')}</p>
            </div>
            <div className="flex gap-2">
              <Input
                readOnly
                value={created.token}
                className="font-mono text-xs"
                aria-label={t('accessTokens.newToken')}
                onFocus={(event) => event.currentTarget.select()}
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-9 shrink-0"
                onClick={() => void handleCopy()}
                aria-label={t('accessTokens.copy')}
              >
                {copied ? <Check size={14} /> : <Copy size={14} />}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">{t('accessTokens.usage')}</p>
            <Button type="button" variant="ghost" size="sm" onClick={() => setCreated(null)}>
              {t('accessTokens.done')}
            </Button>
          </div>
        )}

        <div className="space-y-2">
          {loading ? (
            <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
          ) : loadFailed ? (
            <div className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 p-3">
              <p className="text-sm text-destructive">{t('accessTokens.loadError')}</p>
              <Button type="button" variant="outline" size="sm" onClick={() => void load()}>
                {t('common.retry')}
              </Button>
            </div>
          ) : tokens.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed px-3 py-6 text-center">
              <KeySquare size={20} className="text-muted-foreground" />
              <p className="text-sm text-muted-foreground">{t('accessTokens.empty')}</p>
            </div>
          ) : (
            tokens.map((token) => {
              const isConfirming = confirmRevokeId === token.id
              const isRevoking = revokingId === token.id
              const revoked = token.revoked_at !== null
              const expired = !revoked && isExpired(token)
              const readOnly = !token.scopes.some((scope) => scope.endsWith(':write'))
              const creator = token.user_id !== userId ? memberNames[token.user_id] : undefined
              const access = describeScopes(token.scopes)

              return (
                <div
                  key={token.id}
                  data-testid="access-token-row"
                  className="flex items-start gap-3 rounded-lg border p-3"
                >
                  <div className="mt-0.5 rounded-full bg-primary/10 p-2 text-primary">
                    <KeySquare size={16} />
                  </div>
                  <div className={`min-w-0 flex-1 space-y-1 ${revoked || expired ? 'opacity-60' : ''}`}>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <p className="truncate text-sm font-medium">{token.name}</p>
                      {readOnly && <Badge variant="secondary">{t('accessTokens.readOnlyBadge')}</Badge>}
                      {revoked && <Badge variant="destructive">{t('accessTokens.revokedBadge')}</Badge>}
                      {expired && <Badge variant="destructive">{t('accessTokens.expiredBadge')}</Badge>}
                    </div>
                    <p className="truncate font-mono text-xs text-muted-foreground">{token.token_prefix}…</p>
                    {access.map((line) => (
                      <p key={line} title={line} className="line-clamp-2 text-xs text-muted-foreground">
                        {line}
                      </p>
                    ))}
                    <dl className="flex flex-wrap gap-x-4 text-xs text-muted-foreground">
                      <div className="flex gap-1">
                        <dt>{t('accessTokens.lastUsed')}:</dt>
                        <dd>{token.last_used_at ? formatDate(token.last_used_at, dateLocale) : t('accessTokens.neverUsed')}</dd>
                      </div>
                      <div className="flex gap-1">
                        <dt>{t('accessTokens.expires')}:</dt>
                        <dd>{token.expires_at ? formatDate(token.expires_at, dateLocale) : t('accessTokens.expiryNever')}</dd>
                      </div>
                    </dl>
                    {creator && (
                      <p className="text-xs text-muted-foreground">
                        {t('accessTokens.createdBy', { member: creator })}
                      </p>
                    )}
                  </div>
                  {revoked ? null : isConfirming ? (
                    <div className="flex shrink-0 items-center gap-1">
                      <Button
                        type="button"
                        variant="destructive"
                        size="sm"
                        onClick={() => void handleRevoke(token)}
                        disabled={isRevoking}
                      >
                        {isRevoking ? <Loader2 size={13} className="animate-spin" /> : t('accessTokens.revoke')}
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => setConfirmRevokeId(null)}
                        disabled={isRevoking}
                        aria-label={t('common.cancel')}
                      >
                        <X size={14} />
                      </Button>
                    </div>
                  ) : (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="shrink-0 text-muted-foreground hover:text-destructive"
                      onClick={() => setConfirmRevokeId(token.id)}
                    >
                      {t('accessTokens.revoke')}
                    </Button>
                  )}
                </div>
              )
            })
          )}
        </div>
      </div>

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>
          {t('common.close')}
        </Button>
        <Button type="button" onClick={openCreate} disabled={loading || loadFailed}>
          <Plus size={15} />
          {t('accessTokens.newToken')}
        </Button>
      </DialogFooter>
    </>
  )

  return (
    // Closing while a token is being created would drop the only response
    // that carries its plaintext, so the dialog stays open until it settles.
    <Dialog open={open} onOpenChange={(v) => !v && !saving && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        {view === 'create' ? createView : listView}
      </DialogContent>
    </Dialog>
  )
}
