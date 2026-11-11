import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { WidgetGrid } from '@/components/widget-grid'
import { TooltipProvider } from '@/components/ui/tooltip'
import { DASHBOARD_WIDGETS, type LayoutItem } from '@/lib/library'
import { t } from '@/test/utils'

const content = {
  overview: <div>Balance card</div>,
  goals: null,
  'spending-by-category': <div>Spending card</div>,
  'balance-flow': <div>Flow card</div>,
}

const LAYOUT: LayoutItem[] = [{ id: 'overview' }, { id: 'goals' }, { id: 'spending-by-category' }, { id: 'balance-flow' }]

function renderGrid(props: Partial<Parameters<typeof WidgetGrid>[0]> = {}) {
  const onChange = vi.fn()
  const onAdd = vi.fn()
  render(
    <TooltipProvider>
      <WidgetGrid items={LAYOUT} entries={DASHBOARD_WIDGETS} content={content} editing={false} onChange={onChange} onAdd={onAdd} {...props} />
    </TooltipProvider>,
  )
  return { onChange, onAdd }
}

const regions = () => screen.getAllByRole('region').map((region) => region.getAttribute('aria-label'))

describe('WidgetGrid', () => {
  it('shows the widgets in the chosen order and skips one with nothing to show', () => {
    renderGrid({ items: [{ id: 'balance-flow' }, { id: 'overview' }, { id: 'goals' }] })
    expect(regions()).toEqual([t('dashboard.balanceFlow'), t('library.widget.overview')])
  })

  it('lays each widget out at its width', () => {
    renderGrid({ items: [{ id: 'overview', span: 8 }, { id: 'spending-by-category' }] })
    expect(screen.getByRole('region', { name: t('library.widget.overview') })).toHaveClass('lg:col-span-8')
    expect(screen.getByRole('region', { name: t('dashboard.spendingByCategory') })).toHaveClass('lg:col-span-6')
  })

  it('has no edit controls outside editing', () => {
    renderGrid()
    expect(screen.queryByRole('button', { name: t('library.dragToMove', { name: t('library.widget.overview') }) })).not.toBeInTheDocument()
  })

  it('moves a widget with the keyboard', async () => {
    // jsdom lays nothing out: stack the widgets 100px apart so arrows have somewhere to go.
    const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const sections = [...document.querySelectorAll('section[aria-label]')]
      const index = sections.indexOf(this.closest('section[aria-label]') as Element)
      const top = Math.max(index, 0) * 100
      return { x: 0, y: top, top, left: 0, width: 600, height: 90, right: 600, bottom: top + 90, toJSON: () => ({}) } as DOMRect
    })
    const { onChange } = renderGrid({ editing: true })
    const handle = screen.getByRole('button', { name: t('library.dragToMove', { name: t('library.widget.overview') }) })
    // Space picks it up, an arrow moves it, space puts it down.
    const user = userEvent.setup()
    handle.focus()
    await user.keyboard('[Space]')
    await act(() => new Promise((done) => setTimeout(done, 50)))
    await user.keyboard('[ArrowDown]')
    await act(() => new Promise((done) => setTimeout(done, 50)))
    await user.keyboard('[Space]')
    rect.mockRestore()
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange.mock.calls[0][0].map((item: LayoutItem) => item.id)).toEqual(['goals', 'overview', 'spending-by-category', 'balance-flow'])
  })

  it('resizes from the width menu, saving the default width as just the id', async () => {
    const user = userEvent.setup()
    const { onChange } = renderGrid({ editing: true })
    const spending = screen.getByRole('region', { name: t('dashboard.spendingByCategory') })

    await user.click(within(spending).getByRole('button', { name: t('library.widthOf', { name: t('dashboard.spendingByCategory') }) }))
    // Spending reads well down to a third.
    const options = screen.getAllByRole('menuitemradio').map((option) => option.textContent)
    expect(options).toEqual([t('library.width.4'), t('library.width.6'), t('library.width.8'), t('library.width.12')])
    await user.click(screen.getByRole('menuitemradio', { name: t('library.width.8') }))
    expect(onChange).toHaveBeenLastCalledWith(LAYOUT.map((item) => (item.id === 'spending-by-category' ? { id: item.id, span: 8 } : item)))
  })

  it("doesn't offer a width narrower than the widget reads well at", async () => {
    const user = userEvent.setup()
    renderGrid({ editing: true })
    const overview = screen.getByRole('region', { name: t('library.widget.overview') })
    await user.click(within(overview).getByRole('button', { name: t('library.widthOf', { name: t('library.widget.overview') }) }))
    expect(screen.queryByRole('menuitemradio', { name: t('library.width.4') })).not.toBeInTheDocument()
  })

  it('removes a widget while editing', () => {
    const { onChange } = renderGrid({ editing: true })
    const overview = screen.getByRole('region', { name: t('library.widget.overview') })
    fireEvent.click(within(overview).getByRole('button', { name: t('library.removeFrom.dashboard', { name: t('library.widget.overview') }) }))
    expect(onChange).toHaveBeenLastCalledWith(LAYOUT.filter((item) => item.id !== 'overview'))
  })

  it('keeps an empty widget reachable while editing', () => {
    renderGrid({ editing: true })
    const goals = screen.getByRole('region', { name: t('goals.dashboardTitle') })
    expect(within(goals).getByText(t('library.nothingToShow'))).toBeInTheDocument()
  })

  it("doesn't let a widget's own controls take clicks while editing", () => {
    renderGrid({ editing: true })
    expect(screen.getByText('Balance card').closest('[inert]')).not.toBeNull()
  })

  it('offers the library when everything was removed', () => {
    const { onAdd } = renderGrid({ items: [] })
    expect(screen.getByText(t('library.emptyDashboard'))).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: t('library.addWidget') }))
    expect(onAdd).toHaveBeenCalled()
  })

  it('offers the library when no widget has anything to show', () => {
    renderGrid({ items: [{ id: 'goals' }] })
    expect(screen.getByText(t('library.emptyDashboard'))).toBeInTheDocument()
  })

  it('still lets an empty widget be edited', () => {
    renderGrid({ items: [{ id: 'goals' }], editing: true })
    expect(screen.queryByText(t('library.emptyDashboard'))).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: t('goals.dashboardTitle') })).toBeInTheDocument()
  })
})
