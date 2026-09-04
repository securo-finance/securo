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
      if (typeof detail === 'string') return detail
      // `{ code, message }`, the shape every hand-raised error in the API
      // uses. Without this branch a precise message ("Similarity runs from
      // 0 to 1") was thrown away and the caller showed its generic
      // fallback instead.
      if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
        const message = (detail as { message?: unknown }).message
        if (typeof message === 'string' && message.trim()) return message
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
