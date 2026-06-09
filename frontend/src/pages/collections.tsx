import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { collections as collectionsApi, accounts as accountsApi, assetGroups as assetGroupsApi } from '@/lib/api'
import { getAccountName } from '@/lib/account-utils'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from '@/components/ui/dialog'
import { FolderOpen, Plus, Pencil, Trash2 } from 'lucide-react'
import type { Collection } from '@/types'

const SWATCHES = ['#6366F1', '#0EA5E9', '#10B981', '#F59E0B', '#EF4444', '#EC4899', '#8B5CF6', '#64748B']

export default function CollectionsPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Collection | null>(null)
  const [deleting, setDeleting] = useState<Collection | null>(null)

  const { data: collections, isLoading } = useQuery({
    queryKey: ['collections'],
    queryFn: collectionsApi.list,
  })
  const { data: accounts } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })
  const { data: wallets } = useQuery({
    queryKey: ['asset-groups'],
    queryFn: assetGroupsApi.list,
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['collections'] })

  const createMutation = useMutation({
    mutationFn: collectionsApi.create,
    onSuccess: () => { invalidate(); setDialogOpen(false); toast.success(t('collections.created')) },
    onError: () => toast.error(t('common.error')),
  })
  const updateMutation = useMutation({
    mutationFn: ({ id, ...payload }: { id: string } & Record<string, unknown>) => collectionsApi.update(id, payload),
    onSuccess: () => { invalidate(); setDialogOpen(false); setEditing(null); toast.success(t('collections.updated')) },
    onError: () => toast.error(t('common.error')),
  })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => collectionsApi.delete(id),
    onSuccess: () => { invalidate(); setDeleting(null); toast.success(t('collections.deleted')) },
    onError: () => toast.error(t('common.error')),
  })

  const accountName = useMemo(() => {
    const map = new Map<string, string>()
    ;(accounts ?? []).forEach((a) => map.set(a.id, getAccountName(a)))
    return map
  }, [accounts])

  const list = collections ?? []

  return (
    <div>
      <PageHeader
        section={t('nav.groupSetup')}
        title={t('collections.title')}
        action={
          <Button onClick={() => { setEditing(null); setDialogOpen(true) }}>
            <Plus size={16} className="mr-1.5" />
            {t('collections.add')}
          </Button>
        }
      />
      <p className="text-sm text-muted-foreground mb-5 max-w-2xl">{t('collections.subtitle')}</p>

      <div className="rounded-xl border border-border/60 bg-card overflow-hidden">
        {isLoading ? (
          <div className="p-4 space-y-3">{[...Array(3)].map((_, i) => <Skeleton key={i} className="h-14 w-full rounded-lg" />)}</div>
        ) : list.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
            <FolderOpen size={32} className="mb-3 opacity-40" />
            <p className="text-sm">{t('collections.empty')}</p>
          </div>
        ) : (
          <div className="divide-y divide-border/40">
            {list.map((c) => (
              <div key={c.id} className="flex items-center gap-4 px-5 py-3.5 hover:bg-muted/30 transition-colors">
                <span className="h-8 w-8 shrink-0 rounded-lg" style={{ backgroundColor: `${c.color}22` }}>
                  <span className="flex h-full w-full items-center justify-center">
                    <FolderOpen size={16} style={{ color: c.color }} />
                  </span>
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-foreground truncate">{c.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {t('collections.accountCount', { count: c.account_count })}
                    {c.wallet_count > 0 && ` · ${t('collections.walletCount', { count: c.wallet_count })}`}
                  </p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button variant="ghost" size="icon" onClick={() => { setEditing(c); setDialogOpen(true) }} aria-label={t('common.edit')}>
                    <Pencil size={15} />
                  </Button>
                  <Button variant="ghost" size="icon" onClick={() => setDeleting(c)} aria-label={t('common.delete')} className="text-muted-foreground/60 hover:text-destructive">
                    <Trash2 size={15} />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <CollectionDialog
        open={dialogOpen}
        onClose={() => { setDialogOpen(false); setEditing(null) }}
        collection={editing}
        accounts={(accounts ?? []).map((a) => ({ id: a.id, label: accountName.get(a.id) ?? a.name, currency: a.currency }))}
        wallets={(wallets ?? []).map((w) => ({ id: w.id, label: w.name }))}
        loading={createMutation.isPending || updateMutation.isPending}
        onSave={(payload) => {
          if (editing) updateMutation.mutate({ id: editing.id, ...payload })
          else createMutation.mutate(payload)
        }}
      />

      <Dialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t('collections.confirmDeleteTitle')}</DialogTitle>
            <DialogDescription>{t('collections.confirmDeleteDesc', { name: deleting?.name })}</DialogDescription>
          </DialogHeader>
          <DialogFooter className="mt-2">
            <Button variant="outline" onClick={() => setDeleting(null)}>{t('common.cancel')}</Button>
            <Button variant="destructive" disabled={deleteMutation.isPending} onClick={() => deleting && deleteMutation.mutate(deleting.id)}>
              {deleteMutation.isPending ? t('common.loading') : t('common.delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function CollectionDialog({
  open, onClose, collection, accounts, wallets, loading, onSave,
}: {
  open: boolean
  onClose: () => void
  collection: Collection | null
  accounts: { id: string; label: string; currency: string }[]
  wallets: { id: string; label: string }[]
  loading: boolean
  onSave: (payload: { name: string; color: string; account_ids: string[]; wallet_ids: string[] }) => void
}) {
  const { t } = useTranslation()
  const [name, setName] = useState('')
  const [color, setColor] = useState(SWATCHES[0])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [selectedWallets, setSelectedWallets] = useState<Set<string>>(new Set())

  useEffect(() => {
    setName(collection?.name ?? '')
    setColor(collection?.color ?? SWATCHES[0])
    setSelected(new Set(collection?.account_ids ?? []))
    setSelectedWallets(new Set(collection?.wallet_ids ?? []))
  }, [collection, open])

  const toggleIn = (setter: typeof setSelected) => (id: string) =>
    setter((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const toggle = toggleIn(setSelected)
  const toggleWallet = toggleIn(setSelectedWallets)

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{collection ? t('collections.edit') : t('collections.add')}</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (!name.trim()) return
            onSave({ name: name.trim(), color, account_ids: [...selected], wallet_ids: [...selectedWallets] })
          }}
          className="space-y-4"
        >
          <div className="space-y-1.5">
            <Label>{t('collections.name')}</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} required autoFocus placeholder={t('collections.namePlaceholder')} />
          </div>

          <div className="space-y-1.5">
            <Label>{t('collections.color')}</Label>
            <div className="flex flex-wrap gap-2">
              {SWATCHES.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setColor(s)}
                  className={`h-7 w-7 rounded-full transition-transform ${color === s ? 'ring-2 ring-offset-2 ring-offset-background scale-110' : ''}`}
                  style={{ backgroundColor: s, boxShadow: color === s ? `0 0 0 2px ${s}` : undefined }}
                  aria-label={s}
                />
              ))}
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>{t('collections.accounts')}</Label>
            <div className="max-h-56 overflow-y-auto rounded-lg border border-border/60 divide-y divide-border/40">
              {accounts.length === 0 ? (
                <p className="px-3 py-3 text-xs text-muted-foreground">{t('collections.noAccounts')}</p>
              ) : (
                accounts.map((a) => (
                  <label key={a.id} className="flex items-center gap-2.5 px-3 py-2 text-sm cursor-pointer hover:bg-muted/40">
                    <input
                      type="checkbox"
                      checked={selected.has(a.id)}
                      onChange={() => toggle(a.id)}
                      className="h-4 w-4 rounded border-border accent-primary"
                    />
                    <span className="flex-1 truncate">{a.label}</span>
                    <span className="text-[10.5px] uppercase tracking-wide text-muted-foreground/70">{a.currency}</span>
                  </label>
                ))
              )}
            </div>
            <p className="text-xs text-muted-foreground">{t('collections.accountsHint', { count: selected.size })}</p>
          </div>

          {wallets.length > 0 && (
            <div className="space-y-1.5">
              <Label>{t('collections.wallets')}</Label>
              <div className="max-h-44 overflow-y-auto rounded-lg border border-border/60 divide-y divide-border/40">
                {wallets.map((w) => (
                  <label key={w.id} className="flex items-center gap-2.5 px-3 py-2 text-sm cursor-pointer hover:bg-muted/40">
                    <input
                      type="checkbox"
                      checked={selectedWallets.has(w.id)}
                      onChange={() => toggleWallet(w.id)}
                      className="h-4 w-4 rounded border-border accent-primary"
                    />
                    <span className="flex-1 truncate">{w.label}</span>
                  </label>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">{t('collections.walletsHint', { count: selectedWallets.size })}</p>
            </div>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>{t('common.cancel')}</Button>
            <Button type="submit" disabled={loading || !name.trim()}>
              {loading ? t('common.loading') : t('common.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
