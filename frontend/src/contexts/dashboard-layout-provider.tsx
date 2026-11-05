import {
  createContext, useCallback, useContext, useLayoutEffect, useRef, useState,
  useSyncExternalStore, type ReactNode,
} from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { DASHBOARD_WIDGETS } from '@/components/dashboard/widgets'
import { auth, dashboard, WORKSPACE_STORAGE_KEY } from '@/lib/api'
import {
  getDashboardAuthSnapshot, registerDashboardOwner, subscribeDashboardAuth,
  type DashboardOwnerToken, type DashboardRegistration, type RetirementReason,
} from '@/lib/dashboard-layout-auth-bridge'
import { createDashboardLayoutSession, type SessionStore } from '@/lib/dashboard-layout-session'
import type { User } from '@/types'

export type DashboardLayoutOwner = Readonly<{
  actorId: string
  authGeneration: number
  store: SessionStore
}>

const DashboardLayoutContext = createContext<DashboardLayoutOwner | null | undefined>(undefined)

interface CommittedIdentity {
  client: QueryClient
  actorId: string | null
  token: string | null
  loading: boolean
  user: User | null
  workspaceId: string
}
interface Descriptor {
  client: QueryClient
  token: string
  owner: DashboardLayoutOwner
  key: DashboardOwnerToken
  retire(reason: RetirementReason): void
  isActive(): boolean
  registration: DashboardRegistration | null
}
interface Exposure {
  client: QueryClient
  token: string
  owner: DashboardLayoutOwner
  registration: DashboardRegistration
}
interface Eligibility {
  client: QueryClient
  generation: number
  actorId: string | null
  token: string | null
  loading: boolean
  retried: boolean
}

export function DashboardLayoutProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient()
  const { user, token, isLoading } = useAuth()
  const { current } = useWorkspace()
  const actorId = user?.id ?? null
  const workspaceId = current?.id ?? ''
  const subscribe = useCallback((listener: () => void) => subscribeDashboardAuth(client, listener), [client])
  const getSnapshot = useCallback(() => getDashboardAuthSnapshot(client), [client])
  const { generation } = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  const committed = useRef<CommittedIdentity | null>(null)
  const descriptor = useRef<Descriptor | null>(null)
  const displaced = useRef<{ client: QueryClient; generation: number } | null>(null)
  const eligibility = useRef<Eligibility | null>(null)
  const [exposure, setExposure] = useState<Exposure | null>(null)
  const [retryTurn, setRetryTurn] = useState(0)

  // Before acquisition/construction: only committed identities feed private getters.
  // Workspace/preferences changes update these refs without renewing the owner.
  useLayoutEffect(() => {
    committed.current = { client, actorId, token, loading: isLoading, user, workspaceId }
  })

  useLayoutEffect(() => {
    let cancelled = false
    const previousEligibility = eligibility.current
    if (!previousEligibility || previousEligibility.client !== client ||
        previousEligibility.generation !== generation || previousEligibility.actorId !== actorId ||
        previousEligibility.token !== token || previousEligibility.loading !== isLoading) {
      eligibility.current = { client, generation, actorId, token, loading: isLoading, retried: false }
    }
    const turn = eligibility.current!
    if (displaced.current && (displaced.current.client !== client || displaced.current.generation !== generation)) {
      displaced.current = null
    }
    if (isLoading || !actorId || !token ||
        (displaced.current?.client === client && displaced.current.generation === generation)) {
      setExposure(null)
      return () => { cancelled = true }
    }

    const reconcile = () => {
      if (turn.retried) return
      queueMicrotask(() => {
        if (cancelled || eligibility.current !== turn) return
        // Re-read after the synchronous bridge mutation has unwound. The private
        // turn also renders the latest external snapshot if notification coalesced.
        getDashboardAuthSnapshot(client)
        turn.retried = true
        setRetryTurn((value) => value + 1)
      })
    }
    const prior = descriptor.current
    let candidate: Descriptor
    const reusable = prior && prior.client === client && prior.owner.authGeneration === generation &&
      prior.owner.actorId === actorId && prior.token === token && prior.isActive() &&
      prior.registration?.isCurrent()
    if (reusable) {
      candidate = prior
    } else {
      // Initialize the live holder BEFORE the factory captures getTokenIdentity.
      // Registration is intentionally not an initial-capture prerequisite.
      let active = true
      let registration: DashboardRegistration | null = null
      let store: SessionStore | null = null
      const key = Symbol('dashboard owner')
      const retire = (reason: RetirementReason) => {
        if (!active) return
        active = false
        if (reason === 'displaced') displaced.current = { client, generation }
        store?.revoke()
      }
      store = createDashboardLayoutSession({
        registry: DASHBOARD_WIDGETS,
        actorId,
        authGeneration: generation,
        authSeed: committed.current!.user!,
        getTokenIdentity: () => {
          const latest = committed.current
          if (!active || getDashboardAuthSnapshot(client).generation !== generation ||
              !latest || latest.client !== client || latest.loading || latest.actorId !== actorId ||
              latest.token !== token || (registration !== null && !registration.isCurrent())) return null
          const stored = localStorage.getItem('token')
          return stored === token ? stored : null
        },
        getWorkspaceId: () => {
          const latest = committed.current
          if (!active || !latest || latest.client !== client || !latest.workspaceId ||
              localStorage.getItem(WORKSPACE_STORAGE_KEY) !== latest.workspaceId) return ''
          return latest.workspaceId
        },
        api: { me: auth.me, saveLayout: dashboard.saveLayout },
      })
      const owner: DashboardLayoutOwner = Object.freeze({ actorId, authGeneration: generation, store })
      candidate = {
        client, token, owner, key, retire,
        isActive: () => active,
        get registration() { return registration },
        set registration(value) { registration = value },
      }
    }

    // A released lease is still an ENTRY-current replacement capability. Never
    // pass a stale capability: after authentication/teardown there is no own entry.
    const replacing = prior?.client === client && prior.owner.authGeneration === generation &&
      prior.registration?.isCurrent() ? prior.registration : undefined
    const lease = registerDashboardOwner(client, generation, candidate.key, candidate.retire, replacing)
    if (lease === null) {
      if (candidate !== prior) candidate.retire('teardown')
      setExposure(null)
      reconcile()
      return () => { cancelled = true }
    }
    candidate.registration = lease
    descriptor.current = candidate
    setExposure({ client, token, owner: candidate.owner, registration: lease })
    return () => {
      cancelled = true
      // StrictMode reacquires the same descriptor before guarded teardown expires.
      // This handle cannot revoke or release a replacement entry's lease.
      lease.release()
    }
  }, [client, generation, actorId, token, isLoading, retryTurn])

  // No ref reads during render. Revision-only notifications affect exposure,
  // never acquisition; identity comparisons use the real rendered Auth state.
  const owner = !isLoading && actorId !== null && token !== null && exposure !== null &&
    exposure.client === client && exposure.owner.actorId === actorId && exposure.token === token &&
    exposure.owner.authGeneration === generation && exposure.registration.isCurrent()
    ? exposure.owner : null
  return <DashboardLayoutContext.Provider value={owner}>{children}</DashboardLayoutContext.Provider>
}

export function useDashboardLayoutOwner(): DashboardLayoutOwner | null {
  const owner = useContext(DashboardLayoutContext)
  if (owner === undefined) throw new Error('useDashboardLayoutOwner must be used within a DashboardLayoutProvider')
  return owner
}
