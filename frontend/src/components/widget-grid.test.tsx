import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'

import { WidgetGrid } from '@/components/widget-grid'
import { TooltipProvider } from '@/components/ui/tooltip'
import { DASHBOARD_WIDGETS } from '@/lib/library'
import { t } from '@/test/utils'

const content = {
  overview: <div>Balance card</div>,
  goals: null,
  'spending-by-category': <div>Spending card</div>,
  'balance-flow': <div>Flow card</div>,
}

function renderGrid(props: Partial<Parameters<typeof WidgetGrid>[0]> = {}) {
  const onChange = vi.fn()
  const onAdd = vi.fn()
  render(
    <TooltipProvider>
      <WidgetGrid
        ids={['overview', 'goals', 'spending-by-category', 'balance-flow']}
        entries={DASHBOARD_WIDGETS}
        content={content}
        editing={false}
        onChange={onChange}
        onAdd={onAdd}
        {...props}
      />
    </TooltipProvider>,
  )
  return { onChange, onAdd }
}

const title = (key: string) => t(key)

describe('WidgetGrid', () => {
  it('shows the widgets in the chosen order and skips one with nothing to show', () => {
    renderGrid({ ids: ['balance-flow', 'overview', 'goals'] })
    const sections = screen.getAllByRole('region')
    expect(sections.map((s) => s.getAttribute('aria-label'))).toEqual([
      title('dashboard.balanceFlow'),
      title('library.widget.overview'),
    ])
    expect(screen.queryByText(t('library.nothingToShow'))).not.toBeInTheDocument()
  })

  it('has no edit controls outside editing', () => {
    renderGrid()
    expect(screen.queryByRole('button', { name: t('library.moveUp', { name: title('library.widget.overview') }) })).not.toBeInTheDocument()
  })

  it('moves and removes widgets while editing', () => {
    const { onChange } = renderGrid({ editing: true })
    const overview = screen.getByRole('region', { name: title('library.widget.overview') })

    // The first widget can't go further up.
    expect(within(overview).getByRole('button', { name: t('library.moveUp', { name: title('library.widget.overview') }) })).toBeDisabled()
    fireEvent.click(within(overview).getByRole('button', { name: t('library.moveDown', { name: title('library.widget.overview') }) }))
    expect(onChange).toHaveBeenLastCalledWith(['goals', 'overview', 'spending-by-category', 'balance-flow'])

    fireEvent.click(within(overview).getByRole('button', { name: t('library.removeFrom.dashboard', { name: title('library.widget.overview') }) }))
    expect(onChange).toHaveBeenLastCalledWith(['goals', 'spending-by-category', 'balance-flow'])
  })

  it('keeps an empty widget reachable while editing', () => {
    renderGrid({ editing: true })
    const goals = screen.getByRole('region', { name: title('goals.dashboardTitle') })
    expect(within(goals).getByText(t('library.nothingToShow'))).toBeInTheDocument()
  })

  it("doesn't let a widget's own controls take clicks while editing", () => {
    renderGrid({ editing: true })
    expect(screen.getByText('Balance card').closest('[inert]')).not.toBeNull()
  })

  it('offers the library when everything was removed', () => {
    const { onAdd } = renderGrid({ ids: [] })
    expect(screen.getByText(t('library.emptyDashboard'))).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: t('library.addWidget') }))
    expect(onAdd).toHaveBeenCalled()
  })
})
