import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Edit2, Trash2, Plus, MessageSquare } from 'lucide-react'
import { ConversationRow } from '@/components/agents/conversation-row'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { agents } from '@/lib/api'
import { AgentFormDialog } from '@/components/agents/agent-form-dialog'
import { ChatPanel } from '@/components/agents/chat-panel'
import { KnowledgeSection } from '@/components/agents/knowledge-section'
import { ToolsSection } from '@/components/agents/tools-section'
import { useWorkspace } from '@/contexts/workspace-context'

export default function AgentDetailPage() {
  const { t } = useTranslation()
  const { id = '' } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { canWrite } = useWorkspace()
  const [editOpen, setEditOpen] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(null)
  // Increments on every sidebar click so the chat input refocuses even
  // when the conversation id didn't actually change (e.g. clicking "+"
  // while already on a null conversation).
  const [focusSignal, setFocusSignal] = useState(0)
  const pickConversation = (id: string | null) => {
    setConversationId(id)
    setFocusSignal((n) => n + 1)
  }

  const { data: info } = useQuery({ queryKey: ['agents-info'], queryFn: () => agents.info() })
  const { data: agent, isLoading } = useQuery({
    queryKey: ['agent', id],
    queryFn: () => agents.get(id),
    enabled: !!id,
  })
  const { data: conversations } = useQuery({
    queryKey: ['agent-conversations', id],
    queryFn: () => agents.conversations.list(id),
    enabled: !!id,
  })
  const { data: connections } = useQuery({
    queryKey: ['agent-connections'],
    queryFn: () => agents.connections.list(),
  })
  const linkedConnection = agent?.connection_id
    ? connections?.find((c) => c.id === agent.connection_id)
    : undefined
  const providerLabel =
    linkedConnection?.name ||
    (linkedConnection ? linkedConnection.kind : null) ||
    agent?.provider ||
    t('agents.instanceDefault')
  const modelLabel = agent?.model || linkedConnection?.default_model || '—'

  const removeMut = useMutation({
    mutationFn: () => agents.remove(id),
    onSuccess: () => {
      toast.success(t('agents.detail.deleted'))
      qc.invalidateQueries({ queryKey: ['agents'] })
      navigate('/agents')
    },
  })

  if (isLoading) return <div className="p-6 text-sm text-muted-foreground">{t('agents.loading')}</div>
  if (!agent) return <div className="p-6 text-sm text-muted-foreground">{t('agents.detail.notFound')}</div>

  return (
    // Height has to account for the AppLayout's chrome around <Outlet />:
    //   - mobile sticky header  = h-14 (3.5rem)
    //   - p-6 wrapper top+bottom = 3rem
    // Without subtracting both, the input bar gets pushed past the viewport
    // and the user has to scroll the whole page — which they don't want.
    // overflow-hidden ensures only the messages div inside ChatPanel scrolls.
    <div className="flex flex-col h-[calc(100dvh-6.5rem)] lg:h-[calc(100dvh-3rem)] overflow-hidden">
      <div className="border-b px-4 py-3 flex items-center gap-3">
        <Button size="icon" variant="ghost" onClick={() => navigate('/agents')} title={t('common.back')} aria-label={t('common.back')}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div
          className="h-9 w-9 rounded-md flex items-center justify-center text-white shrink-0"
          style={{ backgroundColor: agent.color }}
        >
          <span className="font-semibold">{agent.name[0]}</span>
        </div>
        <div className="min-w-0 flex-1">
          <div className="font-semibold truncate">{agent.name}</div>
          <div className="text-xs text-muted-foreground truncate">
            {providerLabel} · {modelLabel} · temp {agent.temperature}
          </div>
        </div>
        {canWrite && (
          <>
            <Button size="sm" variant="outline" onClick={() => setEditOpen(true)}>
              <Edit2 className="h-3.5 w-3.5 mr-1.5" /> {t('agents.detail.edit')}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                if (confirm(t('agents.detail.deleteConfirm'))) {
                  removeMut.mutate()
                }
              }}
            >
              <Trash2 className="h-3.5 w-3.5 mr-1.5" /> {t('agents.detail.delete')}
            </Button>
          </>
        )}
      </div>

      <Tabs defaultValue="chat" className="flex-1 flex flex-col min-h-0">
        <div className="px-4 pt-3">
          <TabsList>
            <TabsTrigger value="chat">{t('agents.detail.tabs.chat')}</TabsTrigger>
            <TabsTrigger value="knowledge">{t('agents.detail.tabs.knowledge')}</TabsTrigger>
            <TabsTrigger value="tools">{t('agents.detail.tabs.tools')}</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="chat" className="flex-1 flex min-h-0 mt-3">
          <aside className="hidden md:flex md:w-64 border-r flex-col min-h-0">
            <div className="px-3 pt-2 pb-1 flex items-center justify-between shrink-0">
              <span className="text-xs uppercase tracking-wider text-muted-foreground">{t('agents.detail.conversations')}</span>
              <Button size="icon" variant="ghost" onClick={() => pickConversation(null)} title={t('agents.detail.newConversation')}>
                <Plus className="h-4 w-4" />
              </Button>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto">
              {/* Placeholder row for an unsaved new conversation — appears
                  when conversationId is null so the user can see what
                  they're working on. Replaced by the real entry once the
                  first message is sent. */}
              {conversationId === null && (
                <button
                  onClick={() => pickConversation(null)}
                  className="w-full text-left px-3 py-2 text-sm bg-muted flex items-center gap-2 truncate text-muted-foreground italic"
                >
                  <MessageSquare className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">{t('agents.detail.newConversation')}</span>
                </button>
              )}
              {(conversations ?? []).map((c) => (
                <ConversationRow
                  key={c.id}
                  conv={c}
                  agentId={id}
                  active={conversationId === c.id}
                  onPick={() => pickConversation(c.id)}
                  onDeleted={() => {
                    if (conversationId === c.id) pickConversation(null)
                  }}
                />
              ))}
            </div>
          </aside>
          <div className="flex-1 min-w-0">
            <ChatPanel
              agent={agent}
              conversationId={conversationId}
              focusSignal={focusSignal}
              onConversationCreated={(cid) => {
                setConversationId(cid)
                qc.invalidateQueries({ queryKey: ['agent-conversations', id] })
              }}
            />
          </div>
        </TabsContent>

        <TabsContent value="knowledge" className="flex-1 overflow-y-auto px-4 py-3">
          <KnowledgeSection agentId={id} />
        </TabsContent>

        <TabsContent value="tools" className="flex-1 overflow-y-auto px-4 py-3">
          <ToolsSection agentId={id} />
        </TabsContent>
      </Tabs>

      <AgentFormDialog open={editOpen} onOpenChange={setEditOpen} agent={agent} providers={info?.providers ?? []} />
    </div>
  )
}
