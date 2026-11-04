/**
 * Helpers for side panels that close on an outside click or on Escape.
 *
 * Dialogs, popovers, selects and menus render in portals attached to
 * `document.body`, so a click inside them is "outside" any panel in DOM terms.
 * A panel that opens a dialog (for example to edit one of its rows) must not
 * treat interaction with that dialog as a reason to close.
 */

const OVERLAY_LAYER_SELECTOR = [
  '[role="dialog"]',
  '[role="alertdialog"]',
  '[data-slot="dialog-overlay"]',
  '[data-radix-popper-content-wrapper]',
  '[role="listbox"]',
  '[role="menu"]',
  '[data-sonner-toaster]',
].join(', ')

const OPEN_DIALOG_SELECTOR =
  '[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"]'

/** True when the event target sits inside a portaled dialog, popover, menu or toast. */
export function isInsideOverlayLayer(target: EventTarget | null): boolean {
  if (!target || !(target instanceof Node)) return false
  const element = target instanceof Element ? target : target.parentElement
  return !!element?.closest(OVERLAY_LAYER_SELECTOR)
}

/** True while a dialog is open on top of the page. */
export function hasOpenDialog(doc: Document = document): boolean {
  return doc.querySelector(OPEN_DIALOG_SELECTOR) !== null
}
