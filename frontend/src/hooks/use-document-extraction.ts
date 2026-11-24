import { useCallback, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { documentAi } from '@/lib/api'
import { useFeatureFlags } from '@/hooks/use-feature-flags'
import type { DocumentExtraction, DocumentKind } from '@/types'

/** Extensions document reading accepts, for a file input's `accept`. */
export const DOCUMENT_EXTENSIONS = ['.pdf', '.png', '.jpg', '.jpeg', '.webp']

export function isDocumentFile(name: string | null | undefined): boolean {
  const lower = (name ?? '').toLowerCase()
  return DOCUMENT_EXTENSIONS.some(ext => lower.endsWith(ext))
}

export function isImageFile(name: string | null | undefined): boolean {
  return isDocumentFile(name) && !(name ?? '').toLowerCase().endsWith('.pdf')
}

const KNOWN_ERRORS = new Set([
  'not_configured', 'vision_required', 'unsupported_file', 'file_too_large', 'encrypted_pdf',
  'unreadable_file', 'empty_document', 'provider_error', 'provider_auth', 'invalid_response', 'timeout',
  'already_confirmed', 'attachment_refused', 'internal_error', 'queue_unavailable',
])

/** A translated message for a refusal (`{code, message}`) or a failed read. */
export function documentErrorMessage(t: TFunction, code: string | null | undefined, fallback?: string | null): string {
  if (code && KNOWN_ERRORS.has(code)) return t(`documentAi.errors.${code}`)
  return fallback || t('documentAi.errors.generic')
}

export function errorFromResponse(t: TFunction, error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (detail && typeof detail === 'object' && 'code' in detail) {
    const { code, message } = detail as { code?: string; message?: string }
    return documentErrorMessage(t, code, message)
  }
  if (typeof detail === 'string') return detail
  return t('documentAi.errors.generic')
}

export function useDocumentAiStatus() {
  const { documentAiEnabled } = useFeatureFlags()
  const query = useQuery({
    queryKey: ['document-ai', 'status'],
    queryFn: () => documentAi.status(),
    enabled: documentAiEnabled,
    staleTime: 60_000,
  })
  // Available with the agents module; on once the workspace picked a connection.
  return { available: documentAiEnabled, enabled: documentAiEnabled && !!query.data?.enabled, status: query.data, isLoading: query.isLoading }
}

/**
 * Upload a document and follow its read until it is done or failed.
 * @example const doc = useDocumentExtraction('receipt'); doc.start(file)
 */
export function useDocumentExtraction(kind: DocumentKind) {
  const queryClient = useQueryClient()
  const [extractionId, setExtractionId] = useState<string | null>(null)

  const upload = useMutation({
    mutationFn: (file: File) => documentAi.upload(file, kind),
    onSuccess: (data) => {
      queryClient.setQueryData(['document-ai', 'extraction', data.id], data)
      setExtractionId(data.id)
    },
  })

  const query = useQuery({
    queryKey: ['document-ai', 'extraction', extractionId],
    queryFn: () => documentAi.get(extractionId as string),
    enabled: !!extractionId,
    refetchInterval: (q) => {
      const status = (q.state.data as DocumentExtraction | undefined)?.status
      return status === 'pending' || status === 'processing' ? 2000 : false
    },
  })

  const retry = useMutation({
    mutationFn: () => documentAi.retry(extractionId as string),
    onSuccess: (data) => queryClient.setQueryData(['document-ai', 'extraction', data.id], data),
  })

  const reset = useCallback(() => {
    setExtractionId(null)
    upload.reset()
  }, [upload])

  const extraction = query.data
  const reading = upload.isPending || extraction?.status === 'pending' || extraction?.status === 'processing'
  return {
    start: upload.mutate,
    uploadError: upload.error,
    extraction,
    reading,
    retry: retry.mutate,
    retrying: retry.isPending,
    reset,
  }
}
