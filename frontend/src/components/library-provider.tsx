import { useCallback, useMemo, useState, type ReactNode } from 'react'

import { LibraryContext } from '@/contexts/library-context'
import { LibraryDialog } from '@/components/library-dialog'
import type { LibraryKind } from '@/lib/library'

export function LibraryProvider({ children }: { children: ReactNode }) {
  const [kind, setKind] = useState<LibraryKind | null>(null)
  const openLibrary = useCallback((next: LibraryKind) => setKind(next), [])
  const value = useMemo(() => ({ openLibrary }), [openLibrary])
  return (
    <LibraryContext.Provider value={value}>
      {children}
      {kind && <LibraryDialog kind={kind} onKindChange={setKind} onClose={() => setKind(null)} />}
    </LibraryContext.Provider>
  )
}
