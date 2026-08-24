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
