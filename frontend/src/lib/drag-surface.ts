import { PointerSensor } from '@dnd-kit/core'

// Controls that act on pointer-down (a menu opens before any drag could
// start) or that take text input. Buttons that act on click stay draggable:
// the sensor's travel threshold keeps a click a click.
const CONTROLS = 'a[href], input, select, textarea, [aria-haspopup]'

function isOverText(element: Element, x: number, y: number): boolean {
  for (const node of element.childNodes) {
    if (node.nodeType !== Node.TEXT_NODE || !node.textContent?.trim()) continue
    const range = document.createRange()
    range.selectNodeContents(node)
    for (const rect of range.getClientRects()) {
      if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) return true
    }
  }
  return false
}

/**
 * Whether a press at (x, y) on `target` may start dragging the row or header
 * it is in: anywhere but on text, which stays selectable, or on a control.
 */
export function startsDragAt(target: EventTarget | null, x: number, y: number): boolean {
  if (!(target instanceof Element)) return false
  if (target.closest(CONTROLS)) return false
  return !isOverText(target, x, y)
}

/** A pointer sensor for a whole row or header, which leaves its text and its menus alone. */
export class SurfacePointerSensor extends PointerSensor {
  static activators = [
    {
      eventName: 'onPointerDown' as const,
      handler: (...args: Parameters<(typeof PointerSensor.activators)[number]['handler']>) => {
        const { nativeEvent } = args[0]
        return (
          startsDragAt(nativeEvent.target, nativeEvent.clientX, nativeEvent.clientY) &&
          PointerSensor.activators[0].handler(...args)
        )
      },
    },
  ]
}
