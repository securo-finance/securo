import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import axios from 'axios'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { categories as categoriesApi, categoryGroups as groupsApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import type { Category, CategoryGroup } from '@/types'
import { Pencil, Trash2, Plus, ChevronDown, ChevronRight, ChevronsUpDown, Eye, EyeOff } from 'lucide-react'
import { PageHeader } from '@/components/page-header'
import { invalidateCategoryQueries } from '@/lib/invalidate-queries'
import { CategoryIcon } from '@/components/category-icon'
import { IconPicker } from '@/components/icon-picker'
import { useWorkspace } from '@/contexts/workspace-context'

function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      {children}
    </div>
  )
}
function SectionHeader({ title, titleExtra, action }: { title: string; titleExtra?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-2">
      <div className="flex items-center gap-3">
        <p className="text-sm font-semibold text-foreground">{title}</p>
        {titleExtra}
      </div>
      {action}
    </div>
  )
}

export default function CategoriesPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { canWrite } = useWorkspace()
  const [catDialogOpen, setCatDialogOpen] = useState(false)
  const [editingCat, setEditingCat] = useState<Category | null>(null)
  const [formIcon, setFormIcon] = useState('circle-help')
  const [formColor, setFormColor] = useState('#6366f1')
  const [formTreatAsTransfer, setFormTreatAsTransfer] = useState(false)
  const [formIgnoreTransfer, setFormIgnoreTransfer] = useState(false)
  const [groupDialogOpen, setGroupDialogOpen] = useState(false)
  const [editingGroup, setEditingGroup] = useState<CategoryGroup | null>(null)
  const [groupFormIcon, setGroupFormIcon] = useState('folder')
  const [groupFormColor, setGroupFormColor] = useState('#6B7280')
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())
  const [deletingCategory, setDeletingCategory] = useState<Category | null>(null)
  const [deletingGroup, setDeletingGroup] = useState<CategoryGroup | null>(null)
  const [hidingCategory, setHidingCategory] = useState<
    { category: Category; rules: { id: string; name: string }[] } | null
  >(null)

  const { data: groups } = useQuery({
    queryKey: ['category-groups', 'management'],
    queryFn: groupsApi.listIncludingHidden,
  })

  const { data: categoriesList } = useQuery({
    queryKey: ['categories', 'management'],
    queryFn: categoriesApi.listIncludingHidden,
  })

  const invalidateAll = () => {
    invalidateCategoryQueries(queryClient)
  }

  const createCatMutation = useMutation({
    mutationFn: (cat: Partial<Category>) => categoriesApi.create(cat),
    onSuccess: () => { invalidateAll(); setCatDialogOpen(false); toast.success(t('categories.created')) },
  })
  const updateCatMutation = useMutation({
    mutationFn: ({ id, ...data }: Partial<Category> & { id: string }) => categoriesApi.update(id, data),
    onSuccess: () => { invalidateAll(); setCatDialogOpen(false); setEditingCat(null); toast.success(t('categories.updated')) },
  })

  // Hiding is its own mutation: it can also retire the rules that assign the
  // category, and it must refresh the rule list rather than the category form.
  const hideCatMutation = useMutation({
    mutationFn: ({ id, deactivateRules }: { id: string; deactivateRules: boolean }) =>
      categoriesApi.update(id, { is_hidden: true }, { deactivateRules }),
    onSuccess: (_data, variables) => {
      invalidateAll()
      if (variables.deactivateRules) queryClient.invalidateQueries({ queryKey: ['rules'] })
      setHidingCategory(null)
      toast.success(t('categories.updated'))
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })

  // Rules that still file transactions into a category outlive hiding it, so
  // check for them first and only interrupt the user when there are any.
  async function handleToggleHidden(cat: Category) {
    if (cat.is_hidden) {
      updateCatMutation.mutate({ id: cat.id, is_hidden: false })
      return
    }
    try {
      const usage = await queryClient.fetchQuery({
        queryKey: ['category-rule-usage', cat.id],
        queryFn: () => categoriesApi.ruleUsage(cat.id),
        staleTime: 0,
      })
      if (usage.rules.length === 0) {
        hideCatMutation.mutate({ id: cat.id, deactivateRules: false })
        return
      }
      setHidingCategory({ category: cat, rules: usage.rules })
    } catch (err) {
      toast.error(extractApiError(err, t('common.error')))
    }
  }
  const deleteCatMutation = useMutation({
    mutationFn: (id: string) => categoriesApi.delete(id),
    onSuccess: () => { invalidateAll(); setDeletingCategory(null); toast.success(t('categories.deleted')) },
    onError: (err: unknown) => {
      // The API answers 409 with an English sentence; show the translated one instead.
      if (axios.isAxiosError(err) && err.response?.status === 409) {
        toast.error(t('categories.deleteInUse'))
        return
      }
      toast.error(extractApiError(err, t('common.error')))
    },
  })

  const createGroupMutation = useMutation({
    mutationFn: (g: Partial<CategoryGroup>) => groupsApi.create(g),
    onSuccess: () => { invalidateAll(); setGroupDialogOpen(false); toast.success(t('groups.created')) },
  })
  const updateGroupMutation = useMutation({
    mutationFn: ({ id, ...data }: Partial<CategoryGroup> & { id: string }) => groupsApi.update(id, data),
    onSuccess: () => { invalidateAll(); setGroupDialogOpen(false); setEditingGroup(null); toast.success(t('groups.updated')) },
    onError: () => { toast.error(t('common.error')) },
  })
  const deleteGroupMutation = useMutation({
    mutationFn: (id: string) => groupsApi.delete(id),
    onSuccess: () => { invalidateAll(); setDeletingGroup(null); toast.success(t('groups.deleted')) },
    onError: (err: unknown) => {
      toast.error(extractApiError(err, t('common.error')))
    },
  })

  const toggleCollapse = (groupId: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(groupId)) next.delete(groupId)
      else next.add(groupId)
      return next
    })
  }

  const ungrouped = categoriesList?.filter((c) => !c.group_id) ?? []

  const openCatDialog = (cat: Category | null) => {
    setEditingCat(cat)
    setFormIcon(cat?.icon ?? 'circle-help')
    setFormColor(cat?.color ?? '#6366f1')
    setFormTreatAsTransfer(cat?.treat_as_transfer ?? false)
    setFormIgnoreTransfer(cat?.is_ignored ?? false)
    setCatDialogOpen(true)
  }

  const openGroupDialog = (group: CategoryGroup | null) => {
    setEditingGroup(group)
    setGroupFormIcon(group?.icon ?? 'folder')
    setGroupFormColor(group?.color ?? '#6B7280')
    setGroupDialogOpen(true)
  }

  const renderHiddenBadge = (label: string) => (
    <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-muted text-muted-foreground border border-border shrink-0">
      {label}
    </span>
  )

  const renderCategoryItem = (cat: Category) => (
    <div key={cat.id} className={`flex items-center gap-3 px-4 sm:px-5 pl-6 sm:pl-12 py-2.5 border-b border-border last:border-0 hover:bg-muted transition-colors ${cat.is_hidden ? 'opacity-60' : ''}`}>
      <CategoryIcon icon={cat.icon} color={cat.color} size="md" />
      <div className="flex-1 min-w-0 flex items-center gap-2">
        <span className="text-sm font-medium text-foreground truncate">{cat.name}</span>
        {cat.is_hidden && renderHiddenBadge(t('categories.hiddenBadge'))}
        {cat.treat_as_transfer && (
          <span
            className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-muted text-muted-foreground border border-border shrink-0"
            title={t('categories.treatAsTransferDesc')}
          >
            {t('categories.treatAsTransferBadge')}
          </span>
        )}
        {cat.is_ignored && (
          <span
            className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-muted text-muted-foreground border border-border shrink-0"
            title={t('categories.ignoreTransferDesc')}
          >
            {t('categories.ignoreTransferBadge')}
          </span>
        )}
      </div>
      <div className="hidden sm:flex items-center gap-2 shrink-0">
        <span className="inline-block w-3.5 h-3.5 rounded-full border border-black/10" style={{ backgroundColor: cat.color }} />
        <span className="text-xs text-muted-foreground font-mono">{cat.color}</span>
      </div>
      {canWrite && (
        <div className="flex items-center gap-1 shrink-0 ml-2">
          <button
            className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
            onClick={() => openCatDialog(cat)}
            title={t('common.edit')}
          >
            <Pencil size={13} />
          </button>
          {cat.is_system ? (
            <button
              className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
              onClick={() => handleToggleHidden(cat)}
              disabled={updateCatMutation.isPending || hideCatMutation.isPending}
              title={cat.is_hidden ? t('categories.showDefault') : t('categories.hideDefault')}
            >
              {cat.is_hidden ? <Eye size={13} /> : <EyeOff size={13} />}
            </button>
          ) : (
            <button
              className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
              onClick={() => setDeletingCategory(cat)}
              disabled={deleteCatMutation.isPending}
              title={t('common.delete')}
            >
              <Trash2 size={13} />
            </button>
          )}
        </div>
      )}
    </div>
  )

  return (
    <div>
      <PageHeader section={t('categories.title')} title={t('categories.title')} />

      <p className="mb-4 text-sm text-muted-foreground">
        {t('categories.hiddenScopeDescription')}
      </p>

      <SectionCard>
        <SectionHeader
          title={t('categories.title')}
          titleExtra={
            <button
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => {
                if (!groups) return
                const allCollapsed = groups.every((g) => collapsedGroups.has(g.id))
                if (allCollapsed) {
                  setCollapsedGroups(new Set())
                } else {
                  setCollapsedGroups(new Set(groups.map((g) => g.id)))
                }
              }}
            >
              <ChevronsUpDown size={13} />
              {groups && groups.every((g) => collapsedGroups.has(g.id)) ? t('categories.expandAll') : t('categories.collapseAll')}
            </button>
          }
          action={
            canWrite ? (
              <div className="flex gap-2">
                <Button variant="outline" size="sm" className="gap-1.5 h-8" onClick={() => openGroupDialog(null)}>
                  <Plus size={13} /> <span className="hidden sm:inline">{t('groups.add')}</span>
                </Button>
                <Button size="sm" className="gap-1.5 h-8" onClick={() => openCatDialog(null)}>
                  <Plus size={13} /> <span className="hidden sm:inline">{t('categories.addCategory')}</span>
                </Button>
              </div>
            ) : undefined
          }
        />
        <div>
          {groups?.map((group) => {
            const isCollapsed = collapsedGroups.has(group.id)
            return (
              <div key={group.id} className={group.is_hidden ? 'opacity-60' : ''}>
                <div className="flex items-center gap-2 px-4 sm:px-5 py-3 border-b border-border bg-muted/40">
                  <button
                    className="flex items-center gap-2 flex-1 min-w-0 text-left"
                    onClick={() => toggleCollapse(group.id)}
                  >
                    {isCollapsed ? <ChevronRight size={14} className="text-muted-foreground shrink-0" /> : <ChevronDown size={14} className="text-muted-foreground shrink-0" />}
                    <CategoryIcon icon={group.icon} color={group.color} size="md" />
                    <span className="text-sm font-semibold" style={{ color: group.color }}>{group.name}</span>
                    {group.is_hidden && renderHiddenBadge(t('groups.hiddenBadge'))}
                    <span className="text-xs text-muted-foreground">({group.categories.length})</span>
                  </button>
                  {canWrite && (
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
                        onClick={() => openGroupDialog(group)}
                        title={t('common.edit')}
                      >
                        <Pencil size={13} />
                      </button>
                      {group.is_system ? (
                        <button
                          className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
                          onClick={() => updateGroupMutation.mutate({ id: group.id, is_hidden: !group.is_hidden })}
                          disabled={updateGroupMutation.isPending}
                          title={group.is_hidden ? t('groups.showDefault') : t('groups.hideDefault')}
                        >
                          {group.is_hidden ? <Eye size={13} /> : <EyeOff size={13} />}
                        </button>
                      ) : (
                        <button
                          className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
                          onClick={() => setDeletingGroup(group)}
                          disabled={deleteGroupMutation.isPending}
                          title={t('common.delete')}
                        >
                          <Trash2 size={13} />
                        </button>
                      )}
                    </div>
                  )}
                </div>
                {!isCollapsed && group.categories.map(renderCategoryItem)}
              </div>
            )
          })}
          {ungrouped.length > 0 && (
            <div>
              <div className="px-5 py-3 border-b border-border bg-muted/40">
                <span className="text-sm font-semibold text-muted-foreground">{t('groups.noGroup')}</span>
              </div>
              {ungrouped.map(renderCategoryItem)}
            </div>
          )}
        </div>
      </SectionCard>

      {/* Category Dialog */}
      <Dialog open={catDialogOpen} onOpenChange={() => { setCatDialogOpen(false); setEditingCat(null) }}>
        <DialogContent className="flex max-h-[calc(100dvh-2rem)] flex-col overflow-hidden">
          <DialogHeader>
            <DialogTitle>{editingCat ? t('categories.editCategory') : t('categories.newCategory')}</DialogTitle>
          </DialogHeader>
          <form
            key={editingCat?.id ?? 'new'}
            onSubmit={(e) => {
              e.preventDefault()
              const formData = new FormData(e.currentTarget)
              const data = {
                name: formData.get('name') as string,
                icon: formData.get('icon') as string,
                color: formData.get('color') as string,
                group_id: (formData.get('group_id') as string) || null,
                treat_as_transfer: formTreatAsTransfer,
                is_ignored: formIgnoreTransfer
              }
              if (editingCat) {
                updateCatMutation.mutate({ id: editingCat.id, ...data })
              } else {
                createCatMutation.mutate(data)
              }
            }}
            className="flex min-h-0 flex-1 flex-col"
          >
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-3">
              <div className="space-y-2">
                <Label>{t('groups.name')}</Label>
                <Input name="name" defaultValue={editingCat?.name ?? ''} required />
              </div>
              <div className="space-y-2">
                <Label>{t('categories.group')}</Label>
                <select
                  name="group_id"
                  defaultValue={editingCat?.group_id ?? ''}
                  className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                >
                  <option value="">{t('categories.noGroup')}</option>
                  {groups?.filter((g) => !g.is_hidden || g.id === editingCat?.group_id).map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}{g.is_hidden ? ` (${t('groups.hiddenBadge')})` : ''}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-2">
                <Label>{t('groups.color')}</Label>
                <Input name="color" type="color" value={formColor} onChange={(e) => setFormColor(e.target.value)} required className="h-9 px-2 py-1" />
              </div>
              <div className="space-y-2">
                <Label>{t('groups.icon')}</Label>
                <IconPicker value={formIcon} color={formColor} onChange={setFormIcon} />
                <input type="hidden" name="icon" value={formIcon} />
              </div>
              <div className="pt-2 border-t border-border">
                <label className="flex items-start gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formTreatAsTransfer}
                    onChange={(e) => setFormTreatAsTransfer(e.target.checked)}
                    className="h-4 w-4 mt-0.5 rounded border-border shrink-0"
                  />
                  <div className="flex-1 min-w-0">
                    <span className="text-sm font-medium text-foreground">{t('categories.treatAsTransfer')}</span>
                    <p className="text-xs text-muted-foreground mt-0.5">{t('categories.treatAsTransferDesc')}</p>
                  </div>
                </label>
                <label className="flex items-start gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formIgnoreTransfer}
                    onChange={(e) => setFormIgnoreTransfer(e.target.checked)}
                    className="h-4 w-4 mt-0.5 rounded border-border shrink-0"
                  />
                  <div className="flex-1 min-w-0">
                    <span className="text-sm font-medium text-foreground">{t('categories.ignoreTransfer')}</span>
                    <p className="text-xs text-muted-foreground mt-0.5">{t('categories.ignoreTransferDesc')}</p>
                  </div>
                </label>
              </div>
            </div>
            <DialogFooter className="mt-2 shrink-0 border-t pt-4">
              <Button type="button" variant="outline" onClick={() => { setCatDialogOpen(false); setEditingCat(null) }}>
                {t('common.cancel')}
              </Button>
              <Button type="submit">{t('common.save')}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Group Dialog */}
      <Dialog open={groupDialogOpen} onOpenChange={() => { setGroupDialogOpen(false); setEditingGroup(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editingGroup ? t('groups.edit') : t('groups.new')}</DialogTitle>
          </DialogHeader>
          <form
            key={editingGroup?.id ?? 'new-group'}
            onSubmit={(e) => {
              e.preventDefault()
              const formData = new FormData(e.currentTarget)
              const data = {
                name: formData.get('name') as string,
                icon: formData.get('icon') as string,
                color: formData.get('color') as string,
                position: parseInt(formData.get('position') as string) || 0,
              }
              if (editingGroup) {
                updateGroupMutation.mutate({ id: editingGroup.id, ...data })
              } else {
                createGroupMutation.mutate(data)
              }
            }}
            className="space-y-4"
          >
            <div className="space-y-2">
              <Label>{t('groups.name')}</Label>
              <Input name="name" defaultValue={editingGroup?.name ?? ''} required />
            </div>
            <div className="space-y-2">
              <Label>{t('groups.position')}</Label>
              <Input name="position" type="number" defaultValue={editingGroup?.position?.toString() ?? '0'} />
            </div>
            <div className="space-y-2">
              <Label>{t('groups.color')}</Label>
              <Input name="color" type="color" value={groupFormColor} onChange={(e) => setGroupFormColor(e.target.value)} required className="h-9 px-2 py-1" />
            </div>
            <div className="space-y-2">
              <Label>{t('groups.icon')}</Label>
              <IconPicker value={groupFormIcon} color={groupFormColor} onChange={setGroupFormIcon} />
              <input type="hidden" name="icon" value={groupFormIcon} />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => { setGroupDialogOpen(false); setEditingGroup(null) }}>
                {t('common.cancel')}
              </Button>
              <Button type="submit">{t('common.save')}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <DeleteConfirmationDialog
        open={!!deletingCategory}
        title={t('categories.confirmDeleteTitle')}
        description={t('categories.confirmDeleteDescription', { name: deletingCategory?.name })}
        isPending={deleteCatMutation.isPending}
        onClose={() => setDeletingCategory(null)}
        onConfirm={() => deletingCategory && deleteCatMutation.mutate(deletingCategory.id)}
      />

      <DeleteConfirmationDialog
        open={!!deletingGroup}
        title={t('groups.confirmDeleteTitle')}
        description={t('groups.confirmDeleteDescription', { name: deletingGroup?.name })}
        isPending={deleteGroupMutation.isPending}
        onClose={() => setDeletingGroup(null)}
        onConfirm={() => deletingGroup && deleteGroupMutation.mutate(deletingGroup.id)}
      />

      <Dialog open={!!hidingCategory} onOpenChange={(open) => !open && setHidingCategory(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {t('categories.hideWithRulesTitle', { name: hidingCategory?.category.name })}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              {t('categories.hideWithRulesDescription', { count: hidingCategory?.rules.length ?? 0 })}
            </p>
            <ul className="max-h-40 overflow-y-auto rounded-lg border border-border divide-y divide-border">
              {hidingCategory?.rules.map((rule) => (
                <li key={rule.id} className="px-3 py-2 text-sm text-foreground truncate">
                  {rule.name}
                </li>
              ))}
            </ul>
          </div>
          <DialogFooter className="gap-2 sm:justify-between">
            <Button
              variant="ghost"
              onClick={() => setHidingCategory(null)}
              disabled={hideCatMutation.isPending}
            >
              {t('common.cancel')}
            </Button>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                onClick={() =>
                  hidingCategory
                  && hideCatMutation.mutate({ id: hidingCategory.category.id, deactivateRules: false })
                }
                disabled={hideCatMutation.isPending}
              >
                {t('categories.hideKeepRules')}
              </Button>
              <Button
                onClick={() =>
                  hidingCategory
                  && hideCatMutation.mutate({ id: hidingCategory.category.id, deactivateRules: true })
                }
                disabled={hideCatMutation.isPending}
              >
                {t('categories.hideAndTurnOffRules')}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
