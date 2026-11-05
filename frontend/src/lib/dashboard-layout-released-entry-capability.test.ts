import { QueryClient } from '@tanstack/react-query'
import { expect, it } from 'vitest'

import {
  getDashboardAuthSnapshot, registerDashboardOwner, subscribeDashboardAuth,
  type DashboardAuthSnapshot, type DashboardRegistration, type RetirementReason,
} from '@/lib/dashboard-layout-auth-bridge'

function notifications(): Promise<void> {
  return new Promise<void>((resolve) => { queueMicrotask(resolve) })
}

it('retains released entry capability until guarded detach without letting stale cleanup mutate its successor', async () => {
  const client = new QueryClient()
  const A = Symbol('owner A')
  const B = Symbol('owner B')
  const C = Symbol('owner C')
  const aReasons: RetirementReason[] = []
  const bReasons: RetirementReason[] = []
  const cReasons: RetirementReason[] = []
  const retireA = (reason: RetirementReason): void => { aReasons.push(reason) }
  const retireB = (reason: RetirementReason): void => { bReasons.push(reason) }
  const retireC = (reason: RetirementReason): void => { cReasons.push(reason) }
  const acquired: DashboardRegistration[] = []
  const observed: DashboardAuthSnapshot[] = []
  const unsubscribe = subscribeDashboardAuth(client, () => {
    observed.push(getDashboardAuthSnapshot(client))
  })
  function requireLease(
    registration: DashboardRegistration | null, label: string,
  ): DashboardRegistration {
    if (registration !== null) acquired.push(registration)
    expect(registration, `${label} must acquire a real lease`).not.toBeNull()
    if (registration === null) throw new Error(`${label} registration refused`)
    return registration
  }

  try {
    // Phase 1: actual subscription, initial installation and asynchronous r1.
    const r0 = getDashboardAuthSnapshot(client)
    expect(r0).toEqual({ generation: 0, revision: 0 })
    expect(getDashboardAuthSnapshot(client)).toBe(r0)
    expect(observed).toEqual([])
    const a1 = requireLease(registerDashboardOwner(client, 0, A, retireA), 'a1')
    const r1 = getDashboardAuthSnapshot(client)
    expect(r1).not.toBe(r0)
    expect(r1).toEqual({ generation: 0, revision: 1 })
    expect(getDashboardAuthSnapshot(client)).toBe(r1)
    expect(a1.isCurrent()).toBe(true)
    expect([aReasons, bReasons, cReasons]).toEqual([[], [], []])
    expect(observed).toEqual([])
    await notifications()
    expect(observed).toEqual([r1])
    expect(observed[0]).toBe(r1)
    expect(getDashboardAuthSnapshot(client)).toBe(r1)

    // Phase 2: release queues a real ticket; reacquisition cancels it.
    a1.release()
    a1.release()
    expect(a1.isCurrent()).toBe(true)
    expect(getDashboardAuthSnapshot(client)).toBe(r1)
    expect([aReasons, bReasons, cReasons]).toEqual([[], [], []])
    expect(observed).toEqual([r1])
    const a2 = requireLease(registerDashboardOwner(client, 0, A, retireA), 'a2')
    expect(a2).not.toBe(a1)
    expect([a1.isCurrent(), a2.isCurrent()]).toEqual([true, true])
    expect(getDashboardAuthSnapshot(client)).toBe(r1)
    await notifications()
    expect(getDashboardAuthSnapshot(client)).toBe(r1)
    expect([a1.isCurrent(), a2.isCurrent()]).toEqual([true, true])
    expect([aReasons, bReasons, cReasons]).toEqual([[], [], []])
    expect(observed).toEqual([r1])
    expect(observed[0]).toBe(r1)

    // Phase 3: released a1 identifies its entry BEFORE old queued teardown.
    a2.release()
    expect([a1.isCurrent(), a2.isCurrent()]).toEqual([true, true])
    expect(getDashboardAuthSnapshot(client)).toBe(r1)
    expect([aReasons, bReasons, cReasons]).toEqual([[], [], []])
    const b1 = requireLease(registerDashboardOwner(client, 0, B, retireB, a1), 'b1')
    const r3 = getDashboardAuthSnapshot(client)
    expect(r3).not.toBe(r1)
    expect(r3).toEqual({ generation: 0, revision: 3 })
    expect(getDashboardAuthSnapshot(client)).toBe(r3)
    expect([a1.isCurrent(), a2.isCurrent(), b1.isCurrent()]).toEqual([false, false, true])
    expect([aReasons, bReasons, cReasons]).toEqual([['identity'], [], []])
    expect(observed).toEqual([r1])

    // Phase 4: issued stale capability refuses BEFORE any successor mutation.
    const denied = registerDashboardOwner(client, 0, C, retireC, a1)
    if (denied !== null) acquired.push(denied)
    expect(denied).toBeNull()
    expect(getDashboardAuthSnapshot(client)).toBe(r3)
    expect(b1.isCurrent()).toBe(true)
    expect([aReasons, bReasons, cReasons]).toEqual([['identity'], [], []])
    expect(observed).toEqual([r1])
    // These are already-released old handles, not unreleased-old-lease coverage.
    a1.release()
    a1.release()
    a2.release()
    a2.release()
    expect(getDashboardAuthSnapshot(client)).toBe(r3)
    expect([a1.isCurrent(), a2.isCurrent(), b1.isCurrent()]).toEqual([false, false, true])
    expect([aReasons, bReasons, cReasons]).toEqual([['identity'], [], []])
    expect(observed).toEqual([r1])
    const b2 = requireLease(registerDashboardOwner(client, 0, B, retireB, b1), 'b2')
    expect(b2).not.toBe(b1)
    expect([b1.isCurrent(), b2.isCurrent()]).toEqual([true, true])
    expect(getDashboardAuthSnapshot(client)).toBe(r3)
    expect([aReasons, bReasons, cReasons]).toEqual([['identity'], [], []])
    expect(observed).toEqual([r1])
    b1.release()
    b1.release()
    expect([b1.isCurrent(), b2.isCurrent()]).toEqual([true, true])
    expect(getDashboardAuthSnapshot(client)).toBe(r3)
    expect([aReasons, bReasons, cReasons]).toEqual([['identity'], [], []])
    expect(observed).toEqual([r1])

    // Phase 5: cross OLD teardown and the coalesced identity notification.
    await notifications()
    expect(getDashboardAuthSnapshot(client)).toBe(r3)
    expect(r3).toEqual({ generation: 0, revision: 3 })
    expect([a1.isCurrent(), a2.isCurrent(), b1.isCurrent(), b2.isCurrent()])
      .toEqual([false, false, true, true])
    expect([aReasons, bReasons, cReasons]).toEqual([['identity'], [], []])
    expect(observed).toEqual([r1, r3])
    expect(observed[0]).toBe(r1)
    expect(observed[1]).toBe(r3)

    // Phase 6: the final live lease really detaches B and publishes r4.
    b2.release()
    expect([b1.isCurrent(), b2.isCurrent()]).toEqual([true, true])
    expect(getDashboardAuthSnapshot(client)).toBe(r3)
    expect([aReasons, bReasons, cReasons]).toEqual([['identity'], [], []])
    expect(observed).toEqual([r1, r3])
    await notifications()
    await notifications()
    const r4 = getDashboardAuthSnapshot(client)
    expect(r4).not.toBe(r3)
    expect(r4).toEqual({ generation: 0, revision: 4 })
    expect(getDashboardAuthSnapshot(client)).toBe(r4)
    expect([a1.isCurrent(), a2.isCurrent(), b1.isCurrent(), b2.isCurrent()])
      .toEqual([false, false, false, false])
    expect([aReasons, bReasons, cReasons]).toEqual([['identity'], ['teardown'], []])
    expect(observed).toEqual([r1, r3, r4])
    expect(observed[0]).toBe(r1)
    expect(observed[1]).toBe(r3)
    expect(observed[2]).toBe(r4)
    a1.release()
    a2.release()
    b1.release()
    b2.release()
    expect(getDashboardAuthSnapshot(client)).toBe(r4)
    expect([a1.isCurrent(), a2.isCurrent(), b1.isCurrent(), b2.isCurrent()])
      .toEqual([false, false, false, false])
    expect([aReasons, bReasons, cReasons]).toEqual([['identity'], ['teardown'], []])
    expect(observed).toEqual([r1, r3, r4])
  } finally {
    // Includes an unexpected C acquisition so a failed oracle still cleans up.
    acquired.forEach((registration) => { registration.release() })
    await notifications()
    await notifications()
    unsubscribe()
    client.clear()
  }
})
