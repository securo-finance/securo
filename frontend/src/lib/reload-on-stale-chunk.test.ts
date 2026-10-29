/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { reloadOnStaleChunk } from '@/lib/reload-on-stale-chunk'

describe('reloadOnStaleChunk', () => {
  const reload = vi.fn()
  const unregister = vi.fn(() => Promise.resolve(true))
  let target: Window

  beforeEach(() => {
    reload.mockClear()
    unregister.mockClear()
    sessionStorage.clear()
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
    // A fresh EventTarget-backed window so listeners don't pile up on jsdom's.
    const events = new EventTarget()
    target = {
      addEventListener: events.addEventListener.bind(events),
      dispatchEvent: events.dispatchEvent.bind(events),
      sessionStorage,
      location: { reload },
      navigator: { serviceWorker: { getRegistrations: () => Promise.resolve([{ unregister }]) } },
    } as unknown as Window
    reloadOnStaleChunk(target)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  const fail = async () => {
    target.dispatchEvent(new Event('vite:preloadError', { cancelable: true }))
    await vi.advanceTimersByTimeAsync(0)
  }

  it('reloads once when a chunk fails to load', async () => {
    await fail()
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('unregisters the service worker before reloading', async () => {
    await fail()
    expect(unregister).toHaveBeenCalledTimes(1)
  })

  it('does not reload again right after a reload', async () => {
    await fail()
    vi.advanceTimersByTime(2_000)
    await fail()
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('reloads again once the guard window has passed', async () => {
    await fail()
    vi.advanceTimersByTime(11_000)
    await fail()
    expect(reload).toHaveBeenCalledTimes(2)
  })

  it('does not cancel the event, so the original error still surfaces', () => {
    const event = new Event('vite:preloadError', { cancelable: true })
    target.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)
  })
})
