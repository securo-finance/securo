import { createContext, useContext } from 'react'
import type { LibraryKind } from '@/lib/library'

interface LibraryContextType {
  /** Opens the library on one kind of item. */
  openLibrary: (kind: LibraryKind) => void
}

export const LibraryContext = createContext<LibraryContextType | null>(null)

/** The library, or a no-op outside its provider (a page rendered on its own in a test). */
export function useLibrary(): LibraryContextType {
  return useContext(LibraryContext) ?? { openLibrary: () => {} }
}
