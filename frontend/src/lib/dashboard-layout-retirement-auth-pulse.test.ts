import { QueryClient } from '@tanstack/react-query'
import { expect, it } from 'vitest'

import {
  getDashboardAuthSnapshot, registerDashboardOwner, revokeDashboardAuthentication,
  subscribeDashboardAuth, type DashboardAuthSnapshot, type DashboardRegistration,
  type RetirementReason,
} from '@/lib/dashboard-layout-auth-bridge'

function barrier(): Promise<void> {
  return new Promise<void>((resolve) => { queueMicrotask(resolve) })
}

it('refuses current-generation reentry and aborts old-generation replacement after one retirement Auth pulse', async () => {
  const client = new QueryClient()
  const A = Symbol('owner A')
  const B = Symbol('owner B')
  const C = Symbol('owner C')
  const D = Symbol('owner D')
  const E = Symbol('owner E')
  const aReasons: RetirementReason[] = []
  const bReasons: RetirementReason[] = []
  const cReasons: RetirementReason[] = []
  const dReasons: RetirementReason[] = []
  const eReasons: RetirementReason[] = []
  const acquired: DashboardRegistration[] = []
  const observed: DashboardAuthSnapshot[] = []
  const errors: unknown[] = []
  let pulseCount = 0
  type PulseWitness = {
    r2: DashboardAuthSnapshot; r3: DashboardAuthSnapshot
    beforeD: DashboardAuthSnapshot; afterD: DashboardAuthSnapshot
    d: DashboardRegistration | null
    reasonsBefore: RetirementReason[][]; reasonsAfter: RetirementReason[][]
    noticesBefore: DashboardAuthSnapshot[]; noticesAfter: DashboardAuthSnapshot[]
  }
  const witnesses: PulseWitness[] = []
  const reasons = (): RetirementReason[][] =>
    [aReasons, bReasons, cReasons, dReasons, eReasons].map((values) => [...values])
  function track(registration: DashboardRegistration | null): DashboardRegistration | null {
    if (registration !== null) acquired.push(registration)
    return registration
  }
  const retireB = (reason: RetirementReason): void => { bReasons.push(reason) }
  const retireC = (reason: RetirementReason): void => { cReasons.push(reason) }
  const retireD = (reason: RetirementReason): void => { dReasons.push(reason) }
  const retireE = (reason: RetirementReason): void => { eReasons.push(reason) }
  const retireA = (reason: RetirementReason): void => {
    aReasons.push(reason)
    if (reason !== 'identity') return
    try {
      const r2 = getDashboardAuthSnapshot(client)
      pulseCount += 1
      revokeDashboardAuthentication(client)
      const r3 = getDashboardAuthSnapshot(client)
      const beforeD = getDashboardAuthSnapshot(client)
      const reasonsBefore = reasons()
      const noticesBefore = [...observed]
      const d = track(registerDashboardOwner(client, r3.generation, D, retireD))
      witnesses.push({
        r2, r3, beforeD, d, afterD: getDashboardAuthSnapshot(client),
        reasonsBefore, reasonsAfter: reasons(), noticesBefore, noticesAfter: [...observed],
      })
    } catch (error) {
      errors.push(error)
    }
  }
  const unsubscribe = subscribeDashboardAuth(client, () => {
    observed.push(getDashboardAuthSnapshot(client))
  })
  function requireLease(registration: DashboardRegistration | null): DashboardRegistration {
    expect(registration).not.toBeNull()
    if (registration === null) throw new Error('Expected a real held lease')
    return registration
  }
  function expectNotifications(
    expected: DashboardAuthSnapshot[], actual: DashboardAuthSnapshot[] = observed,
  ): void {
    expect(actual).toEqual(expected)
    expected.forEach((snapshot, index) => { expect(actual[index]).toBe(snapshot) })
  }
  function expectEvidence(teardown = false): void {
    expect(reasons()).toEqual([['identity'], [], teardown ? ['teardown'] : [], [], []])
    expect(pulseCount).toBe(1)
    expect(errors).toEqual([])
    expect(witnesses).toHaveLength(1)
  }

  try {
    // 1: A remains genuinely held through the issued identity-replacement attempt.
    const r0 = getDashboardAuthSnapshot(client)
    expect(r0).toEqual({ generation: 0, revision: 0 })
    expect(getDashboardAuthSnapshot(client)).toBe(r0)
    expect(reasons()).toEqual([[], [], [], [], []])
    expect(pulseCount).toBe(0)
    expect(errors).toEqual([])
    expectNotifications([])
    const a = requireLease(track(registerDashboardOwner(client, r0.generation, A, retireA)))
    const r1 = getDashboardAuthSnapshot(client)
    expect(r1).toEqual({ generation: 0, revision: 1 })
    expect(r1).not.toBe(r0)
    expect(a.isCurrent()).toBe(true)
    expectNotifications([])
    await barrier()
    expect(getDashboardAuthSnapshot(client)).toBe(r1)
    expect(a.isCurrent()).toBe(true)
    expect(reasons()).toEqual([[], [], [], [], []])
    expectNotifications([r1])

    // 2–3: Capture inside the callback; assert only after bridge exception isolation.
    const b = track(registerDashboardOwner(client, r0.generation, B, retireB, a))
    expect(b).toBeNull()
    expectEvidence()
    const witness = witnesses[0]
    if (witness === undefined) throw new Error('Missing retirement pulse witness')
    const { r2, r3 } = witness
    expect(r2).toEqual({ generation: 0, revision: 2 })
    expect(r2).not.toBe(r1)
    expect(r3).toEqual({ generation: 1, revision: 3 })
    expect(r3).not.toBe(r2)
    expect(witness.d).toBeNull()
    expect(witness.beforeD).toBe(r3)
    expect(witness.afterD).toBe(witness.beforeD)
    expect(witness.reasonsBefore).toEqual([['identity'], [], [], [], []])
    expect(witness.reasonsAfter).toEqual(witness.reasonsBefore)
    expectNotifications([r1], witness.noticesBefore)
    expectNotifications([r1], witness.noticesAfter)
    expect(getDashboardAuthSnapshot(client)).toBe(r3)
    expect(a.isCurrent()).toBe(false)
    expectNotifications([r1])
    await barrier()
    expect(getDashboardAuthSnapshot(client)).toBe(r3)
    expect(a.isCurrent()).toBe(false)
    expectEvidence()
    expectNotifications([r1, r3])

    // 4: Depth unwound: fresh G1 C succeeds, then stale G0 E cannot displace it.
    const c = requireLease(track(registerDashboardOwner(client, r3.generation, C, retireC)))
    const r4 = getDashboardAuthSnapshot(client)
    expect(r4).toEqual({ generation: 1, revision: 4 })
    expect(r4).not.toBe(r3)
    function expectC(expected: DashboardAuthSnapshot[]): void {
      expect(getDashboardAuthSnapshot(client)).toBe(r4)
      expect(r4).toEqual({ generation: 1, revision: 4 })
      expect(c.isCurrent()).toBe(true)
      expect(a.isCurrent()).toBe(false)
      expectEvidence()
      expectNotifications(expected)
    }
    expectC([r1, r3])
    const beforeE = getDashboardAuthSnapshot(client)
    const reasonsBeforeE = reasons()
    const noticesBeforeE = [...observed]
    const e = track(registerDashboardOwner(client, r0.generation, E, retireE))
    expect(e).toBeNull()
    expect(getDashboardAuthSnapshot(client)).toBe(beforeE)
    expect(reasons()).toEqual(reasonsBeforeE)
    expectNotifications(noticesBeforeE)
    expectC([r1, r3])
    await barrier()
    expectC([r1, r3, r4])

    // 5: Old cleanup is inert; C's sole lease tears down only across native barriers.
    a.release()
    expectC([r1, r3, r4])
    await barrier()
    expectC([r1, r3, r4])
    c.release()
    expectC([r1, r3, r4])
    await barrier()
    await barrier()
    const r5 = getDashboardAuthSnapshot(client)
    expect(r5).toEqual({ generation: 1, revision: 5 })
    expect(r5).not.toBe(r4)
    function expectTerminal(): void {
      expect(getDashboardAuthSnapshot(client)).toBe(r5)
      expect(r5).toEqual({ generation: 1, revision: 5 })
      acquired.forEach((registration) => { expect(registration.isCurrent()).toBe(false) })
      expectEvidence(true)
      expectNotifications([r1, r3, r4, r5])
    }
    expectTerminal()
    acquired.forEach((registration) => { registration.release() })
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
