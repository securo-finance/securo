import { useCallback, useState, useSyncExternalStore } from 'react'

const STORAGE_KEY = 'securo.transactions.groupSimilar'
const listeners = new Set<() => void>()
let fallbackValue = true

function getSnapshot(): boolean {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return stored === null ? true : stored === 'true'
  } catch {
    return fallbackValue
  }
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  const handleStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY) listener()
  }
  window.addEventListener('storage', handleStorage)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('storage', handleStorage)
  }
}

function notify() {
  listeners.forEach(listener => listener())
}

export function useSimilarTransactionGrouping() {
  const enabled = useSyncExternalStore(subscribe, getSnapshot, () => true)
  const setEnabled = useCallback((next: boolean) => {
    fallbackValue = next
    try {
      localStorage.setItem(STORAGE_KEY, String(next))
    } catch {
      // The in-memory fallback keeps the preference usable for this visit.
    }
    notify()
  }, [])
  return { enabled, setEnabled } as const
}

export function useSimilarGroupExpansion() {
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set())
  const toggle = useCallback((key: string) => {
    setExpandedKeys(previous => {
      const next = new Set(previous)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])
  const expand = useCallback((key: string) => {
    setExpandedKeys(previous => {
      if (previous.has(key)) return previous
      const next = new Set(previous)
      next.add(key)
      return next
    })
  }, [])
  const clear = useCallback(() => setExpandedKeys(new Set()), [])
  return { expandedKeys, toggle, expand, clear } as const
}
