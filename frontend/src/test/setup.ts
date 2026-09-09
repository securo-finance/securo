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
import { createElement, type ComponentProps } from 'react'

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    // Use the same test viewport before the first ResizeObserver notification.
    // Keep the actual container/charts so their SVG rendering is exercised.
    ResponsiveContainer(props: ComponentProps<typeof actual.ResponsiveContainer>) {
      return createElement(actual.ResponsiveContainer, {
        initialDimension: { width: 800, height: 300 },
        ...props,
      })
    },
  }
})

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

// jsdom has no layout. Give chart containers a deterministic viewport while
// retaining normal jsdom measurements for every other element.
const getBoundingClientRect = HTMLElement.prototype.getBoundingClientRect
HTMLElement.prototype.getBoundingClientRect = function () {
  return this.classList.contains('recharts-responsive-container')
    ? DOMRect.fromRect({ width: 800, height: 300 })
    : getBoundingClientRect.call(this)
}

class MockResizeObserver {
  private callback: ResizeObserverCallback

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
  }

  observe(target: Element) {
    const contentRect = target.getBoundingClientRect()
    const size = [{ inlineSize: contentRect.width, blockSize: contentRect.height }]
    this.callback([{
      target, contentRect, borderBoxSize: size, contentBoxSize: size,
      devicePixelContentBoxSize: size,
    }], this)
  }

  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = MockResizeObserver
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

// pdfjs-dist touches these at module scope, so importing the attachment
// viewer throws before any component renders. That import is transitive from
// the transactions, dashboard and account-detail pages, which would put three
// of the busiest screens out of reach of any test. The stubs only need to
// exist for the module to evaluate; nothing here renders a real PDF.
class MockDOMMatrix {
  a = 1
  b = 0
  c = 0
  d = 1
  e = 0
  f = 0
  multiply() {
    return this
  }
  translate() {
    return this
  }
  scale() {
    return this
  }
  inverse() {
    return this
  }
}

globalThis.DOMMatrix ??= MockDOMMatrix as unknown as typeof DOMMatrix
globalThis.Path2D ??= class {} as unknown as typeof Path2D
globalThis.ImageData ??= class {
  data = new Uint8ClampedArray()
  width = 0
  height = 0
} as unknown as typeof ImageData

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
