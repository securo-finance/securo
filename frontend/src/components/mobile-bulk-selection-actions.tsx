import type { Category, CategoryGroup } from '@/types'
import { ArrowLeftRight, Check, MoreHorizontal, SlidersHorizontal, Trash2, Users, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { CategorySelect } from '@/components/category-select'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

export type MobileBulkSelectionActionsProps = {
  selectedCount: number
  categoryValue: string
  categories: Category[]
  categoryGroups: CategoryGroup[]
  categoryPending: boolean
  groupPending: boolean
  tagInput: string
  addTagsPending: boolean
  removeTagsPending: boolean
  transferDisabled: boolean
  transferTitle: string
  onCategoryChange: (value: string) => void
  onOpenGroup: () => void
  onOpenTransfer: () => void
  onCreateRule?: () => void
  onBulkDelete: () => void
  onTagInputChange: (value: string) => void
  onAddTags: (tags: string[]) => void
  onRemoveTags: (tags: string[]) => void
  onClear: () => void
}

function parseBulkTags(value: string) {
  return value.trim().split(/[\s,]+/).filter(Boolean)
}

function BulkCategoryPicker(props: MobileBulkSelectionActionsProps) {
  const { t } = useTranslation()
  return (
    <div className="min-w-0 flex-1">
      <CategorySelect
        key={`mobile-${props.categoryValue}`}
        value={props.categoryValue}
        onChange={props.onCategoryChange}
        categories={props.categories}
        groups={props.categoryGroups}
        placeholder={t('transactions.selectCategory')}
        disabled={props.categoryPending}
        className="h-9 min-w-0 border-transparent bg-transparent px-2 shadow-none hover:bg-muted/60 focus:bg-muted/60 focus-visible:ring-0"
        contentProps={{ side: 'top', sideOffset: 8 }}
      />
    </div>
  )
}

function BulkTagButtons(props: MobileBulkSelectionActionsProps) {
  const { t } = useTranslation()
  const tags = parseBulkTags(props.tagInput)
  return (
    <div className="mt-1.5 grid grid-cols-2 gap-1.5">
      <Button size="sm" variant="ghost" disabled={!tags.length || props.addTagsPending} onClick={() => props.onAddTags(tags)}>
        <Check size={15} />{t('transactions.bulkAddTags', 'Add tags')}
      </Button>
      <Button size="sm" variant="ghost" disabled={!tags.length || props.removeTagsPending} onClick={() => props.onRemoveTags(tags)}>
        <X size={15} />{t('transactions.bulkRemoveTags', 'Remove tags')}
      </Button>
    </div>
  )
}

function BulkTagEditor(props: MobileBulkSelectionActionsProps) {
  const { t } = useTranslation()
  const submitTags = () => {
    const tags = parseBulkTags(props.tagInput)
    if (tags.length) props.onAddTags(tags)
  }
  return (
    <div className="mt-1 border-t border-border px-2 pt-2">
      <input
        type="text"
        value={props.tagInput}
        onChange={(event) => props.onTagInputChange(event.target.value)}
        placeholder={t('transactions.addTagsPlaceholder', '#tag…')}
        className="h-9 w-full rounded-md border border-input bg-transparent px-2.5 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:border-ring"
        onKeyDown={(event) => { event.stopPropagation(); if (event.key === 'Enter') { event.preventDefault(); submitTags() } }}
      />
      <BulkTagButtons {...props} />
    </div>
  )
}

function BulkActionItems(props: MobileBulkSelectionActionsProps) {
  const { t } = useTranslation()
  return (
    <>
      <DropdownMenuItem disabled={props.groupPending} onSelect={props.onOpenGroup}>
        <Users size={15} />{t('transactions.addToGroup')}
      </DropdownMenuItem>
      <DropdownMenuItem disabled={props.transferDisabled} title={props.transferTitle} onSelect={props.onOpenTransfer}>
        <ArrowLeftRight size={15} />{t('transactions.linkAsTransfer')}
      </DropdownMenuItem>
      {props.onCreateRule && <DropdownMenuItem onSelect={props.onCreateRule}><SlidersHorizontal size={15} />{t('transactions.createRule')}</DropdownMenuItem>}
      <DropdownMenuItem onSelect={props.onBulkDelete} className="text-destructive">
        <Trash2 size={15} />{t('transactions.bulkDelete')}
      </DropdownMenuItem>
      <BulkTagEditor {...props} />
    </>
  )
}

function BulkActionsMenu(props: MobileBulkSelectionActionsProps) {
  const { t } = useTranslation()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="h-9 shrink-0 px-2.5">
          <MoreHorizontal size={16} />{t('rules.actions')}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="end" sideOffset={8} className="w-64">
        <BulkActionItems {...props} />
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/**
 * Exposes bulk count, categorization, secondary actions, and close control on mobile.
 * @example `<MobileBulkSelectionActions selectedCount={2} {...bulkActionProps} />`
 */
export function MobileBulkSelectionActions(props: MobileBulkSelectionActionsProps) {
  const { t } = useTranslation()
  return (
    <div className="flex w-full items-center gap-1.5 sm:hidden">
      <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">{props.selectedCount}</span>
      <BulkCategoryPicker {...props} />
      <BulkActionsMenu {...props} />
      <button type="button" onClick={props.onClear} className="shrink-0 rounded-lg p-2 text-muted-foreground hover:bg-muted/60 hover:text-foreground" aria-label={t('common.close', 'Close')}>
        <X size={16} />
      </button>
    </div>
  )
}
