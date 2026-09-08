/**
 * Shared render helper.
 *
 * Almost every component here reads from at least one of three providers:
 * TanStack Query, the router, and i18n. Wiring them per test file drifts, so
 * they live in one place and `renderWithProviders` is what tests call.
 *
 * i18n is the real instance with the real English bundle, not a stub that
 * echoes keys back. That is deliberate: a test asserting on "Save changes"
 * fails when someone deletes the key, which is exactly the regression the
 * locale files keep having.
 */
import type { ReactElement, ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { I18nextProvider } from 'react-i18next'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { render, type RenderOptions, type RenderResult } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import i18n from '@/lib/i18n'

/**
 * Retries and caching are useful in the app and only add flake in a test: a
 * failed query would be retried past the assertion, and a cached one would
 * leak into the next test. Both are off.
 */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  })
}

export interface ProviderOptions extends Omit<RenderOptions, 'wrapper'> {
  /** Initial URL for the memory router. */
  route?: string
  /**
   * Route pattern to mount the element under, for components that read params
   * with `useParams`. Pass `path="/accounts/:id"` with `route="/accounts/42"`.
   */
  path?: string
  queryClient?: QueryClient
}

export interface ProviderRenderResult extends RenderResult {
  queryClient: QueryClient
  /** Pre-bound user-event instance, so tests do not each call `setup()`. */
  user: ReturnType<typeof userEvent.setup>
}

export function renderWithProviders(
  ui: ReactElement,
  options: ProviderOptions = {},
): ProviderRenderResult {
  const {
    route = '/',
    path,
    queryClient = createTestQueryClient(),
    ...renderOptions
  } = options

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <I18nextProvider i18n={i18n}>
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[route]}>
            {path ? (
              <Routes>
                <Route path={path} element={children} />
              </Routes>
            ) : (
              children
            )}
          </MemoryRouter>
        </QueryClientProvider>
      </I18nextProvider>
    )
  }

  return {
    ...render(ui, { wrapper: Wrapper, ...renderOptions }),
    queryClient,
    user: userEvent.setup(),
  }
}

/** Translate through the real bundle, so tests assert on the shipped copy. */
export function t(key: string, options?: Record<string, unknown>): string {
  return i18n.t(key, options) as string
}

export { i18n }
