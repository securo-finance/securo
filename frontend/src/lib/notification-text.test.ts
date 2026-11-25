import { expect, it } from 'vitest'

import { notificationTarget } from '@/lib/notification-text'
import type { AppNotification } from '@/types'

it('sends a kind this bundle does not know to the page with all of them', () => {
  expect(notificationTarget({ kind: 'from_a_newer_server', data: {} } as unknown as AppNotification)).toBe('/notifications')
})
