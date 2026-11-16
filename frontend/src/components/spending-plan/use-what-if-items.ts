import { useCallback, useState } from 'react'
import type { SimulatedPurchase } from '@/types'

/** A hypothetical purchase the plan can count or not, kept on this device. */
export interface WhatIfItem {
  id: string
  label: string
  amount: number
  first_date: string
  installments: number
  enabled: boolean
}

const STORAGE_PREFIX = 'spendingPlan.whatIf'

function storageKey(workspaceId: string | undefined) {
  return workspaceId ? `${STORAGE_PREFIX}.${workspaceId}` : STORAGE_PREFIX
}

function isItem(value: unknown): value is WhatIfItem {
  if (typeof value !== 'object' || value === null) return false
  const item = value as Record<string, unknown>
  return (
    typeof item.id === 'string' &&
    typeof item.label === 'string' &&
    typeof item.amount === 'number' &&
    item.amount > 0 &&
    typeof item.first_date === 'string' &&
    /^\d{4}-\d{2}-\d{2}$/.test(item.first_date) &&
    Number.isInteger(item.installments) &&
    (item.installments as number) >= 1 &&
    (item.installments as number) <= 120 &&
    typeof item.enabled === 'boolean'
  )
}

function load(key: string): WhatIfItem[] {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(key) ?? '[]')
    return Array.isArray(parsed) ? parsed.filter(isItem) : []
  } catch {
    return []
  }
}

/** The what-if items of a workspace, persisted to localStorage on every change. */
export function useWhatIfItems(workspaceId: string | undefined) {
  const key = storageKey(workspaceId)
  const [state, setState] = useState(() => ({ key, items: load(key) }))
  if (state.key !== key) setState({ key, items: load(key) })
  const setItems = useCallback(
    (next: WhatIfItem[]) => {
      window.localStorage.setItem(key, JSON.stringify(next))
      setState({ key, items: next })
    },
    [key],
  )
  return [state.items, setItems] as const
}

export function asPurchase(item: WhatIfItem): SimulatedPurchase {
  return {
    description: item.label,
    amount: item.amount,
    installments: item.installments,
    first_date: item.first_date,
  }
}
