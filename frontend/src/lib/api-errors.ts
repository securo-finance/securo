const DEFAULT_ERROR = 'An unexpected error occurred'

export function extractApiError(
  error: unknown,
  fallback = DEFAULT_ERROR,
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

/**
 * Extract an API error when Axios was configured with responseType: "blob".
 * Axios keeps error responses as Blob objects in that mode, even when the
 * server returned FastAPI JSON. Local errors retain their own message.
 */
export async function extractApiErrorAsync(
  error: unknown,
  fallback = DEFAULT_ERROR,
): Promise<string> {
  const axiosError = error as {
    isAxiosError?: boolean
    response?: { data?: unknown }
  } | null
  const response = axiosError?.response
  // Axios network/transport failures are Error instances too, but their raw
  // messages are not user-facing and must not replace a translated fallback.
  const localFallback = !response && error instanceof Error && !axiosError?.isAxiosError
    ? error.message
    : fallback
  const data = response?.data

  if (!(data instanceof Blob)) return extractApiError(error, localFallback)

  try {
    const text = await data.text()
    const decoded: unknown = JSON.parse(text)
    return extractApiError({ response: { data: decoded } }, fallback)
  } catch {
    return localFallback
  }
}
