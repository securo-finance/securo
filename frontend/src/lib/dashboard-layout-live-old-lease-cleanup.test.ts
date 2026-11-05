import { QueryClient } from '@tanstack/react-query'
import { expect, it } from 'vitest'

import {
  getDashboardAuthSnapshot, registerDashboardOwner, subscribeDashboardAuth,
  type DashboardAuthSnapshot, type DashboardRegistration, type RetirementReason,
} from '@/lib/dashboard-layout-auth-bridge'

function barrier(): Promise<void> {
  return new Promise<void>((resolve) => { queueMicrotask(resolve) })
}

it('keeps a same-generation identity successor unchanged through held old-lease cleanup', async () => {
  const client = new QueryClient()
  const A = Symbol('owner A')
  const B = Symbol('owner B')
  const aReasons: RetirementReason[] = []
  const bReasons: RetirementReason[] = []
  const retireA = (reason: RetirementReason): void => { aReasons.push(reason) }
  const retireB = (reason: RetirementReason): void => { bReasons.push(reason) }
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
  function expectNotifications(expected: DashboardAuthSnapshot[]): void {
    expect(observed).toEqual(expected)
    expected.forEach((snapshot, index) => { expect(observed[index]).toBe(snapshot) })
  }

  try {
    // 1: Fresh public snapshot and subscription.
    const r0 = getDashboardAuthSnapshot(client)
    expect(r0).toEqual({ generation: 0, revision: 0 })
    expect(getDashboardAuthSnapshot(client)).toBe(r0)
    expectNotifications([])
    expect([aReasons, bReasons]).toEqual([[], []])

    // 2: Two genuinely held A leases; no release before identity replacement.
    const a1 = requireLease(registerDashboardOwner(client, 0, A, retireA), 'a1')
    const r1 = getDashboardAuthSnapshot(client)
    const a2 = requireLease(registerDashboardOwner(client, 0, A, retireA), 'a2')
    expect(a2).not.toBe(a1)
    expect(r1).not.toBe(r0)
    function expectHeldA(expected: DashboardAuthSnapshot[]): void {
      expect(getDashboardAuthSnapshot(client)).toBe(r1)
      expect(r1).toEqual({ generation: 0, revision: 1 })
      expect([a1.isCurrent(), a2.isCurrent()]).toEqual([true, true])
      expect([aReasons, bReasons]).toEqual([[], []])
      expectNotifications(expected)
    }
    expectHeldA([])
    await barrier()
    expectHeldA([r1])

    // 3: Own-issued replacement; same-B reacquisition is the positive control.
    const b1 = requireLease(registerDashboardOwner(client, 0, B, retireB, a1), 'b1')
    const r3 = getDashboardAuthSnapshot(client)
    const b2 = requireLease(registerDashboardOwner(client, 0, B, retireB, b1), 'b2')
    expect(b2).not.toBe(b1)
    expect(r3).not.toBe(r1)
    function expectSuccessor(expected: DashboardAuthSnapshot[]): void {
      expect(getDashboardAuthSnapshot(client)).toBe(r3)
      expect(r3).toEqual({ generation: 0, revision: 3 })
      expect([a1.isCurrent(), a2.isCurrent(), b1.isCurrent(), b2.isCurrent()])
        .toEqual([false, false, true, true])
      expect([aReasons, bReasons]).toEqual([['identity'], []])
      expectNotifications(expected)
    }
    expectSuccessor([r1])

    // 4: Coalesced notification establishes invariant I.
    await barrier()
    function expectInvariantI(): void { expectSuccessor([r1, r3]) }
    expectInvariantI()

    // 5: Real first old-lease decrement, with B still held.
    a1.release()
    expectInvariantI()
    await barrier()
    expectInvariantI()

    // 6: Real final old-lease decrement, then inert old repeats.
    a2.release()
    expectInvariantI()
    await barrier()
    expectInvariantI()
    await barrier()
    expectInvariantI()
    a1.release()
    a2.release()
    expectInvariantI()
    await barrier()
    expectInvariantI()

    // 7: Released b1 remains entry-current while b2 still holds B.
    b1.release()
    expectInvariantI()
    await barrier()
    expectInvariantI()
    b1.release()
    expectInvariantI()
    await barrier()
    expectInvariantI()

    // 8: Only final B cleanup detaches; terminal repeats remain inert.
    b2.release()
    expectInvariantI()
    await barrier()
    await barrier()
    const r4 = getDashboardAuthSnapshot(client)
    expect(r4).not.toBe(r3)
    function expectTerminal(): void {
      expect(getDashboardAuthSnapshot(client)).toBe(r4)
      expect(r4).toEqual({ generation: 0, revision: 4 })
      expect([a1.isCurrent(), a2.isCurrent(), b1.isCurrent(), b2.isCurrent()])
        .toEqual([false, false, false, false])
      expect([aReasons, bReasons]).toEqual([['identity'], ['teardown']])
      expectNotifications([r1, r3, r4])
    }
    expectTerminal()
    a1.release()
    a2.release()
    b1.release()
    b2.release()
    expectTerminal()
    await barrier()
    await barrier()
    expectTerminal()
  } finally {
    acquired.forEach((registration) => { registration.release() })
    await barrier()
    await barrier()
    unsubscribe()
    client.clear()
  }
})
