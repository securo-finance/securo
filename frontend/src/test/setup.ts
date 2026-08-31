/**
 * Global test setup: jest-dom matchers, DOM cleanup, and the browser APIs
 * jsdom does not implement.
 *
 * The polyfills below are not decoration. Radix primitives (dialog, select,
 * dropdown) and Recharts call into pointer capture, ResizeObserver and
 * scrollIntoView while rendering, and jsdom throws on all of them. Without
 * these, a component test fails on the environment rather than on the
 * component, which is the fastest way to make people stop writing tests.
 */
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// Testing Library only auto-cleans when vitest runs with `globals: true`, and
// this project imports `describe`/`it`/`expect` explicitly instead. Unmount by
// hand so one test's DOM never leaks into the next one's queries.
afterEach(() => {
  cleanup()
  localStorage.clear()
  sessionStorage.clear()
})

// next-themes reads this on mount. jsdom ships no implementation at all.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }),
})

class MockObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return []
  }
}

// Recharts' ResponsiveContainer and several Radix primitives construct one of
// these on mount.
globalThis.ResizeObserver = MockObserver as unknown as typeof ResizeObserver
globalThis.IntersectionObserver =
  MockObserver as unknown as typeof IntersectionObserver

// Radix Select and DropdownMenu move focus with these three. jsdom implements
// none of them, and the omission surfaces as "target.hasPointerCapture is not
// a function" deep inside the primitive.
Element.prototype.hasPointerCapture = vi.fn(() => false)
Element.prototype.setPointerCapture = vi.fn()
Element.prototype.releasePointerCapture = vi.fn()
Element.prototype.scrollIntoView = vi.fn()

// jsdom parses CSS animations but never runs them, so Radix's exit animations
// would wait forever on a promise that never settles.
Element.prototype.getAnimations = vi.fn(() => [])

window.scrollTo = vi.fn()

// Recharts measures text through a canvas context. jsdom has no canvas backend
// and logs a noisy "not implemented" error for every chart that renders.
HTMLCanvasElement.prototype.getContext = vi.fn(
  () =>
    ({
      measureText: () => ({ width: 0 }),
      fillText: () => {},
      clearRect: () => {},
    }) as unknown as CanvasRenderingContext2D,
) as unknown as HTMLCanvasElement['getContext']
