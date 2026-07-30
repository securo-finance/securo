import * as React from "react"
import { Command as CommandPrimitive } from "cmdk"
import { SearchIcon } from "lucide-react"

import { cn } from "@/lib/utils"

// When this list is nested inside a modal Dialog (e.g. category picker
// inside the transaction dialog), Radix's body scroll lock (react-remove-scroll)
// registers a bubble-phase `wheel`/`touchmove` listener on `document` that
// preventDefault()s events whose target isn't inside the dialog's own DOM
// subtree — which is exactly this list's situation, since its Popover portals
// to document.body. That listener only fires once the event has bubbled all
// the way up, so React's own (passive, non-preventable) onWheel/onTouchMove
// props can't detect or work around it in time. The only reliable fix is to
// attach our own non-passive native listener directly on the list and drive
// scrollTop ourselves, unconditionally — this works the same whether or not
// a Dialog's scroll lock is active.
function useManualScroll<T extends HTMLElement>() {
  const ref = React.useRef<T>(null)

  React.useEffect(() => {
    const el = ref.current
    if (!el) return

    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const multiplier = e.deltaMode === 1 ? 20 : e.deltaMode === 2 ? el.clientHeight : 1
      el.scrollTop += e.deltaY * multiplier
    }

    let touchStart: { y: number; scrollTop: number } | null = null
    const onTouchStart = (e: TouchEvent) => {
      const touch = e.touches[0]
      touchStart = { y: touch.clientY, scrollTop: el.scrollTop }
    }
    const onTouchMove = (e: TouchEvent) => {
      if (!touchStart) return
      e.preventDefault()
      const touch = e.touches[0]
      el.scrollTop = touchStart.scrollTop + (touchStart.y - touch.clientY)
    }

    el.addEventListener("wheel", onWheel, { passive: false })
    el.addEventListener("touchstart", onTouchStart, { passive: true })
    el.addEventListener("touchmove", onTouchMove, { passive: false })
    return () => {
      el.removeEventListener("wheel", onWheel)
      el.removeEventListener("touchstart", onTouchStart)
      el.removeEventListener("touchmove", onTouchMove)
    }
  }, [])

  return ref
}

function Command({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive>) {
  return (
    <CommandPrimitive
      data-slot="command"
      className={cn(
        "bg-popover text-popover-foreground flex h-full w-full flex-col overflow-hidden rounded-md",
        className
      )}
      {...props}
    />
  )
}

function CommandInput({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Input>) {
  return (
    <div className="flex items-center border-b border-border px-3" data-slot="command-input-wrapper">
      <SearchIcon className="mr-2 h-4 w-4 shrink-0 opacity-50" />
      <CommandPrimitive.Input
        data-slot="command-input"
        className={cn(
          "placeholder:text-muted-foreground flex h-9 w-full rounded-md bg-transparent py-3 text-sm outline-hidden disabled:cursor-not-allowed disabled:opacity-50",
          className
        )}
        {...props}
      />
    </div>
  )
}

function CommandList({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.List>) {
  const ref = useManualScroll<HTMLDivElement>()
  return (
    <CommandPrimitive.List
      ref={ref}
      data-slot="command-list"
      className={cn("max-h-[300px] overflow-y-auto overflow-x-hidden overscroll-contain", className)}
      {...props}
    />
  )
}

function CommandEmpty({
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Empty>) {
  return (
    <CommandPrimitive.Empty
      data-slot="command-empty"
      className="py-6 text-center text-sm text-muted-foreground"
      {...props}
    />
  )
}

function CommandGroup({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Group>) {
  return (
    <CommandPrimitive.Group
      data-slot="command-group"
      className={cn(
        "text-foreground overflow-hidden p-1 [&_[data-value]]:text-foreground",
        className
      )}
      {...props}
    />
  )
}

function CommandItem({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Item>) {
  return (
    <CommandPrimitive.Item
      data-slot="command-item"
      className={cn(
        "relative flex cursor-default select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-hidden",
        "data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50",
        className
      )}
      {...props}
    />
  )
}

function CommandSeparator({
  className,
  ...props
}: React.ComponentProps<typeof CommandPrimitive.Separator>) {
  return (
    <CommandPrimitive.Separator
      data-slot="command-separator"
      className={cn("-mx-1 my-1 h-px bg-border", className)}
      {...props}
    />
  )
}

export {
  Command,
  CommandInput,
  CommandList,
  CommandEmpty,
  CommandGroup,
  CommandItem,
  CommandSeparator,
}
