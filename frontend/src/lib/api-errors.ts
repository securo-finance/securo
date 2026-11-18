import { translateApiDetail, translateApiMessage } from '@/lib/api-error-translations'

export function extractApiError(
  error: unknown,
  fallback = 'An unexpected error occurred',
): string {
  if (
    error &&
    typeof error === 'object' &&
    'response' in error &&
    error.response &&
    typeof error.response === 'object' &&
    'data' in error.response
  ) {
    const data = (error.response as { data: unknown }).data
    if (data && typeof data === 'object' && 'detail' in data) {
      const detail = (data as { detail: unknown }).detail
      if (typeof detail === 'string') return translateApiMessage(detail)
      // `{ code, message }`, the shape every hand-raised error in the API
      // uses. Without this branch a precise message ("Similarity runs from
      // 0 to 1") was thrown away and the caller showed its generic
      // fallback instead.
      if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
        // A coded detail ("import_undo_oversell") reads better translated with
        // its own values than the English sentence the API builds with them.
        const text = translateApiDetail(detail as Record<string, unknown>)
        if (text) return text
      }
      if (Array.isArray(detail)) {
        const message = detail.map((d: { msg?: string; loc?: string[] }) => {
          const field = d.loc?.slice(-1)[0] ?? ''
          return `${field}: ${d.msg ?? 'invalid'}`
        }).join(', ')
        if (message.trim()) return message
      }
    }
  }
  return fallback
}
