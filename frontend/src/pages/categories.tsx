import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { categories as categoriesApi, categoryGroups as groupsApi } from '@/lib/api'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
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
import { Pencil, Trash2, Plus, ChevronDown, ChevronRight, ChevronsUpDown } from 'lucide-react'
import { PageHeader } from '@/components/page-header'
import { CategoryIcon } from '@/components/category-icon'
import { IconPicker } from '@/components/icon-picker'

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

  const { data: groups } = useQuery({
    queryKey: ['category-groups'],
    queryFn: groupsApi.list,
  })

  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ['categories'] })
    queryClient.invalidateQueries({ queryKey: ['category-groups'] })
  }

  const createCatMutation = useMutation({
    mutationFn: (cat: Partial<Category>) => categoriesApi.create(cat),
    onSuccess: () => { invalidateAll(); setCatDialogOpen(false); toast.success(t('categories.created')) },
  })
  const updateCatMutation = useMutation({
    mutationFn: ({ id, ...data }: Partial<Category> & { id: string }) => categoriesApi.update(id, data),
    onSuccess: () => { invalidateAll(); setCatDialogOpen(false); setEditingCat(null); toast.success(t('categories.updated')) },
  })
  const deleteCatMutation = useMutation({
    mutationFn: (id: string) => categoriesApi.delete(id),
    onSuccess: () => { invalidateAll(); toast.success(t('categories.deleted')) },
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
    onSuccess: () => { invalidateAll(); toast.success(t('groups.deleted')) },
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
    setCatDialogOpen(true)
  }

  const openGroupDialog = (group: CategoryGroup | null) => {
    setEditingGroup(group)
    setGroupFormIcon(group?.icon ?? 'folder')
    setGroupFormColor(group?.color ?? '#6B7280')
    setGroupDialogOpen(true)
  }

  const renderCategoryItem = (cat: Category) => (
    <div key={cat.id} className="flex items-center gap-3 px-4 sm:px-5 pl-6 sm:pl-12 py-2.5 border-b border-border last:border-0 hover:bg-muted transition-colors">
      <CategoryIcon icon={cat.icon} color={cat.color} size="md" />
      <div className="flex-1 min-w-0 flex items-center gap-2">
        <span className="text-sm font-medium text-foreground truncate">{cat.name}</span>
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
      <div className="flex items-center gap-1 shrink-0 ml-2">
        <button
          className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
          onClick={() => openCatDialog(cat)}
          title={t('common.edit')}
        >
          <Pencil size={13} />
        </button>
        {!cat.is_system && (
          <button
            className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
            onClick={() => deleteCatMutation.mutate(cat.id)}
            disabled={deleteCatMutation.isPending}
            title={t('common.delete')}
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>
    </div>
  )

  return (
    <div>
      <PageHeader section={t('categories.title')} title={t('categories.title')} />

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
            <div className="flex gap-2">
              <Button variant="outline" size="sm" className="gap-1.5 h-8" onClick={() => openGroupDialog(null)}>
                <Plus size={13} /> <span className="hidden sm:inline">{t('groups.add')}</span>
              </Button>
              <Button size="sm" className="gap-1.5 h-8" onClick={() => openCatDialog(null)}>
                <Plus size={13} /> <span className="hidden sm:inline">{t('categories.addCategory')}</span>
              </Button>
            </div>
          }
        />
        <div>
          {groups?.map((group) => {
            const isCollapsed = collapsedGroups.has(group.id)
            return (
              <div key={group.id}>
                <div className="flex items-center gap-2 px-4 sm:px-5 py-3 border-b border-border bg-muted/40">
                  <button
                    className="flex items-center gap-2 flex-1 min-w-0 text-left"
                    onClick={() => toggleCollapse(group.id)}
                  >
                    {isCollapsed ? <ChevronRight size={14} className="text-muted-foreground shrink-0" /> : <ChevronDown size={14} className="text-muted-foreground shrink-0" />}
                    <CategoryIcon icon={group.icon} color={group.color} size="md" />
                    <span className="text-sm font-semibold" style={{ color: group.color }}>{group.name}</span>
                    <span className="text-xs text-muted-foreground">({group.categories.length})</span>
                  </button>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
                      onClick={() => openGroupDialog(group)}
                      title={t('common.edit')}
                    >
                      <Pencil size={13} />
                    </button>
                    {!group.is_system && (
                      <button
                        className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
                        onClick={() => deleteGroupMutation.mutate(group.id)}
                        disabled={deleteGroupMutation.isPending}
                        title={t('common.delete')}
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>
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
        <DialogContent>
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
            className="space-y-4"
          >
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
                {groups?.map((g) => (
                  <option key={g.id} value={g.id}>{g.name}</option>
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
            <DialogFooter>
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
    </div>
  )
}
