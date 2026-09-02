/**
 * Evaluates every component module in the tree.
 *
 * Most of these are pulled in by a lazily-loaded page, so a module that
 * throws on import does not fail the build and does not fail any test that
 * happens not to touch it. The blast radius is whoever opens the screen.
 * Importing all of them costs little and catches the whole class: a circular
 * import that resolves to undefined, a top-level call into a browser API, a
 * barrel that lost an export in a rename.
 *
 * The assertion is deliberately weak. This file is a smoke detector, not a
 * behaviour suite: the moment it starts asserting on rendering, it becomes
 * something contributors have to maintain rather than something that quietly
 * protects them.
 */
import { describe, expect, it } from 'vitest'

const modules = import.meta.glob('@/components/**/*.tsx', {
  eager: false,
}) as Record<string, () => Promise<Record<string, unknown>>>

const paths = Object.keys(modules)
  .filter((path) => !path.includes('.test.'))
  .sort()

// First evaluation of a module compiles its whole transitive graph through
// Vite, which is slow and varies with how much else is running in parallel.
// The default 5s is a budget for a *test*, not for a compile, and the page
// with the heaviest graph sits right on it, so adding one component
// anywhere in the tree can fail a page test that has nothing to do with it.
//
// A generous ceiling hides nothing: a module that fails to evaluate rejects
// immediately, and only a genuine hang reaches this number.
const EVALUATION_BUDGET_MS = 20_000

describe('component modules', () => {
  it('finds the component tree', () => {
    // If a refactor moves components elsewhere, this suite would silently
    // pass over an empty list.
    expect(paths.length).toBeGreaterThan(50)
  })

  for (const path of paths) {
    const name = path.replace(/^.*\/components\//, '')

    it(`${name} evaluates and exports a component`, async () => {
      const module = await modules[path]()

      // A plain component is a function; one wrapped in memo, forwardRef or
      // lazy is an object carrying $$typeof. Both are renderable, and this
      // has to accept either or it fails on the wrapped ones for no reason.
      const components = Object.values(module).filter(
        (value) =>
          typeof value === 'function' ||
          (typeof value === 'object' && value !== null && '$$typeof' in value),
      )
      expect(components.length).toBeGreaterThan(0)
    }, EVALUATION_BUDGET_MS)
  }
})
