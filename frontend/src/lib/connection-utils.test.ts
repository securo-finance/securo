import { describe, expect, it } from 'vitest'

import { connectionRequiresReconnect } from './connection-utils'

describe('connectionRequiresReconnect', () => {
  it('allows errored connections to retry with their saved credentials', () => {
    expect(connectionRequiresReconnect('error')).toBe(false)
  })

  it('requires reconnecting expired credentials', () => {
    expect(connectionRequiresReconnect('expired')).toBe(true)
  })
})
