import { Fragment, type ReactElement } from 'react'
import { MoreHorizontal, type LucideIcon } from 'lucide-react'
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from '@/components/ui/context-menu'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

export type AssetRowAction = {
  key: string
  label: string
  icon: LucideIcon
  onSelect: () => void
  disabled?: boolean
  /** Shown in red, after a separator. */
  destructive?: boolean
  /** Why the action is disabled, shown under its label: a disabled item gets
   *  no hover, so a tooltip would never appear. */
  hint?: string
}

function ActionContent({ action }: { action: AssetRowAction }) {
  return (
    <>
      <action.icon className="me-2 h-4 w-4" />
      {action.hint ? (
        <span className="flex flex-col">
          <span>{action.label}</span>
          <span className="text-[11px] text-muted-foreground">{action.hint}</span>
        </span>
      ) : action.label}
    </>
  )
}

/** Separator before the first destructive action. */
function startsDestructive(actions: AssetRowAction[], i: number) {
  return Boolean(actions[i].destructive) && !actions.slice(0, i).some((a) => a.destructive)
}

/** "⋯" button that opens the row's actions. */
export function AssetRowMenuButton({ label, actions }: { label: string; actions: AssetRowAction[] }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={label}
          title={label}
          onClick={(e) => e.stopPropagation()}
          onContextMenu={(e) => e.stopPropagation()}
          className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
        >
          <MoreHorizontal size={14} />
        </button>
      </DropdownMenuTrigger>
      {/* Portals bubble through the React tree to the row's expand onClick
          and its right-click menu. */}
      <DropdownMenuContent
        align="end"
        onClick={(e) => e.stopPropagation()}
        onContextMenu={(e) => e.stopPropagation()}
      >
        {actions.map((a, i) => (
          <Fragment key={a.key}>
            {startsDestructive(actions, i) && <DropdownMenuSeparator />}
            <DropdownMenuItem
              variant={a.destructive ? 'destructive' : 'default'}
              disabled={a.disabled}
              onSelect={a.onSelect}
            >
              <ActionContent action={a} />
            </DropdownMenuItem>
          </Fragment>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** Opens the same actions on right-click over `children` (one element). */
export function AssetRowContextMenu({ actions, children }: { actions: AssetRowAction[]; children: ReactElement }) {
  if (actions.length === 0) return children
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
      <ContextMenuContent className="w-56">
        {actions.map((a, i) => (
          <Fragment key={a.key}>
            {startsDestructive(actions, i) && <ContextMenuSeparator />}
            <ContextMenuItem
              variant={a.destructive ? 'destructive' : 'default'}
              disabled={a.disabled}
              onSelect={a.onSelect}
            >
              <ActionContent action={a} />
            </ContextMenuItem>
          </Fragment>
        ))}
      </ContextMenuContent>
    </ContextMenu>
  )
}
