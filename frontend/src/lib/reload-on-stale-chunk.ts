const RELOAD_KEY = 'securo:stale-chunk-reload-at'
// A second failure inside this window means the reload didn't fix it (the
// server really is missing the file), so stop instead of reloading forever.
const RELOAD_WINDOW_MS = 10_000

/**
 * After a deploy, an open tab still references chunk files the server no
 * longer has, and the lazy page import fails. Vite reports that as
 * `vite:preloadError`; reloading picks up the new index.html and its chunks.
 *
 * The service worker is unregistered first: it would otherwise answer the
 * reload with its precached copy of the old index.html. The app registers
 * the new worker again on the next load.
 *
 * The event is deliberately not cancelled: if the reload is skipped, the
 * original error still surfaces.
 */
export function reloadOnStaleChunk(target: Window = window): void {
  target.addEventListener('vite:preloadError', () => {
    try {
      const last = Number(target.sessionStorage.getItem(RELOAD_KEY))
      if (Date.now() - last < RELOAD_WINDOW_MS) return
      target.sessionStorage.setItem(RELOAD_KEY, String(Date.now()))
    } catch {
      // sessionStorage unavailable: reloading without the guard could loop.
      return
    }
    const reload = () => target.location.reload()
    const workers = target.navigator.serviceWorker
    if (!workers) return reload()
    workers
      .getRegistrations()
      .then((registrations) => Promise.all(registrations.map((r) => r.unregister())))
      .catch(() => undefined)
      .then(reload)
  })
}
