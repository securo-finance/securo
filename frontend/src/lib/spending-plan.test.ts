import { describe, expect, it } from 'vitest'
import type { SpendingPlanEvent, SpendingPlanTimeline } from '@/types'
import {
  balanceOn,
  balanceTone,
  cleanDescription,
  dayEntries,
  firstDayBelowFloor,
  lookupShortcuts,
  gridFrom,
  monthGrid,
  monthsBetween,
  planReceipt,
  waterfallFor,
} from './spending-plan'

function event(
  date: string,
  description: string | null,
  amount: number,
  source: SpendingPlanEvent['source'],
  account_name: string | null = null,
): SpendingPlanEvent {
  return { date, description, amount, source, account_name }
}

const salary = event('2026-11-13', 'Salário', 3000, 'recurring', 'Checking')
const netflix = event('2026-11-13', 'Netflix', -50, 'card', 'Visa')
const market = event('2026-11-13', 'Mercado (parcela 2/3)', -100, 'card', 'Visa')

// Today is the 12th: 30 a day, 10 already spent today, 200 floor.
const timeline: SpendingPlanTimeline = {
  currency: 'BRL',
  daily_allowance: 30,
  balance_floor: 200,
  horizon_end: '2026-11-16',
  safe_daily_allowance: 382.5,
  average_daily_surplus: 0,
  covered_until: '2026-11-16',
  covered_through_horizon: true,
  opening_balance: 1000,
  binding_date: '2026-11-15',
  today: { allowance: 30, spent: 10, remaining: 20 },
  points: [
    { date: '2026-11-12', balance: 970 },
    { date: '2026-11-13', balance: 3790 },
    { date: '2026-11-14', balance: 2060 },
    { date: '2026-11-15', balance: 1610 },
    { date: '2026-11-16', balance: 2580 },
  ],
  events: [
    event('2026-11-12', null, -10, 'other'),
    salary,
    netflix,
    market,
    event('2026-11-14', 'Aluguel', -1500, 'recurring', 'Checking'),
    event('2026-11-14', 'Dentista', -200, 'planned', 'Checking'),
    event('2026-11-15', 'TV 1/2', -400, 'scenario'),
    event('2026-11-15', 'Uber', -20, 'card', 'Nubank'),
    event('2026-11-16', 'Freela', 1000, 'recurring', 'Checking'),
  ],
}

describe('balanceOn', () => {
  it('reads the last point on or before the date', () => {
    expect(balanceOn(timeline.points, '2026-11-14')).toBe(2060)
    expect(balanceOn(timeline.points, '2027-01-01')).toBe(2580)
    expect(balanceOn(timeline.points, '2026-11-01')).toBeNull()
  })
})

describe('cleanDescription', () => {
  it('drops the installment marker so every installment reads as one item', () => {
    expect(cleanDescription('Mercado (parcela 2/3)')).toBe('Mercado')
    expect(cleanDescription('Curso (1ª parcela)')).toBe('Curso')
    expect(cleanDescription('TV 3/10')).toBe('TV')
    expect(cleanDescription('Aluguel')).toBe('Aluguel')
  })
})

describe('balanceTone', () => {
  it('is good well above the floor, warn near it and bad below it', () => {
    expect(balanceTone(1700, 200)).toBe('good')
    expect(balanceTone(1699.99, 200)).toBe('warn')
    expect(balanceTone(200, 200)).toBe('warn')
    expect(balanceTone(199.99, 200)).toBe('bad')
  })
})

describe('dayEntries', () => {
  it('folds the card charges due on a day into one bill per card', () => {
    expect(dayEntries([netflix, salary, market])).toEqual([
      { kind: 'event', event: salary, amount: 3000 },
      { kind: 'bill', account: 'Visa', amount: -150, children: [netflix, market] },
    ])
  })
})

describe('waterfallFor', () => {
  it('takes the balance on a day apart into parts that add up to it', () => {
    const waterfall = waterfallFor(timeline, '2026-11-15')

    expect(waterfall).toEqual({
      date: '2026-11-15',
      opening: 1000,
      income: { total: 3000, items: [{ label: 'Salário', amount: 3000 }] },
      fixed: {
        total: -1700,
        items: [
          { label: 'Aluguel', amount: -1500 },
          { label: 'Dentista', amount: -200 },
        ],
      },
      card: {
        total: -170,
        items: [
          { label: 'Visa', amount: -150 },
          { label: 'Nubank', amount: -20 },
        ],
      },
      scenario: { total: -400, items: [{ label: 'TV', amount: -400 }] },
      other: -10,
      days: 4,
      daily: -110,
      balance: 1610,
    })
  })

  it('adds up to the projected balance on every day', () => {
    for (const point of timeline.points) {
      const w = waterfallFor(timeline, point.date)!
      const sum = w.opening + w.income.total + w.fixed.total + w.card.total + w.scenario.total + w.other + w.daily
      expect(Math.round(sum * 100) / 100).toBe(point.balance)
      expect(w.other).toBe(-10)
    }
  })

  it('has nothing before today', () => {
    expect(waterfallFor(timeline, '2026-11-11')).toBeNull()
  })
})

describe('planReceipt', () => {
  it('lists everything through the binding day and divides what is left by its days', () => {
    expect(planReceipt(timeline)).toEqual({
      through: '2026-11-15',
      days: 4,
      opening: 1000,
      groups: [
        { key: 'income', bucket: { total: 3000, items: [{ label: 'Salário', amount: 3000 }] } },
        {
          key: 'fixed',
          bucket: {
            total: -1700,
            items: [
              { label: 'Aluguel', amount: -1500 },
              { label: 'Dentista', amount: -200 },
            ],
          },
        },
        {
          key: 'card',
          bucket: {
            total: -170,
            items: [
              { label: 'Mercado', amount: -100 },
              { label: 'Netflix', amount: -50 },
              { label: 'Uber', amount: -20 },
            ],
          },
        },
        { key: 'scenario', bucket: { total: -400, items: [{ label: 'TV', amount: -400 }] } },
        { key: 'other', bucket: { total: -10, items: [] } },
      ],
      spentToday: 10,
      floor: 200,
      free: 1530,
      perDay: 382.5,
    })
  })

  it('adds up to the opening balance plus every event through the binding day, less the floor', () => {
    const receipt = planReceipt(timeline)!
    const events = timeline.events.filter((e) => e.date <= receipt.through).reduce((total, e) => total + e.amount, 0)

    expect(receipt.free).toBe(timeline.opening_balance + events - timeline.balance_floor + timeline.today.spent)
    expect(receipt.perDay).toBe(timeline.safe_daily_allowance)
  })

  it('floors the money per day to cents', () => {
    expect(planReceipt({ ...timeline, opening_balance: 1000.01 })!.perDay).toBe(382.5)
    expect(planReceipt({ ...timeline, opening_balance: 999.99 })!.perDay).toBe(382.49)
  })

  it('has nothing without a binding day', () => {
    expect(planReceipt({ ...timeline, binding_date: null })).toBeNull()
  })
})

describe('firstDayBelowFloor', () => {
  it('is the day after the coverage ends', () => {
    expect(firstDayBelowFloor({ ...timeline, covered_through_horizon: false, covered_until: '2026-11-13' })).toBe(
      '2026-11-14',
    )
    expect(firstDayBelowFloor({ ...timeline, covered_through_horizon: false, covered_until: null })).toBe('2026-11-12')
    expect(firstDayBelowFloor(timeline)).toBeNull()
  })
})

describe('calendar dates', () => {
  it('pads a Sunday-first grid', () => {
    expect(monthGrid('2026-11').slice(0, 2)).toEqual(['2026-11-01', '2026-11-02'])
    expect(monthGrid('2026-11')).toHaveLength(30)
    expect(monthGrid('2026-12').slice(0, 3)).toEqual([null, null, '2026-12-01'])
  })

  it('starts the grid at the week holding today', () => {
    expect(gridFrom('2026-11', '2026-11-16').slice(0, 2)).toEqual(['2026-11-15', '2026-11-16'])
    expect(gridFrom('2026-11', '2026-11-15')[0]).toBe('2026-11-15')
    expect(gridFrom('2026-12', '2026-11-16').slice(0, 3)).toEqual([null, null, '2026-12-01'])
  })

  it('lists the months of the plan', () => {
    expect(monthsBetween('2026-11-16', '2027-02-16')).toEqual(['2026-11', '2026-12', '2027-01', '2027-02'])
  })

  it('offers lookup shortcuts within the plan', () => {
    expect(lookupShortcuts('2026-11-16', '2027-11-16')).toEqual([
      { key: 'monthEnd', date: '2026-11-30' },
      { key: 'yearEnd', date: '2026-12-31' },
      { key: 'threeMonths', date: '2027-02-16' },
      { key: 'sixMonths', date: '2027-05-16' },
      { key: 'horizon', date: '2027-11-16' },
    ])
    expect(lookupShortcuts('2026-12-10', '2027-06-10')[1]).toEqual({ key: 'yearEnd', date: '2027-06-10' })
    expect(lookupShortcuts('2027-01-31', '2028-01-31')[2]).toEqual({ key: 'threeMonths', date: '2027-04-30' })
  })
})
