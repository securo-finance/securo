import { useRef, useState, useEffect, useCallback, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useDateLocale } from '@/hooks/use-display-locale'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { transactions as transactionsApi, settings as settingsApi } from '@/lib/api'
import { toast } from 'sonner'
import { GlobalWorkerOptions, getDocument } from 'pdfjs-dist'
import { Paperclip, Upload, Download, Trash2, X, Loader2, FileText, Eye, Plus, Pencil, Check } from 'lucide-react'
import { Skeleton } from '@/components/ui/skeleton'
import type { Attachment } from '@/types'

GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.mjs', import.meta.url).href

export interface AttachmentPreview {
  attachmentId: string
  url: string
  contentType: string
  filename: string
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function getFileExtension(filename: string): string {
  return filename.includes('.') ? filename.split('.').pop()!.toLowerCase() : ''
}

function isImageType(contentType: string): boolean {
  return contentType.startsWith('image/')
}

function sanitizeFilename(filename: string): string {
  const dotIdx = filename.lastIndexOf('.')
  let name = dotIdx > 0 ? filename.slice(0, dotIdx) : filename
  const ext = dotIdx > 0 ? filename.slice(dotIdx + 1).toLowerCase().replace(/[^a-z0-9]/g, '') : ''
  name = name.replace(/[^a-zA-Z0-9._-]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '')
  if (!name) name = 'file'
  return ext ? `${name}.${ext}` : name
}

interface OptimisticAttachment {
  id: string
  filename: string
  content_type: string
  size: number
  isUploading: true
}

export function TransactionAttachments({
  transactionId,
  onPreviewChange,
  activePreviewId,
}: {
  transactionId: string
  onPreviewChange?: (preview: AttachmentPreview | null) => void
  activePreviewId?: string | null
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const lastClickedRef = useRef<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const renameInputRef = useRef<HTMLInputElement>(null)
  const [thumbnails, setThumbnails] = useState<Record<string, string>>({})
  const [optimisticFiles, setOptimisticFiles] = useState<OptimisticAttachment[]>([])
  const dateLocale = useDateLocale()
  const queryKey = ['attachments', transactionId]

  const { data: attachmentSettings } = useQuery({
    queryKey: ['settings', 'attachments'],
    queryFn: () => settingsApi.attachments(),
    staleTime: 5 * 60 * 1000,
  })

  const allowedExtensions = useMemo(() => attachmentSettings?.allowed_extensions ?? ['jpg', 'jpeg', 'png', 'webp', 'gif', 'heic', 'pdf'], [attachmentSettings?.allowed_extensions])
  const maxFileSize = (attachmentSettings?.max_file_size_mb ?? 10) * 1024 * 1024
  const maxAttachments = attachmentSettings?.max_attachments_per_transaction ?? 10

  const { data: attachments, isLoading } = useQuery({
    queryKey,
    queryFn: () => transactionsApi.attachments.list(transactionId),
  })

  // Load thumbnails for image and PDF attachments
  useEffect(() => {
    if (!attachments) return
    let cancelled = false
    const urls: string[] = []

    const renderPdfThumbnail = async (blobUrl: string): Promise<string> => {
      const pdf = await getDocument(blobUrl).promise
      const page = await pdf.getPage(1)
      const scale = 200 / page.getViewport({ scale: 1 }).width
      const viewport = page.getViewport({ scale })
      const canvas = document.createElement('canvas')
      canvas.width = viewport.width
      canvas.height = viewport.height
      await page.render({ canvas, canvasContext: canvas.getContext('2d')!, viewport }).promise
      const dataUrl = canvas.toDataURL('image/png')
      pdf.destroy()
      return dataUrl
    }

    const loadThumbnails = async () => {
      const newThumbnails: Record<string, string> = {}
      for (const att of attachments) {
        if (thumbnails[att.id]) continue
        const isImage = isImageType(att.content_type)
        const isPdf = att.content_type === 'application/pdf'
        if (!isImage && !isPdf) continue

        try {
          const blobUrl = await transactionsApi.attachments.downloadUrl(transactionId, att.id)
          if (cancelled) {
            URL.revokeObjectURL(blobUrl)
            return
          }

          if (isImage) {
            newThumbnails[att.id] = blobUrl
            urls.push(blobUrl)
          } else {
            // PDF: render first page to canvas, then revoke the blob
            const dataUrl = await renderPdfThumbnail(blobUrl)
            URL.revokeObjectURL(blobUrl)
            if (cancelled) return
            newThumbnails[att.id] = dataUrl
          }
        } catch {
          // skip failed thumbnail
        }
      }
      if (!cancelled && Object.keys(newThumbnails).length > 0) {
        setThumbnails(prev => ({ ...prev, ...newThumbnails }))
      }
    }

    loadThumbnails()
    return () => {
      cancelled = true
      urls.forEach(URL.revokeObjectURL)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attachments?.map(a => a.id).join(',')])

  // Cleanup thumbnails on unmount
  useEffect(() => {
    return () => {
      Object.values(thumbnails).forEach(URL.revokeObjectURL)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const uploadMutation = useMutation({
    mutationFn: ({ file }: { file: File; optimisticId: string }) =>
      transactionsApi.attachments.upload(transactionId, file),
    onMutate: ({ file, optimisticId }) => {
      const optimistic: OptimisticAttachment = {
        id: optimisticId,
        filename: sanitizeFilename(file.name),
        content_type: file.type || 'application/octet-stream',
        size: file.size,
        isUploading: true,
      }
      // Generate a local thumbnail for images
      if (isImageType(file.type)) {
        const url = URL.createObjectURL(file)
        setThumbnails(prev => ({ ...prev, [optimisticId]: url }))
      }
      setOptimisticFiles(prev => [...prev, optimistic])
    },
    onSuccess: (_data, { optimisticId }) => {
      setOptimisticFiles(prev => prev.filter(f => f.id !== optimisticId))
      if (thumbnails[optimisticId]) {
        URL.revokeObjectURL(thumbnails[optimisticId])
        setThumbnails(prev => {
          const next = { ...prev }
          delete next[optimisticId]
          return next
        })
      }
      queryClient.invalidateQueries({ queryKey })
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      toast.success(t('transactions.attachmentUploaded'))
    },
    onError: (error: unknown, { optimisticId }) => {
      setOptimisticFiles(prev => prev.filter(f => f.id !== optimisticId))
      if (thumbnails[optimisticId]) {
        URL.revokeObjectURL(thumbnails[optimisticId])
        setThumbnails(prev => {
          const next = { ...prev }
          delete next[optimisticId]
          return next
        })
      }
      const msg = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg || t('common.error'))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (attachmentId: string) => transactionsApi.attachments.delete(transactionId, attachmentId),
    onSuccess: (_data, attachmentId) => {
      if (activePreviewId === attachmentId) {
        onPreviewChange?.(null)
      }
      if (thumbnails[attachmentId]) {
        URL.revokeObjectURL(thumbnails[attachmentId])
        setThumbnails(prev => {
          const next = { ...prev }
          delete next[attachmentId]
          return next
        })
      }
      queryClient.invalidateQueries({ queryKey })
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      toast.success(t('transactions.attachmentDeleted'))
      setDeletingId(null)
      setConfirmDeleteId(null)
    },
    onError: () => {
      toast.error(t('common.error'))
      setDeletingId(null)
    },
  })

  const renameMutation = useMutation({
    mutationFn: ({ attachmentId, filename }: { attachmentId: string; filename: string }) =>
      transactionsApi.attachments.rename(transactionId, attachmentId, filename),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey })
      setRenamingId(null)
      toast.success(t('transactions.attachmentRenamed'))
    },
    onError: () => {
      toast.error(t('common.error'))
    },
  })

  const startRename = (att: Attachment) => {
    // Pre-fill with name without extension
    const dotIdx = att.filename.lastIndexOf('.')
    const nameWithoutExt = dotIdx > 0 ? att.filename.slice(0, dotIdx) : att.filename
    setRenameValue(nameWithoutExt)
    setRenamingId(att.id)
    setConfirmDeleteId(null)
    setTimeout(() => renameInputRef.current?.select(), 0)
  }

  const submitRename = (attachmentId: string) => {
    const trimmed = renameValue.trim()
    if (!trimmed) {
      setRenamingId(null)
      return
    }
    renameMutation.mutate({ attachmentId, filename: trimmed })
  }

  const validateAndUpload = useCallback((files: FileList | File[]) => {
    const currentCount = (attachments?.length ?? 0) + optimisticFiles.length
    let uploaded = 0
    for (const file of Array.from(files)) {
      if (currentCount + uploaded >= maxAttachments) {
        toast.error(t('transactions.attachmentMaxReached'))
        break
      }
      const ext = getFileExtension(file.name)
      if (!allowedExtensions.includes(ext)) {
        toast.error(t('transactions.attachmentTypeNotAllowed'))
        continue
      }
      if (file.size > maxFileSize) {
        toast.error(t('transactions.attachmentTooLarge'))
        continue
      }
      const optimisticId = `optimistic-${crypto.randomUUID()}`
      uploadMutation.mutate({ file, optimisticId })
      uploaded++
    }
  }, [uploadMutation, t, attachments?.length, optimisticFiles.length, maxAttachments, allowedExtensions, maxFileSize])

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    if (e.dataTransfer.files?.length) {
      validateAndUpload(e.dataTransfer.files)
    }
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) {
      validateAndUpload(e.target.files)
      e.target.value = ''
    }
  }

  const handlePreview = async (attachment: Attachment) => {
    if (activePreviewId === attachment.id) {
      onPreviewChange?.(null)
      return
    }

    lastClickedRef.current = attachment.id
    try {
      const url = await transactionsApi.attachments.downloadUrl(transactionId, attachment.id)
      if (lastClickedRef.current !== attachment.id) {
        URL.revokeObjectURL(url)
        return
      }
      onPreviewChange?.({
        attachmentId: attachment.id,
        url,
        contentType: attachment.content_type,
        filename: attachment.filename,
      })
    } catch {
      toast.error(t('common.error'))
    }
  }

  const handleDownload = async (attachment: Attachment) => {
    try {
      const url = await transactionsApi.attachments.downloadUrl(transactionId, attachment.id)
      const a = document.createElement('a')
      a.href = url
      a.download = attachment.filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch {
      toast.error(t('common.error'))
    }
  }

  const handleDelete = (attachmentId: string) => {
    setDeletingId(attachmentId)
    deleteMutation.mutate(attachmentId)
  }

  const allItems = [
    ...(attachments ?? []).map(att => ({ ...att, isUploading: false as const })),
    ...optimisticFiles,
  ]
  const hasAttachments = allItems.length > 0
  const atMaxAttachments = allItems.length >= maxAttachments

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Paperclip size={14} />
        {t('transactions.attachments')}
        {hasAttachments && (
          <span className="text-xs text-muted-foreground font-normal">({attachments!.length})</span>
        )}
      </div>

      {/* Attachment grid */}
      {isLoading ? (
        <div className="grid grid-cols-3 gap-2">
          <Skeleton className="aspect-square w-full rounded-xl" />
          <Skeleton className="aspect-square w-full rounded-xl" />
        </div>
      ) : hasAttachments ? (
        <>
        <div className="grid grid-cols-3 gap-2">
          {allItems.map((att) => {
            const isUploading = att.isUploading
            const isActive = !isUploading && activePreviewId === att.id
            const isPdf = att.content_type === 'application/pdf'
            const ext = getFileExtension(att.filename).toUpperCase()
            const isConfirming = confirmDeleteId === att.id

            return (
              <div
                key={att.id}
                className={`group relative rounded-xl overflow-hidden transition-all duration-200 ${
                  isUploading
                    ? 'ring-1 ring-border opacity-60'
                    : isActive
                      ? 'ring-2 ring-primary shadow-md shadow-primary/10 cursor-pointer'
                      : 'ring-1 ring-border hover:ring-border/80 hover:shadow-md hover:shadow-black/5 cursor-pointer'
                }`}
                onClick={() => !isUploading && !isConfirming && handlePreview(att as Attachment)}
              >
                {/* Thumbnail */}
                <div className="aspect-square bg-muted/50 flex items-center justify-center overflow-hidden relative">
                  {thumbnails[att.id] ? (
                    <img
                      src={thumbnails[att.id]}
                      alt={att.filename}
                      className={`w-full h-full object-cover transition-transform duration-300 ${isUploading ? '' : 'group-hover:scale-[1.03]'}`}
                    />
                  ) : (
                    <div className="flex flex-col items-center gap-2">
                      <div className={`w-12 h-14 rounded-lg flex items-center justify-center ${
                        isPdf ? 'bg-red-500/10' : 'bg-muted'
                      }`}>
                        <FileText size={24} className={isPdf ? 'text-red-500' : 'text-muted-foreground'} />
                      </div>
                      <span className="text-[10px] font-semibold tracking-widest text-muted-foreground/70 uppercase">
                        {ext || 'FILE'}
                      </span>
                    </div>
                  )}
                  {isUploading && (
                    <div className="absolute inset-0 flex items-center justify-center bg-background/40">
                      <Loader2 size={20} className="animate-spin text-primary" />
                    </div>
                  )}
                </div>

                {/* Hover action bar — floats over bottom of thumbnail */}
                {!isUploading && (
                <div className={`absolute left-0 right-0 bottom-[44px] flex items-center justify-center gap-1 px-2 py-1.5 transition-all duration-200 ${
                  isConfirming
                    ? 'opacity-100 translate-y-0'
                    : 'opacity-0 translate-y-1 group-hover:opacity-100 group-hover:translate-y-0'
                }`}>
                  <div className="flex items-center gap-1 bg-background/90 dark:bg-card/90 backdrop-blur-sm rounded-lg ring-1 ring-border/50 shadow-lg shadow-black/10 px-1 py-0.5">
                    {isConfirming ? (
                      <>
                        <button
                          type="button"
                          className="flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-medium bg-destructive text-destructive-foreground hover:bg-destructive/90 cursor-pointer transition-colors"
                          onClick={(e) => { e.stopPropagation(); handleDelete(att.id) }}
                          disabled={deletingId === att.id}
                        >
                          {deletingId === att.id ? <Loader2 size={12} className="animate-spin" /> : t('common.delete')}
                        </button>
                        <button
                          type="button"
                          className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent cursor-pointer transition-colors"
                          onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(null) }}
                        >
                          <X size={13} />
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          type="button"
                          className={`p-1.5 rounded-md cursor-pointer transition-colors ${
                            isActive
                              ? 'text-primary bg-primary/10'
                              : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                          }`}
                          onClick={(e) => { e.stopPropagation(); handlePreview(att as Attachment) }}
                          title="Preview"
                        >
                          <Eye size={14} />
                        </button>
                        <button
                          type="button"
                          className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent cursor-pointer transition-colors"
                          onClick={(e) => { e.stopPropagation(); startRename(att as Attachment) }}
                          title={t('transactions.attachmentRename')}
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          type="button"
                          className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent cursor-pointer transition-colors"
                          onClick={(e) => { e.stopPropagation(); handleDownload(att as Attachment) }}
                          title="Download"
                        >
                          <Download size={14} />
                        </button>
                        <button
                          type="button"
                          className="p-1.5 rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 cursor-pointer transition-colors"
                          onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(att.id) }}
                          title={t('common.delete')}
                        >
                          <Trash2 size={14} />
                        </button>
                      </>
                    )}
                  </div>
                </div>
                )}

                {/* Info bar — always visible, clean */}
                <div className="px-3 py-2.5 bg-card">
                  {!isUploading && renamingId === att.id ? (
                    <div
                      className="flex items-center gap-1"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <input
                        ref={renameInputRef}
                        type="text"
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onBlur={() => submitRename(att.id)}
                        onKeyDown={(e) => {
                          e.stopPropagation()
                          if (e.key === 'Enter') { e.preventDefault(); submitRename(att.id) }
                          if (e.key === 'Escape') setRenamingId(null)
                        }}
                        className="flex-1 min-w-0 text-[12px] font-medium bg-transparent border-b border-primary outline-none leading-tight py-0.5"
                        autoFocus
                      />
                      <button
                        type="button"
                        className="p-0.5 text-primary hover:text-primary/80 cursor-pointer"
                        onClick={(e) => { e.stopPropagation(); submitRename(att.id) }}
                      >
                        {renameMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                      </button>
                    </div>
                  ) : (
                    <p
                      className={`text-[12px] font-medium truncate leading-tight ${!isUploading ? 'cursor-text hover:text-primary/80' : ''}`}
                      title={att.filename}
                      onClick={(e) => { if (!isUploading) { e.stopPropagation(); startRename(att as Attachment) } }}
                    >
                      {att.filename}
                    </p>
                  )}
                  <p className="text-[10px] text-muted-foreground mt-1 leading-tight">
                    {isUploading
                      ? t('transactions.attachmentsUploading')
                      : `${formatFileSize(att.size)} · ${new Date((att as Attachment).created_at).toLocaleDateString(dateLocale, { month: 'short', day: 'numeric' })}`
                    }
                  </p>
                </div>
              </div>
            )
          })}

        </div>

        {/* Add more button */}
        {!atMaxAttachments && (
        <button
          type="button"
          className={`w-full mt-2 rounded-lg border-2 border-dashed py-3 flex items-center justify-center gap-2 cursor-pointer transition-all duration-200 ${
            dragOver
              ? 'border-primary bg-primary/5'
              : 'border-border hover:border-muted-foreground/40 hover:bg-muted/30'
          }`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <Plus size={14} className="text-muted-foreground" />
          <span className="text-xs text-muted-foreground">{t('transactions.attachmentsUpload')}</span>
        </button>
        )}
        </>
      ) : (
        /* Empty state with drop zone */
        <div
          className={`rounded-xl border-2 border-dashed py-6 px-4 text-center transition-all cursor-pointer ${
            dragOver ? 'border-primary bg-primary/5' : 'border-border hover:border-muted-foreground/40'
          }`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <div className="flex flex-col items-center gap-2">
            <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center">
              <Upload size={14} className="text-muted-foreground" />
            </div>
            <span className="text-xs text-muted-foreground">{t('transactions.attachmentsUpload')}</span>
          </div>
        </div>
      )}

      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={allowedExtensions.map(ext => `.${ext}`).join(',')}
        onChange={handleFileChange}
        className="hidden"
      />
    </div>
  )
}
