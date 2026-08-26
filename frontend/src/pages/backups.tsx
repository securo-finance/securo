import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  AlertTriangle,
  ArchiveRestore,
  Download,
  FileArchive,
  HardDriveDownload,
  RefreshCw,
  Save,
  Upload,
  ShieldCheck,
} from 'lucide-react'

import {
  backup as backupApi,
  type BackupContent,
  type BackupItem,
  type BackupPreview,
  type BackupRestoreMode,
} from '@/lib/api'
import { useWorkspace } from '@/contexts/workspace-context'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
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

const CONTENT_OPTIONS: BackupContent[] = ['both', 'configuration', 'data']
const RESTORE_MODES: BackupRestoreMode[] = ['new_workspace', 'current_workspace']

function errorMessage(error: unknown, fallback: string): string {
  return (
    (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
    (error instanceof Error ? error.message : fallback)
  )
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size >= 10 || unit === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`
}

function countSummary(counts: Record<string, number>): string {
  const important = ['accounts', 'transactions', 'categories', 'rules', 'budgets', 'goals']
    .map((key) => [key, counts[key] ?? 0] as const)
    .filter(([, count]) => count > 0)
  if (!important.length) return '0 items'
  return important.map(([key, count]) => `${count} ${key.replace(/_/g, ' ')}`).join(' · ')
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

function contentLabel(content: BackupContent, t: (key: string) => string): string {
  return t(`backup.content.${content}`)
}

function modeLabel(mode: BackupRestoreMode, t: (key: string) => string): string {
  return t(`backup.restoreMode.${mode}`)
}

function PreviewSummary({ preview }: { preview: BackupPreview }) {
  const { t } = useTranslation()
  return (
    <div className="rounded-lg border bg-muted/30 p-3 text-sm space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">{contentLabel(preview.content, t)}</Badge>
        <span className="font-medium">{preview.workspace_name || t('backup.unknownWorkspace')}</span>
        <span className="text-muted-foreground">{formatDate(preview.export_date)}</span>
      </div>
      <p className="text-muted-foreground">{countSummary(preview.entity_counts)}</p>
      {preview.warnings.length > 0 && (
        <div className="space-y-1 text-amber-700 dark:text-amber-300">
          {preview.warnings.map((warning) => (
            <p key={warning} className="flex gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{warning}</span>
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

export default function BackupPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { refresh: refreshWorkspaces } = useWorkspace()
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const [runContent, setRunContent] = useState<BackupContent>('both')
  const [scheduledEnabled, setScheduledEnabled] = useState(false)
  const [schedule, setSchedule] = useState<'daily' | 'weekly'>('daily')
  const [configContent, setConfigContent] = useState<BackupContent>('both')
  const [retentionCount, setRetentionCount] = useState('10')
  const [retentionDays, setRetentionDays] = useState('')
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadPreview, setUploadPreview] = useState<BackupPreview | null>(null)
  const [uploadContent, setUploadContent] = useState<BackupContent>('both')
  const [uploadMode, setUploadMode] = useState<BackupRestoreMode>('new_workspace')
  const [uploadConfirmation, setUploadConfirmation] = useState('')
  const [storedRestore, setStoredRestore] = useState<BackupItem | null>(null)
  const [storedMode, setStoredMode] = useState<BackupRestoreMode>('new_workspace')
  const [storedContent, setStoredContent] = useState<BackupContent>('both')
  const [storedConfirmation, setStoredConfirmation] = useState('')

  const configQuery = useQuery({
    queryKey: ['backup-config'],
    queryFn: backupApi.getConfig,
  })

  const backupsQuery = useQuery({
    queryKey: ['backups'],
    queryFn: backupApi.list,
  })

  useEffect(() => {
    const cfg = configQuery.data
    if (!cfg) return
    // Hydrate the editable form from the server config when the query resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setScheduledEnabled(cfg.scheduled_enabled)
    setSchedule(cfg.schedule)
    setConfigContent(cfg.content)
    setRetentionCount(String(cfg.retention_count))
    setRetentionDays(cfg.retention_days ? String(cfg.retention_days) : '')
  }, [configQuery.data])

  const invalidateBackups = () => {
    void queryClient.invalidateQueries({ queryKey: ['backups'] })
    void queryClient.invalidateQueries({ queryKey: ['backup-config'] })
  }

  const saveConfigMutation = useMutation({
    mutationFn: () => backupApi.updateConfig({
      scheduled_enabled: scheduledEnabled,
      schedule,
      content: configContent,
      retention_count: Number(retentionCount) || 10,
      retention_days: retentionDays.trim() ? Number(retentionDays) : null,
    }),
    onSuccess: () => {
      toast.success(t('backup.configSaved'))
      invalidateBackups()
    },
    onError: (error) => toast.error(errorMessage(error, t('backup.configError'))),
  })

  const runMutation = useMutation({
    mutationFn: () => backupApi.run(runContent),
    onSuccess: () => {
      toast.success(t('backup.runSuccess'))
      invalidateBackups()
    },
    onError: (error) => toast.error(errorMessage(error, t('backup.runError'))),
  })

  const downloadNowMutation = useMutation({
    mutationFn: () => backupApi.download(runContent),
    onSuccess: () => toast.success(t('backup.success')),
    onError: (error) => toast.error(errorMessage(error, t('backup.error'))),
  })

  const downloadStoredMutation = useMutation({
    mutationFn: (backupId: string) => backupApi.downloadStored(backupId),
    onError: (error) => toast.error(errorMessage(error, t('backup.error'))),
  })

  const previewUploadMutation = useMutation({
    mutationFn: (file: File) => backupApi.previewUpload(file),
    onSuccess: (preview) => {
      setUploadPreview(preview)
      setUploadContent(preview.content)
    },
    onError: (error) => {
      setUploadPreview(null)
      toast.error(errorMessage(error, t('backup.previewError')))
    },
  })

  const restoreUploadMutation = useMutation({
    mutationFn: () => {
      if (!uploadFile) throw new Error(t('backup.noFile'))
      return backupApi.restoreUpload(uploadFile, {
        content: uploadContent,
        mode: uploadMode,
        confirmation: uploadMode === 'current_workspace' ? uploadConfirmation : undefined,
      })
    },
    onSuccess: async (result) => {
      toast.success(t('backup.restoreSuccess', { name: result.workspace_name }))
      setUploadFile(null)
      setUploadPreview(null)
      setUploadConfirmation('')
      if (fileInputRef.current) fileInputRef.current.value = ''
      await refreshWorkspaces()
      invalidateBackups()
    },
    onError: (error) => toast.error(errorMessage(error, t('backup.restoreError'))),
  })

  const restoreStoredMutation = useMutation({
    mutationFn: () => {
      if (!storedRestore) throw new Error(t('backup.noBackupSelected'))
      return backupApi.restoreStored(storedRestore.id, {
        content: storedContent,
        mode: storedMode,
        confirmation: storedMode === 'current_workspace' ? storedConfirmation : undefined,
      })
    },
    onSuccess: async (result) => {
      toast.success(t('backup.restoreSuccess', { name: result.workspace_name }))
      setStoredRestore(null)
      setStoredConfirmation('')
      await refreshWorkspaces()
      invalidateBackups()
    },
    onError: (error) => toast.error(errorMessage(error, t('backup.restoreError'))),
  })

  const backups = backupsQuery.data ?? []

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{t('backup.title')}</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">{t('backup.subtitle')}</p>
        </div>
        <Badge variant="secondary" className="w-fit gap-1.5 rounded-full px-2.5 py-1 text-xs">
          <ShieldCheck className="h-3.5 w-3.5" />
          {t('backup.safeBadge')}
        </Badge>
      </div>

      <Card className="overflow-hidden">
        <CardContent className="grid gap-0 p-0 xl:grid-cols-2">
          <section className="space-y-3 p-4">
            <div className="flex items-start gap-3">
              <span className="mt-0.5 rounded-lg bg-primary/10 p-2 text-primary">
                <HardDriveDownload className="h-4 w-4" />
              </span>
              <div className="min-w-0 space-y-1">
                <h2 className="text-base font-semibold leading-none tracking-tight">{t('backup.runTitle')}</h2>
                <p className="text-xs leading-5 text-muted-foreground">{t('backup.runDescription')}</p>
              </div>
            </div>
            <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_auto_auto] lg:items-end xl:grid-cols-[minmax(0,1fr)_auto_auto]">
              <div className="grid gap-1.5">
                <Label className="text-xs font-medium text-muted-foreground">{t('backup.contentLabel')}</Label>
                <Select value={runContent} onValueChange={(value) => setRunContent(value as BackupContent)}>
                  <SelectTrigger className="h-9 w-full text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CONTENT_OPTIONS.map((content) => (
                      <SelectItem key={content} value={content}>{contentLabel(content, t)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button size="sm" className="h-9" onClick={() => runMutation.mutate()} disabled={runMutation.isPending}>
                <HardDriveDownload className="h-4 w-4" />
                {runMutation.isPending ? t('backup.running') : t('backup.runNow')}
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-9"
                onClick={() => downloadNowMutation.mutate()}
                disabled={downloadNowMutation.isPending}
              >
                <Download className="h-4 w-4" />
                {downloadNowMutation.isPending ? t('backup.downloading') : t('backup.downloadNow')}
              </Button>
            </div>
            <div className="rounded-lg border border-primary/15 bg-primary/5 px-3 py-2 text-xs leading-5 text-muted-foreground">
              {t('backup.secretNotice')}
            </div>
          </section>

          <section className="space-y-3 border-t p-4 xl:border-l xl:border-t-0">
            <div className="flex items-start gap-3">
              <span className="mt-0.5 rounded-lg bg-primary/10 p-2 text-primary">
                <Upload className="h-4 w-4" />
              </span>
              <div className="min-w-0 space-y-1">
                <h2 className="text-base font-semibold leading-none tracking-tight">{t('backup.uploadTitle')}</h2>
                <p className="text-xs leading-5 text-muted-foreground">{t('backup.uploadDescription')}</p>
              </div>
            </div>
            <div className="grid gap-1.5">
              <Label className="text-xs font-medium text-muted-foreground">{t('backup.uploadFileLabel')}</Label>
              <Input
                ref={fileInputRef}
                className="hidden"
                type="file"
                accept=".zip,application/zip"
                onChange={(e) => {
                  const file = e.target.files?.[0] ?? null
                  setUploadFile(file)
                  setUploadPreview(null)
                  if (file) previewUploadMutation.mutate(file)
                }}
              />
              <div className="grid gap-2 sm:grid-cols-[auto_minmax(0,1fr)]">
                <Button type="button" size="sm" variant="outline" className="h-9" onClick={() => fileInputRef.current?.click()}>
                  <Upload className="h-4 w-4" />
                  {t('backup.chooseFile')}
                </Button>
                <span className="min-w-0 truncate rounded-md border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
                  {uploadFile?.name ?? t('backup.noFileSelected')}
                </span>
              </div>
            </div>
            {previewUploadMutation.isPending && (
              <p className="text-sm text-muted-foreground">{t('backup.previewing')}</p>
            )}
            {uploadPreview && <PreviewSummary preview={uploadPreview} />}
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end">
              <div className="grid gap-1.5">
                <Label className="text-xs font-medium text-muted-foreground">{t('backup.contentLabel')}</Label>
                <Select value={uploadContent} onValueChange={(value) => setUploadContent(value as BackupContent)}>
                  <SelectTrigger className="h-9 w-full text-sm"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CONTENT_OPTIONS.map((content) => (
                      <SelectItem key={content} value={content}>{contentLabel(content, t)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-1.5">
                <Label className="text-xs font-medium text-muted-foreground">{t('backup.restoreModeLabel')}</Label>
                <Select value={uploadMode} onValueChange={(value) => setUploadMode(value as BackupRestoreMode)}>
                  <SelectTrigger className="h-9 w-full text-sm"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {RESTORE_MODES.map((mode) => (
                      <SelectItem key={mode} value={mode}>{modeLabel(mode, t)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button
                size="sm"
                className="h-9 px-4"
                onClick={() => restoreUploadMutation.mutate()}
                disabled={!uploadFile || restoreUploadMutation.isPending || (uploadMode === 'current_workspace' && uploadConfirmation !== 'RESTORE')}
              >
                <ArchiveRestore className="h-4 w-4" />
                {restoreUploadMutation.isPending ? t('backup.restoring') : t('backup.restore')}
              </Button>
            </div>
            {uploadMode === 'current_workspace' && (
              <div className="space-y-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3">
                <Label className="text-xs font-medium text-muted-foreground">{t('backup.confirmRestoreLabel')}</Label>
                <Input className="h-9 text-sm" value={uploadConfirmation} onChange={(e) => setUploadConfirmation(e.target.value)} placeholder="RESTORE" />
                <p className="text-xs text-muted-foreground">{t('backup.currentWorkspaceWarning')}</p>
              </div>
            )}
          </section>
        </CardContent>
      </Card>

      <Card className="overflow-hidden">
        <CardHeader className="px-4 pb-2 pt-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0 space-y-1">
              <CardTitle className="text-base">{t('backup.scheduleTitle')}</CardTitle>
              <CardDescription className="max-w-4xl text-xs leading-5">{t('backup.scheduleDescription')}</CardDescription>
            </div>
            <Switch checked={scheduledEnabled} onCheckedChange={setScheduledEnabled} className="shrink-0" />
          </div>
        </CardHeader>
        <CardContent className="space-y-3 px-4 pb-4">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-[9rem_minmax(14rem,18rem)_7rem_9rem] xl:justify-start">
            <div className="grid gap-1.5">
              <Label className="text-xs font-medium text-muted-foreground">{t('backup.scheduleLabel')}</Label>
              <Select value={schedule} onValueChange={(value) => setSchedule(value as 'daily' | 'weekly')}>
                <SelectTrigger className="h-9 w-full text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="daily">{t('backup.daily')}</SelectItem>
                  <SelectItem value="weekly">{t('backup.weekly')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label className="text-xs font-medium text-muted-foreground">{t('backup.contentLabel')}</Label>
              <Select value={configContent} onValueChange={(value) => setConfigContent(value as BackupContent)}>
                <SelectTrigger className="h-9 w-full text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {CONTENT_OPTIONS.map((content) => (
                    <SelectItem key={content} value={content}>{contentLabel(content, t)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label className="text-xs font-medium text-muted-foreground">{t('backup.retentionCount')}</Label>
              <Input className="h-9 text-sm" value={retentionCount} onChange={(e) => setRetentionCount(e.target.value)} inputMode="numeric" />
            </div>
            <div className="grid gap-1.5">
              <Label className="text-xs font-medium text-muted-foreground">{t('backup.retentionDays')}</Label>
              <Input className="h-9 text-sm" value={retentionDays} onChange={(e) => setRetentionDays(e.target.value)} inputMode="numeric" placeholder={t('backup.optional')} />
            </div>
          </div>
          <div className="flex flex-col gap-3 rounded-lg border bg-muted/20 px-3 py-2.5 text-xs sm:flex-row sm:items-center sm:justify-between">
            <div className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-muted-foreground">
              <span>{t('backup.destinationHint')}</span>
              <span><span className="font-medium text-foreground/80">{t('backup.lastRun')}:</span> {formatDate(configQuery.data?.last_run_at ?? null)}</span>
              <span><span className="font-medium text-foreground/80">{t('backup.nextRun')}:</span> {formatDate(configQuery.data?.next_run_at ?? null)}</span>
            </div>
            <Button size="sm" className="h-8 shrink-0" onClick={() => saveConfigMutation.mutate()} disabled={saveConfigMutation.isPending}>
              <Save className="h-3.5 w-3.5" />
              {t('common.save')}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card className="overflow-hidden">
        <CardHeader className="px-4 pb-2 pt-4">
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 items-start gap-3">
              <span className="mt-0.5 rounded-lg bg-primary/10 p-2 text-primary">
                <FileArchive className="h-4 w-4" />
              </span>
              <div className="min-w-0 space-y-1">
                <CardTitle className="text-base">{t('backup.storedTitle')}</CardTitle>
                <CardDescription className="text-xs leading-5">{t('backup.storedDescription')}</CardDescription>
              </div>
            </div>
            <Button variant="outline" size="sm" className="h-8 shrink-0" onClick={() => backupsQuery.refetch()} disabled={backupsQuery.isFetching}>
              <RefreshCw className="h-3.5 w-3.5" />
              {t('common.refresh')}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-2 px-4 pb-4">
          {backupsQuery.isLoading ? (
            <div className="flex min-h-20 items-center justify-center rounded-lg border border-dashed bg-muted/10 px-4 py-5 text-center text-sm text-muted-foreground">
              {t('common.loading')}
            </div>
          ) : backups.length === 0 ? (
            <div className="flex min-h-20 items-center justify-center rounded-lg border border-dashed bg-muted/10 px-4 py-5 text-center text-sm text-muted-foreground">
              {t('backup.emptyStored')}
            </div>
          ) : backups.map((item) => (
            <div key={item.id} className="flex flex-col gap-3 rounded-lg border bg-background/40 p-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0 space-y-1">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <p className="truncate text-sm font-medium">{item.filename}</p>
                  <Badge variant="secondary" className="rounded-full px-2 py-0 text-[11px]">{contentLabel(item.content, t)}</Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  {formatDate(item.created_at)} · {formatBytes(item.size_bytes)} · {countSummary(item.entity_counts)}
                </p>
              </div>
              <div className="flex shrink-0 flex-wrap gap-2">
                <Button variant="outline" size="sm" className="h-8" onClick={() => downloadStoredMutation.mutate(item.id)}>
                  <Download className="h-3.5 w-3.5" />
                  {t('backup.download')}
                </Button>
                <Button
                  size="sm"
                  className="h-8"
                  onClick={() => {
                    setStoredRestore(item)
                    setStoredContent(item.content)
                    setStoredMode('new_workspace')
                    setStoredConfirmation('')
                  }}
                >
                  <ArchiveRestore className="h-3.5 w-3.5" />
                  {t('backup.restore')}
                </Button>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <Dialog open={!!storedRestore} onOpenChange={(open) => !open && setStoredRestore(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('backup.restoreStoredTitle')}</DialogTitle>
            <DialogDescription>{storedRestore?.filename}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label>{t('backup.contentLabel')}</Label>
                <Select value={storedContent} onValueChange={(value) => setStoredContent(value as BackupContent)}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CONTENT_OPTIONS.map((content) => (
                      <SelectItem key={content} value={content}>{contentLabel(content, t)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label>{t('backup.restoreModeLabel')}</Label>
                <Select value={storedMode} onValueChange={(value) => setStoredMode(value as BackupRestoreMode)}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {RESTORE_MODES.map((mode) => (
                      <SelectItem key={mode} value={mode}>{modeLabel(mode, t)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            {storedMode === 'current_workspace' && (
              <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 space-y-2">
                <Label>{t('backup.confirmRestoreLabel')}</Label>
                <Input value={storedConfirmation} onChange={(e) => setStoredConfirmation(e.target.value)} placeholder="RESTORE" />
                <p className="text-xs text-muted-foreground">{t('backup.currentWorkspaceWarning')}</p>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setStoredRestore(null)}>{t('common.cancel')}</Button>
            <Button
              variant={storedMode === 'current_workspace' ? 'destructive' : 'default'}
              onClick={() => restoreStoredMutation.mutate()}
              disabled={restoreStoredMutation.isPending || (storedMode === 'current_workspace' && storedConfirmation !== 'RESTORE')}
            >
              {restoreStoredMutation.isPending ? t('backup.restoring') : t('backup.restore')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
