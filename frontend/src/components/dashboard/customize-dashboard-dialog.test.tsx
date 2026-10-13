import { describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import { CustomizeDashboardDialog } from '@/components/dashboard/customize-dashboard-dialog'
import { DEFAULT_LAYOUT } from '@/components/dashboard/widgets'
import { renderWithProviders, t } from '@/test/utils'

function renderDialog(layout = DEFAULT_LAYOUT) {
  const onSave = vi.fn()
  const onOpenChange = vi.fn()
  const utils = renderWithProviders(
    <CustomizeDashboardDialog open onOpenChange={onOpenChange} layout={layout} onSave={onSave} />,
  )
  return { ...utils, onSave, onOpenChange }
}

const label = (key: string) => t(`dashboard.customize.widgets.${key}`)

describe('CustomizeDashboardDialog', () => {
  it('lists every widget in the saved order', () => {
    renderDialog({ ...DEFAULT_LAYOUT, order: ['transactions', ...DEFAULT_LAYOUT.order.filter((id) => id !== 'transactions')] })

    const items = within(screen.getByRole('list')).getAllByRole('listitem')
    expect(items).toHaveLength(DEFAULT_LAYOUT.order.length)
    expect(items[0]).toHaveTextContent(label('transactions'))
  })

  it('saves hidden widgets and the new order', async () => {
    const { user, onSave, onOpenChange } = renderDialog()

    await user.click(screen.getByRole('switch', { name: label('goals') }))
    await user.click(screen.getByRole('button', { name: t('dashboard.customize.moveUp', { widget: label('uncategorized') }) }))
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    expect(onSave).toHaveBeenCalledWith({
      order: ['uncategorized', 'hero', 'category-spending', 'balance-flow', 'goals', 'transactions'],
      hidden: ['goals'],
    })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('resets to the default layout', async () => {
    const { user, onSave } = renderDialog({
      order: [...DEFAULT_LAYOUT.order].reverse(),
      hidden: ['hero', 'goals'],
    })

    await user.click(screen.getByRole('button', { name: t('dashboard.customize.reset') }))
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    expect(onSave).toHaveBeenCalledWith(DEFAULT_LAYOUT)
  })

  it('disables moving past either end', () => {
    renderDialog()

    expect(screen.getByRole('button', { name: t('dashboard.customize.moveUp', { widget: label('hero') }) })).toBeDisabled()
    expect(screen.getByRole('button', { name: t('dashboard.customize.moveDown', { widget: label('transactions') }) })).toBeDisabled()
  })
})
