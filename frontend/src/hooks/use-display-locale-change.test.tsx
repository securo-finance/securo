import { useState } from 'react'
import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useDisplayLocaleChange } from './use-display-locale-change'
import { convertAmountInput } from '@/lib/format'

function useSeededAmount(locale: string) {
  const [amount, setAmount] = useState('1234.56')
  useDisplayLocaleChange(locale, (prev, next) => setAmount((v) => convertAmountInput(v, prev, next)))
  return amount
}

describe('useDisplayLocaleChange', () => {
  it('re-renders seeded text when the locale resolves, keeping its value', () => {
    const { result, rerender } = renderHook(({ locale }) => useSeededAmount(locale), {
      initialProps: { locale: 'en-US' },
    })
    expect(result.current).toBe('1234.56')
    rerender({ locale: 'de-DE' })
    expect(result.current).toBe('1234,56')
    rerender({ locale: 'de-DE' })
    expect(result.current).toBe('1234,56')
  })
})
