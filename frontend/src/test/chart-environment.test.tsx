import { render, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { Line, LineChart, ResponsiveContainer } from 'recharts'

it('measures a real responsive chart without hiding console warnings', async () => {
  const warn = vi.spyOn(console, 'warn')
  try {
    const { container } = render(
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={[{ value: 1 }, { value: 2 }]}>
          <Line dataKey="value" isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>,
    )
    await waitFor(() => expect(container.querySelector('svg')).toHaveAttribute('width', '800'))
    expect(container.querySelector('svg')).toHaveAttribute('height', '300')
    expect(warn).not.toHaveBeenCalled()
  } finally {
    warn.mockRestore()
  }
})
