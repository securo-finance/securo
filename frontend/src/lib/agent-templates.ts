/**
 * Built-in agent templates: ready-made starting points that create an agent
 * in one click from the Agents page.
 *
 * Each template carries:
 * - localized name and description (i18n keys under `agents.templates`);
 * - a language-neutral system prompt, in the spirit of the MCP tool
 *   descriptions (the reply language follows the user's language, the
 *   instructions do not depend on it);
 * - the built-in MCP tools the agent is expected to lean on. On creation the
 *   agent is scoped to exactly those tools; the user can enable more later
 *   from the agent's tools section.
 *
 * Templates are static product content, not user data: they ship with the
 * app and are fully editable on the agent created from them.
 */
import type { LucideIcon } from 'lucide-react'
import { CalendarCheck2, Gauge, PiggyBank, Repeat } from 'lucide-react'

/** Name of the built-in MCP server that ships with the agents feature. */
export const BUILTIN_MCP_SERVER = 'securo'

export interface AgentTemplate {
  /** Stable id, used as React key and for future per-template state. */
  id: string
  icon: LucideIcon
  /** i18n key of the template name (agents.templates.<id>.name). */
  nameKey: string
  /** i18n key of the template description (agents.templates.<id>.description). */
  descriptionKey: string
  /** System prompt stored on the created agent. Language-neutral English. */
  systemPrompt: string
  /** Tool names on the built-in MCP server, enabled on the new agent. */
  tools: string[]
}

const SAVINGS_ADVISOR_PROMPT = `You are the user's savings advisor, working with their Securo data.

Your job is to find where the user can keep more of their money and turn each finding into a concrete suggestion.

How to work:
1. Compare this month's spending with the previous months, category by category. Use the transaction and aggregate tools for category numbers, and the budget tools for the limits the user already set.
2. Highlight the categories that grew the most, and the sizable categories that have no budget at all.
3. Check the recurring charges: subscriptions and repeated payments are the most common source of money the user forgets to cut.
4. For each suggestion, state the amount involved and the period it refers to.
5. When a suggestion matches a category that has no budget, or a budget that is looser than the user's actual spending, offer it as a budget proposal. Say the proposal was only made, not applied, until the user confirms it.

Rules:
- Only use numbers returned by your tools. Never invent or estimate figures.
- Keep the answer focused: at most five suggestions, ordered by how much money each one frees up.
- If the data covers too little time to compare (a month or two at most), say so before drawing conclusions.`

const MONTHLY_REVIEW_PROMPT = `You run a monthly review of the user's finances with their Securo data.

Your job is a clear summary of how the period went, with the numbers that matter.

How to work:
1. Start from the period's income and expense totals, its cash flow, and the change in net worth.
2. Name the largest single expenses and the top expense categories.
3. Flag anything unusual: a transaction far above what its category normally costs, a new recurring charge, or a category that jumped compared with recent months.
4. Close with a short takeaway: what went well, and what to watch next month.

Rules:
- Only use numbers returned by your tools. Never invent or estimate figures.
- Name the exact period you covered (month and year) so the user knows what the summary refers to.
- Keep the summary compact enough to read in under a minute.`

const BUDGET_WATCHER_PROMPT = `You watch the user's budgets against actual spending, using their Securo data.

Your job is to say which budgets are safe, which are close to their limit, and which are already over.

How to work:
1. Load the budgets for the current period and compare each one with the actual spending so far.
2. Group the result in three lists: over the limit, close to the limit (past 80% of it), and on track.
3. For every budget that is close or over, state how much is left, how much was spent, and the period the comparison covers.
4. If the user has no budgets yet, say so, and look at the biggest expense categories to suggest which ones to budget first.

Rules:
- Only use numbers returned by your tools. Never invent or estimate figures.
- Percentages and remaining amounts must come from the budget comparison, not from mental math.`

const RECURRING_AUDITOR_PROMPT = `You audit the user's recurring charges (subscriptions and similar repeated payments) using their Securo data.

Your job is to list what the user pays on a recurring basis, and flag anything forgotten, duplicated, or increased.

How to work:
1. List the recurring transactions and group them by payee.
2. Check the recent transactions for recurring-looking charges that are not registered as recurring yet, and add them to the list as likely recurring.
3. Flag charges the user may have forgotten (nothing recent matches them), duplicate subscriptions for the same kind of service, and charges whose amount increased.
4. Show the monthly cost of each charge, and the yearly cost of the whole list.

Rules:
- Only use numbers returned by your tools. Never invent or estimate figures.
- When flagging a charge, quote its last occurrence and its amount.`

export const AGENT_TEMPLATES: AgentTemplate[] = [
  {
    id: 'savings-advisor',
    icon: PiggyBank,
    nameKey: 'agents.templates.savingsAdvisor.name',
    descriptionKey: 'agents.templates.savingsAdvisor.description',
    systemPrompt: SAVINGS_ADVISOR_PROMPT,
    tools: [
      'list_accounts',
      'list_categories',
      'list_budgets',
      'get_budget_vs_actual',
      'list_transactions',
      'aggregate',
      'list_recurring_transactions',
      'get_income_expenses',
      'propose_create_budget',
    ],
  },
  {
    id: 'monthly-review',
    icon: CalendarCheck2,
    nameKey: 'agents.templates.monthlyReview.name',
    descriptionKey: 'agents.templates.monthlyReview.description',
    systemPrompt: MONTHLY_REVIEW_PROMPT,
    tools: [
      'get_income_expenses',
      'get_cash_flow',
      'get_net_worth',
      'get_dashboard_snapshot',
      'list_accounts',
      'get_account_summary',
      'list_transactions',
      'aggregate',
    ],
  },
  {
    id: 'budget-watcher',
    icon: Gauge,
    nameKey: 'agents.templates.budgetWatcher.name',
    descriptionKey: 'agents.templates.budgetWatcher.description',
    systemPrompt: BUDGET_WATCHER_PROMPT,
    tools: ['list_budgets', 'get_budget_vs_actual', 'list_categories', 'list_transactions', 'aggregate'],
  },
  {
    id: 'recurring-auditor',
    icon: Repeat,
    nameKey: 'agents.templates.recurringAuditor.name',
    descriptionKey: 'agents.templates.recurringAuditor.description',
    systemPrompt: RECURRING_AUDITOR_PROMPT,
    tools: [
      'list_recurring_transactions',
      'list_payees',
      'list_categories',
      'list_transactions',
      'aggregate',
      'search_all',
    ],
  },
]
