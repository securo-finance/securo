import { useEffect } from 'react'

/**
 * Registers a global Cmd/Ctrl+K listener that toggles the command palette.
 * Lives in its own file so the palette module can stay a pure component
 * export (keeps React Fast Refresh happy).
 */
export function useCommandPaletteHotkey(setOpen: (open: boolean | ((prev: boolean) => boolean)) => void) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const isMod = e.metaKey || e.ctrlKey
      if (isMod && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault()
        setOpen((prev) => !prev)
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [setOpen])
}
