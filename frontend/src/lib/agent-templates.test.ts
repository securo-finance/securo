/// <reference types="node" />
import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { AGENT_TEMPLATES, BUILTIN_MCP_SERVER } from '@/lib/agent-templates'

const LOCALES_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'locales')

// Built-in MCP tool contract. If a tool is renamed or removed on the
// backend, every template that references it must be reviewed here.
const KNOWN_BUILTIN_TOOLS = new Set([
  'aggregate',
  'get_account_summary',
  'get_budget_vs_actual',
  'get_cash_flow',
  'get_dashboard_snapshot',
  'get_group_balances',
  'get_income_expenses',
  'get_net_worth',
  'list_accounts',
  'list_assets',
  'list_budgets',
  'list_categories',
  'list_goals',
  'list_groups',
  'list_group_settlements',
  'list_payees',
  'list_recurring_transactions',
  'list_rules',
  'list_transactions',
  'preview_rule',
  'propose_cancel_recurring_transaction',
  'propose_categorize',
  'propose_create_budget',
  'propose_create_category',
  'propose_create_goal',
  'propose_create_payee_rule',
  'propose_create_recurring_transaction',
  'propose_create_rule',
  'propose_create_transaction',
  'propose_delete_rule',
  'propose_update_recurring_transaction',
  'propose_update_rule',
  'search_all',
  'search_knowledge_base',
])

function flattenKeys(obj: Record<string, unknown>, prefix = ''): string[] {
  return Object.entries(obj).flatMap(([k, v]) => {
    const full = prefix ? `${prefix}.${k}` : k
    return v !== null && typeof v === 'object' && !Array.isArray(v)
      ? flattenKeys(v as Record<string, unknown>, full)
      : [full]
  })
}

const LOCALES = readdirSync(LOCALES_DIR)
  .filter((f: string) => f.endsWith('.json'))
  .map((f: string) => f.replace(/\.json$/, ''))

describe('agent templates', () => {
  it('defines at least the templates requested by the community', () => {
    const ids = AGENT_TEMPLATES.map((t) => t.id)
    expect(ids).toContain('savings-advisor')
    expect(ids).toContain('monthly-review')
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('keeps every template complete and well-formed', () => {
    for (const tpl of AGENT_TEMPLATES) {
      expect(tpl.nameKey, tpl.id).toMatch(/^agents\.templates\./)
      expect(tpl.descriptionKey, tpl.id).toMatch(/^agents\.templates\./)
      expect(tpl.systemPrompt.trim().length, tpl.id).toBeGreaterThan(200)
      expect(tpl.tools.length, tpl.id).toBeGreaterThan(0)
      // Public text rules: no em dash (U+2014) in shipped prompts.
      expect(tpl.systemPrompt, tpl.id).not.toMatch(/\u2014/)
    }
  })

  it('only references tools that exist on the built-in MCP server', () => {
    expect(BUILTIN_MCP_SERVER).toBe('securo')
    for (const tpl of AGENT_TEMPLATES) {
      for (const tool of tpl.tools) {
        expect(KNOWN_BUILTIN_TOOLS.has(tool), `${tpl.id} -> ${tool}`).toBe(true)
      }
    }
  })

  it('has translated name and description keys in every locale', () => {
    for (const locale of LOCALES) {
      const keys = new Set(flattenKeys(JSON.parse(readFileSync(path.join(LOCALES_DIR, `${locale}.json`), 'utf-8'))))
      for (const tpl of AGENT_TEMPLATES) {
        expect(keys.has(tpl.nameKey), `${locale} ${tpl.id} name`).toBe(true)
        expect(keys.has(tpl.descriptionKey), `${locale} ${tpl.id} description`).toBe(true)
      }
      expect(keys.has('agents.templates.title'), `${locale} title`).toBe(true)
      expect(keys.has('agents.templates.create'), `${locale} create`).toBe(true)
    }
  })
})
