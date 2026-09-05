/**
 * Global slide-over chat — available from every page (⌘J / Ctrl+J).
 *
 * Header surfaces:
 *   - Agent selector (all non-archived agents; default agent pre-selected)
 *   - Conversation history toggle (resume any prior thread)
 *   - New-conversation +
 *   - Close ×
 *
 * Conversation persistence: the active conversationId is kept in
 * localStorage keyed by agent. Re-opening the panel resumes the same
 * thread instead of starting fresh — the explicit "+" button is the
 * only way to start a new one.
 *
 * Page context: each send forwards a `page_context` snapshot built
 * from the active page's registration (or a synthesized fallback).
 */
import { useEffect, useMemo, useState } from 'react'
import { Dialog as DialogPrimitive } from 'radix-ui'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { ArrowLeft, History, Loader2, Plus, Settings, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { agents } from '@/lib/api'
import type { Agent, AgentConversation } from '@/lib/api'
import { ChatPanel } from '@/components/agents/chat-panel'
import { getEffectivePageChatContext } from '@/lib/page-chat-context'
import { formatRelative } from '@/lib/relative-time'
import { cn } from '@/lib/utils'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
}

const STORAGE_KEY = 'securo.global-chat'

interface PersistedState {
  agentId?: string
  // One conversation pinned per agent so switching agents doesn't lose
  // either thread. Keyed by agent id.
  conversationByAgent?: Record<string, string>
}

function readState(): PersistedState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as PersistedState) : {}
  } catch {
    return {}
  }
}

function writeState(s: PersistedState) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s))
  } catch {
    // localStorage can be disabled (private mode, quota); silent fallback.
  }
}

export function GlobalChatPanel({ open, onOpenChange }: Props) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [view, setView] = useState<'chat' | 'history'>('chat')
  // Bumped on every "new conversation" click + agent switch so the
  // textarea refocuses even when conversationId itself doesn't change.
  const [focusBump, setFocusBump] = useState(0)

  // Persisted preferences (agent + per-agent active conversation).
  const [persisted, setPersisted] = useState<PersistedState>(() => readState())

  const { data: agentsList, isLoading: loadingAgents } = useQuery({
    queryKey: ['agents'],
    queryFn: () => agents.list(false),
    enabled: open,
    staleTime: 1000 * 30,
  })
  const { data: defaultAgent } = useQuery({
    queryKey: ['agents-default'],
    queryFn: () => agents.getDefault(),
    enabled: open && !persisted.agentId,
    retry: false,
    staleTime: 1000 * 60,
  })

  // Resolve which agent is active. Order: persisted choice → default →
  // first in the list. The picked id always points at an agent that
  // still exists; falls back gracefully when the persisted one was
  // archived/deleted.
  const activeAgent: Agent | undefined = useMemo(() => {
    if (!agentsList || agentsList.length === 0) return undefined
    if (persisted.agentId) {
      const hit = agentsList.find((a) => a.id === persisted.agentId)
      if (hit) return hit
    }
    if (defaultAgent && agentsList.find((a) => a.id === defaultAgent.id)) {
      return agentsList.find((a) => a.id === defaultAgent.id)
    }
    return agentsList[0]
  }, [agentsList, persisted.agentId, defaultAgent])

  const conversationId = activeAgent ? persisted.conversationByAgent?.[activeAgent.id] ?? null : null

  function setConversationForActive(cid: string | null) {
    if (!activeAgent) return
    setPersisted((prev) => {
      const next = { ...prev, conversationByAgent: { ...(prev.conversationByAgent || {}) } }
      if (cid === null) delete next.conversationByAgent![activeAgent.id]
      else next.conversationByAgent![activeAgent.id] = cid
      writeState(next)
      return next
    })
  }

  function selectAgent(id: string) {
    setPersisted((prev) => {
      const next = { ...prev, agentId: id }
      writeState(next)
      return next
    })
    setView('chat')
    setFocusBump((n) => n + 1)
  }

  function startNewConversation() {
    setConversationForActive(null)
    setView('chat')
    setFocusBump((n) => n + 1)
  }

  // When the panel opens, default the inner view to chat (history view
  // is opt-in). Conversation itself is NOT reset — the user explicitly
  // clicks + to start a new one.
  useEffect(() => {
    if (open) {
      setView('chat')
      setFocusBump((n) => n + 1)
    }
  }, [open])

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className={cn(
            // Match the command palette (⌘K) overlay: light background-
            // tinted veil + small blur. Distinct from the heavier
            // bg-black/30 we had before, which felt like a modal cut.
            'fixed inset-0 z-50 backdrop-blur-[3px] bg-background/40',
            'data-[state=open]:animate-in data-[state=closed]:animate-out',
            'data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
          )}
        />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          className={cn(
            'fixed right-0 top-0 z-50 h-full w-full sm:w-[440px] md:w-[480px] bg-background border-l shadow-xl',
            'flex flex-col outline-none',
            'data-[state=open]:animate-in data-[state=closed]:animate-out',
            'data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right',
            'duration-200',
          )}
        >
          <DialogPrimitive.Title className="sr-only">
            {t('agents.globalChat.title', 'Chat')}
          </DialogPrimitive.Title>

          <header className="flex items-center justify-between gap-2 px-3 py-2 border-b shrink-0">
            <div className="flex items-center gap-1 min-w-0 flex-1">
              {view === 'history' ? (
                <>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="-ml-1 px-1.5"
                    onClick={() => setView('chat')}
                    aria-label="Back to chat"
                  >
                    <ArrowLeft className="h-4 w-4" />
                  </Button>
                  <span className="text-sm font-medium truncate">
                    {t('agents.globalChat.history', 'Recent conversations')}
                  </span>
                </>
              ) : agentsList && agentsList.length > 1 ? (
                // Styled Radix Select — matches the rest of the app and
                // gets a proper popover with active-state styling. The
                // trigger sheds borders to fit the slim header bar.
                <Select value={activeAgent?.id ?? ''} onValueChange={selectAgent}>
                  <SelectTrigger
                    aria-label={t('agents.globalChat.selectAgent', 'Select agent')}
                    className={cn(
                      'h-8 gap-1.5 border-0 bg-transparent shadow-none focus-visible:ring-0',
                      'px-2 -ml-1 hover:bg-muted text-sm font-medium',
                      'data-[size=default]:h-8',
                    )}
                  >
                    {/* The SelectValue renders the matching SelectItem's
                        children, so the dot + name + badge live inside
                        each item. We don't add a separate dot here or
                        it'd appear twice. */}
                    <SelectValue placeholder={t('agents.globalChat.selectAgent', 'Select agent')} />
                  </SelectTrigger>
                  <SelectContent align="start" className="max-h-[60vh]">
                    {agentsList.map((a) => (
                      <SelectItem key={a.id} value={a.id}>
                        <span className="inline-flex items-center gap-2">
                          <span
                            className="inline-block h-2 w-2 rounded-full shrink-0"
                            style={{ backgroundColor: a.color }}
                            aria-hidden
                          />
                          <span>{a.name}</span>
                          {a.is_default && (
                            <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-200/70 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200">
                              {t('agents.defaultBadge', 'Default')}
                            </span>
                          )}
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <div className="inline-flex items-center gap-2 px-2 -ml-1">
                  <span
                    className="inline-block h-2 w-2 rounded-full shrink-0"
                    style={{ backgroundColor: activeAgent?.color ?? 'transparent' }}
                    aria-hidden
                  />
                  <span className="text-sm font-medium truncate">
                    {activeAgent?.name ?? t('agents.globalChat.title', 'Chat')}
                  </span>
                  {activeAgent?.is_default && (
                    <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-200/70 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200 shrink-0">
                      {t('agents.defaultBadge', 'Default')}
                    </span>
                  )}
                </div>
              )}
            </div>
            <div className="flex items-center gap-1">
              {view === 'chat' && activeAgent && (
                <>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setView('history')
                      qc.invalidateQueries({ queryKey: ['global-chat-conversations', activeAgent.id] })
                    }}
                    aria-label={t('agents.globalChat.history', 'Recent conversations')}
                    title={t('agents.globalChat.history', 'Recent conversations')}
                  >
                    <History className="h-4 w-4" />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={startNewConversation}
                    aria-label={t('agents.newConversation', 'New conversation')}
                    title={t('agents.newConversation', 'New conversation')}
                  >
                    <Plus className="h-4 w-4" />
                  </Button>
                </>
              )}
              {/* Settings — jumps to /agents (the management page).
                  Closing the panel after navigation so the user lands
                  on a clean view of the agents config. */}
              <Button
                asChild
                size="sm"
                variant="ghost"
                aria-label={t('agents.globalChat.openSettings', 'Agent settings')}
                title={t('agents.globalChat.openSettings', 'Agent settings')}
              >
                <Link to="/agents" onClick={() => onOpenChange(false)}>
                  <Settings className="h-4 w-4" />
                </Link>
              </Button>
              <DialogPrimitive.Close asChild>
                <Button size="sm" variant="ghost" aria-label="Close">
                  <X className="h-4 w-4" />
                </Button>
              </DialogPrimitive.Close>
            </div>
          </header>

          <div className="flex-1 min-h-0 flex flex-col">
            {loadingAgents && (
              <div className="flex-1 flex items-center justify-center text-muted-foreground gap-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                <span className="text-sm">{t('common.loading', 'Loading…')}</span>
              </div>
            )}
            {!loadingAgents && agentsList && agentsList.length === 0 && (
              <div className="flex-1 flex flex-col items-center justify-center text-center px-6 gap-2 text-sm text-muted-foreground">
                <span>
                  {t(
                    'agents.globalChat.empty',
                    'No agent available. Create one in the Agents page to enable the global chat.',
                  )}
                </span>
                <a href="/agents" className="underline text-foreground">
                  {t('agents.globalChat.openAgents', 'Go to Agents')}
                </a>
              </div>
            )}
            {activeAgent && view === 'chat' && (
              <ChatPanel
                // Key on agent only. Including conversationId here would
                // remount the panel mid-stream when the SSE assigns a
                // brand-new conversation an id (null → real), wiping the
                // streaming state and hiding the loading bubble for the
                // first message of a fresh chat.
                key={activeAgent.id}
                agent={activeAgent}
                conversationId={conversationId}
                onConversationCreated={(id) => setConversationForActive(id)}
                focusSignal={focusBump}
                getPageContext={() => getEffectivePageChatContext()}
              />
            )}
            {activeAgent && view === 'history' && (
              <ConversationsList
                agentId={activeAgent.id}
                activeConversationId={conversationId}
                onPick={(id) => {
                  setConversationForActive(id)
                  setView('chat')
                  setFocusBump((n) => n + 1)
                }}
              />
            )}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}

interface ConvListProps {
  agentId: string
  activeConversationId: string | null
  onPick: (id: string) => void
}

function ConversationsList({ agentId, activeConversationId, onPick }: ConvListProps) {
  const { t } = useTranslation()
  const { data, isLoading } = useQuery({
    queryKey: ['global-chat-conversations', agentId],
    queryFn: () => agents.conversations.list(agentId, 50),
    staleTime: 1000 * 10,
  })

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground gap-2">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span className="text-sm">{t('common.loading', 'Loading…')}</span>
      </div>
    )
  }
  if (!data || data.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground px-6 text-center">
        {t('agents.globalChat.noConversations', 'No conversations yet.')}
      </div>
    )
  }
  return (
    <div className="flex-1 overflow-y-auto divide-y">
      {data.map((c) => (
        <ConversationRow
          key={c.id}
          conv={c}
          isActive={c.id === activeConversationId}
          onClick={() => onPick(c.id)}
        />
      ))}
    </div>
  )
}

function ConversationRow({
  conv,
  isActive,
  onClick,
}: {
  conv: AgentConversation
  isActive: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'w-full text-left px-3 py-2.5 hover:bg-muted transition-colors flex flex-col gap-0.5',
        isActive && 'bg-muted',
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium truncate">
          {conv.title || 'Untitled'}
        </span>
        <span className="text-[11px] text-muted-foreground shrink-0 tabular-nums">
          {formatRelative(conv.updated_at)}
        </span>
      </div>
    </button>
  )
}
