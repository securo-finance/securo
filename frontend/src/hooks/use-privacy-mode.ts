import { useSyncExternalStore, useCallback } from 'react'

const STORAGE_KEY = 'privacyMode'
const MASK = '\u2022\u2022\u2022\u2022\u2022'

const listeners = new Set<() => void>()

function getSnapshot(): boolean {
  return localStorage.getItem(STORAGE_KEY) === 'true'
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

function notify() {
  listeners.forEach((cb) => cb())
}

export function usePrivacyMode() {
  const privacyMode = useSyncExternalStore(subscribe, getSnapshot, () => false)

  const togglePrivacyMode = useCallback(() => {
    const next = !getSnapshot()
    localStorage.setItem(STORAGE_KEY, String(next))
    notify()
  }, [])

  const mask = useCallback((value: string) => (privacyMode ? MASK : value), [privacyMode])

  return { privacyMode, togglePrivacyMode, mask, MASK } as const
}
