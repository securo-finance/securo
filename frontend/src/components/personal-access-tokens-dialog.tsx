import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Check, Copy, KeySquare, Loader2, TriangleAlert, X } from 'lucide-react'
import { personalAccessTokens } from '@/lib/api'
import { useWorkspace } from '@/contexts/workspace-context'
import type { PersonalAccessToken, PersonalAccessTokenCreated } from '@/types'
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
import { Switch } from '@/components/ui/switch'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

interface PersonalAccessTokensDialogProps {
  open: boolean
  onClose: () => void
}

const EXPIRY_OPTIONS = ['30', '90', '365', 'never'] as const
type ExpiryOption = (typeof EXPIRY_OPTIONS)[number]

export function PersonalAccessTokensDialog(props: PersonalAccessTokensDialogProps) {
  return props.open ? <PersonalAccessTokensSession {...props} /> : null
}

function isExpired(token: PersonalAccessToken): boolean {
  return token.expires_at !== null && new Date(token.expires_at).getTime() <= Date.now()
}

function PersonalAccessTokensSession({ open, onClose }: PersonalAccessTokensDialogProps) {
  const { t } = useTranslation()
  const { current } = useWorkspace()
  const [tokens, setTokens] = useState<PersonalAccessToken[]>([])
  const [name, setName] = useState('')
  const [expiry, setExpiry] = useState<ExpiryOption>('90')
  const [readOnly, setReadOnly] = useState(false)
  const [created, setCreated] = useState<PersonalAccessTokenCreated | null>(null)
  const [copied, setCopied] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [saving, setSaving] = useState(false)
  const [revokingId, setRevokingId] = useState<string | null>(null)
  const [confirmRevokeId, setConfirmRevokeId] = useState<string | null>(null)

  const loadTokens = useCallback(async () => {
    setLoading(true)
    setLoadFailed(false)
    try {
      setTokens(await personalAccessTokens.list())
    } catch {
      setLoadFailed(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) void loadTokens()
  }, [open, loadTokens])

  const formatDate = (value: string) =>
    new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(value))

  const handleCreate = async (event: React.FormEvent) => {
    event.preventDefault()
    const tokenName = name.trim()
    if (!tokenName) return
    setSaving(true)
    try {
      const result = await personalAccessTokens.create({
        name: tokenName,
        read_only: readOnly,
        expires_in_days: expiry === 'never' ? null : Number(expiry),
      })
      setTokens((existing) => [result, ...existing])
      setCreated(result)
      setCopied(false)
      setName('')
      setReadOnly(false)
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

  const handleRevoke = async (token: PersonalAccessToken) => {
    setRevokingId(token.id)
    try {
      await personalAccessTokens.revoke(token.id)
      setTokens((existing) => existing.filter((item) => item.id !== token.id))
      if (created?.id === token.id) setCreated(null)
      setConfirmRevokeId(null)
      toast.success(t('accessTokens.revoked'))
    } catch {
      toast.error(t('accessTokens.revokeError'))
    } finally {
      setRevokingId(null)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('accessTokens.title')}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">{t('accessTokens.description')}</p>

          {created ? (
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
          ) : (
            <form onSubmit={handleCreate} className="space-y-3 rounded-lg border p-3">
              <div className="space-y-1.5">
                <Label htmlFor="pat-name">{t('accessTokens.name')}</Label>
                <Input
                  id="pat-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder={t('accessTokens.namePlaceholder')}
                  maxLength={100}
                  disabled={saving}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="pat-expiry">{t('accessTokens.expiry')}</Label>
                <Select value={expiry} onValueChange={(value) => setExpiry(value as ExpiryOption)}>
                  <SelectTrigger id="pat-expiry" className="w-full">
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
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p id="pat-read-only-label" className="text-sm font-medium">{t('accessTokens.readOnly')}</p>
                  <p className="text-xs text-muted-foreground">{t('accessTokens.readOnlyHint')}</p>
                </div>
                <Switch
                  checked={readOnly}
                  onCheckedChange={setReadOnly}
                  aria-labelledby="pat-read-only-label"
                  disabled={saving}
                />
              </div>
              {current && (
                <p className="text-xs text-muted-foreground">
                  {t('accessTokens.workspaceNote', { workspace: current.name })}
                </p>
              )}
              <Button type="submit" disabled={saving || !name.trim()} className="w-full">
                {saving && <Loader2 size={15} className="animate-spin" />}
                {t('accessTokens.create')}
              </Button>
            </form>
          )}

          <div className="space-y-2">
            {loading ? (
              <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
            ) : loadFailed ? (
              <div className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 p-3">
                <p className="text-sm text-destructive">{t('accessTokens.loadError')}</p>
                <Button type="button" variant="outline" size="sm" onClick={() => void loadTokens()}>
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
                const expired = isExpired(token)

                return (
                  <div key={token.id} className="flex items-start gap-3 rounded-lg border p-3">
                    <div className="mt-0.5 rounded-full bg-primary/10 p-2 text-primary">
                      <KeySquare size={16} />
                    </div>
                    <div className="min-w-0 flex-1 space-y-0.5">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <p className="truncate text-sm font-medium">{token.name}</p>
                        {token.read_only && <Badge variant="secondary">{t('accessTokens.readOnlyBadge')}</Badge>}
                        {expired && <Badge variant="destructive">{t('accessTokens.expiredBadge')}</Badge>}
                      </div>
                      <p className="truncate font-mono text-xs text-muted-foreground">{token.token_prefix}…</p>
                      <p className="text-xs text-muted-foreground">
                        {t('accessTokens.workspace')}: {token.workspace_name}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {t('accessTokens.lastUsed')}:{' '}
                        {token.last_used_at ? formatDate(token.last_used_at) : t('accessTokens.neverUsed')}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {t('accessTokens.expires')}:{' '}
                        {token.expires_at ? formatDate(token.expires_at) : t('accessTokens.expiryNever')}
                      </p>
                    </div>
                    {isConfirming ? (
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
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
