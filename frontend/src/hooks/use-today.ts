import { useState } from 'react'

/**
 * The current instant, captured once when the component mounts.
 *
 * Reading the clock during render makes a component impure: React may render
 * it more than once for the same props and expects the same output each time.
 * Capturing the instant in state keeps it stable for the lifetime of the
 * component — every render sees the same "today" — while still allowing a
 * fresh value on the next mount.
 */
export function useToday(): Date {
  const [today] = useState(() => new Date())
  return today
}