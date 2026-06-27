export function calculateRangeSelection<T extends { id: string }>(
  currentSelected: Set<string>,
  lastSelectedId: string | null,
  clickedId: string,
  items: T[],
  isShiftKey: boolean,
  isSelectable: (item: T) => boolean = () => true
): Set<string> {
  const next = new Set(currentSelected)

  if (isShiftKey && lastSelectedId) {
    const lastIndex = items.findIndex(item => item.id === lastSelectedId)
    const currentIndex = items.findIndex(item => item.id === clickedId)

    if (lastIndex !== -1 && currentIndex !== -1) {
      const start = Math.min(lastIndex, currentIndex)
      const end = Math.max(lastIndex, currentIndex)
      const shouldSelect = !currentSelected.has(clickedId)

      for (let i = start; i <= end; i++) {
        const item = items[i]
        if (!isSelectable(item)) continue
        if (shouldSelect) {
          next.add(item.id)
        } else {
          next.delete(item.id)
        }
      }
      return next
    }
  }

  // Standard toggle
  if (next.has(clickedId)) {
    next.delete(clickedId)
  } else {
    next.add(clickedId)
  }
  return next
}
