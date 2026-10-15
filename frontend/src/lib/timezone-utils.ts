/** "GMT-3", "GMT+5:30" or "GMT" for a zone right now, or null when unknown. */
export function timezoneOffsetLabel(name: string, at = new Date()): string | null {
  try {
    const part = new Intl.DateTimeFormat('en-US', { timeZone: name, timeZoneName: 'shortOffset' })
      .formatToParts(at)
      .find((p) => p.type === 'timeZoneName')
    // Runtimes disagree on the zero offset ("GMT" vs "GMT+0"); show one form.
    return part ? part.value.replace(/^GMT[+-]0$/, 'GMT') : null
  } catch {
    return null
  }
}

/** "America/Sao_Paulo" reads as "Sao Paulo" under the "America" heading. */
export function timezoneCityLabel(name: string): string {
  const slash = name.indexOf('/')
  return (slash < 0 ? name : name.slice(slash + 1)).replace(/_/g, ' ')
}

/** The part before the first slash, or an empty string for UTC-style names. */
export function timezoneRegion(name: string): string {
  const slash = name.indexOf('/')
  return slash < 0 ? '' : name.slice(0, slash)
}
