/**
 * Evaluates every page module.
 *
 * Every route in App.tsx is a `lazy(() => import(...))`. A page that fails to
 * evaluate — a bad export, a circular import, a top-level call into something
 * undefined — does not fail the build, because `tsc` checks modules in
 * isolation and Vite only resolves the chunk when a user navigates to it. The
 * first person to find out is whoever clicks the nav link.
 *
 * The list comes from a glob rather than being written out, so adding a page
 * covers it automatically. A hardcoded list plus an "is this list complete"
 * assertion would fail every PR that adds a page, which teaches contributors
 * that this file is an obstacle rather than a safety net.
 */
import { describe, expect, it } from 'vitest'

const modules = import.meta.glob('@/pages/**/*.tsx') as Record<
  string,
  () => Promise<Record<string, unknown>>
>

const paths = Object.keys(modules)
  .filter((path) => !path.includes('.test.'))
  .sort()

describe('page modules', () => {
  it('finds the page tree', () => {
    // If a refactor moves pages elsewhere, this suite would otherwise pass
    // silently over an empty list.
    expect(paths.length).toBeGreaterThan(20)
  })

  for (const path of paths) {
    const name = path.replace(/^.*\/pages\//, '').replace(/\.tsx$/, '')

    it(`${name} evaluates and default-exports a component`, async () => {
      const module = await modules[path]()

      // A plain page is a function; one wrapped in memo or forwardRef is an
      // object carrying $$typeof. Both are renderable.
      const component = module.default
      expect(component).toBeDefined()
      expect(
        typeof component === 'function' ||
          (typeof component === 'object' &&
            component !== null &&
            '$$typeof' in component),
      ).toBe(true)
    })
  }
})
