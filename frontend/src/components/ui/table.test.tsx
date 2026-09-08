import { describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'

import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { renderWithProviders } from '@/test/utils'

function Example() {
  return (
    <Table>
      <TableCaption>June transactions</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>Date</TableHead>
          <TableHead>Payee</TableHead>
          <TableHead>Amount</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow>
          <TableCell>04/06</TableCell>
          <TableCell>Padaria</TableCell>
          <TableCell>R$ 18,00</TableCell>
        </TableRow>
        <TableRow>
          <TableCell>05/06</TableCell>
          <TableCell>Uber</TableCell>
          <TableCell>R$ 32,40</TableCell>
        </TableRow>
      </TableBody>
      <TableFooter>
        <TableRow>
          <TableCell>Total</TableCell>
          <TableCell />
          <TableCell>R$ 50,40</TableCell>
        </TableRow>
      </TableFooter>
    </Table>
  )
}

describe('Table', () => {
  it('renders a semantic table so screen readers can navigate it', () => {
    renderWithProviders(<Example />)

    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('exposes its column headers', () => {
    renderWithProviders(<Example />)

    const headers = screen.getAllByRole('columnheader')
    expect(headers.map((h) => h.textContent)).toEqual([
      'Date',
      'Payee',
      'Amount',
    ])
  })

  it('renders one row per record plus the header and footer rows', () => {
    renderWithProviders(<Example />)

    expect(screen.getAllByRole('row')).toHaveLength(4)
  })

  it('keeps cells in their row', () => {
    renderWithProviders(<Example />)

    const row = screen.getByText('Padaria').closest('tr')!
    expect(within(row).getByText('R$ 18,00')).toBeInTheDocument()
    expect(within(row).queryByText('R$ 32,40')).not.toBeInTheDocument()
  })

  it('renders the caption', () => {
    renderWithProviders(<Example />)

    expect(screen.getByText('June transactions')).toBeInTheDocument()
  })

  it('renders an empty body without crashing', () => {
    renderWithProviders(
      <Table>
        <TableBody />
      </Table>,
    )

    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.queryAllByRole('row')).toHaveLength(0)
  })
})
