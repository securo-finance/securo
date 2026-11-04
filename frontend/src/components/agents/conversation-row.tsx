import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Check, MessageSquare, Pencil, Trash2, X } from 'lucide-react'
import { toast } from 'sonner'
import { agents } from '@/lib/api'
import type { AgentConversation } from '@/lib/api'
import { formatRelative } from '@/lib/relative-time'
import { cn } from '@/lib/utils'

interface Props {
  conv: AgentConversation
  agentId: string
  active: boolean
  onPick: () => void
  onDeleted: () => void
}

export function ConversationRow({ conv, agentId, active, onPick, onDeleted }: Props) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(conv.title || '')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [editing])

  const renameMut = useMutation({
    mutationFn: (title: string) => agents.conversations.rename(conv.id, title),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agent-conversations', agentId] })
      setEditing(false)
    },
    onError: () => toast.error(t('agents.conversation.renameFailed')),
  })

  const deleteMut = useMutation({
    mutationFn: () => agents.conversations.remove(conv.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agent-conversations', agentId] })
      qc.removeQueries({ queryKey: ['agent-conv-messages', conv.id] })
      onDeleted()
      toast.success(t('agents.conversation.deleted'))
    },
    onError: () => toast.error(t('agents.conversation.deleteFailed')),
  })

  const submitRename = () => {
    const value = draft.trim()
    if (!value) {
      setDraft(conv.title || '')
      setEditing(false)
      return
    }
    if (value === conv.title) {
      setEditing(false)
      return
    }
    renameMut.mutate(value)
  }

  if (editing) {
    return (
      <div className={cn('flex items-center gap-1 px-2 py-1.5', active && 'bg-muted')}>
        <MessageSquare className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              submitRename()
            } else if (e.key === 'Escape') {
              setDraft(conv.title || '')
              setEditing(false)
            }
          }}
          className="flex-1 min-w-0 rounded-sm border bg-card px-1.5 py-0.5 text-sm"
        />
        <button
          type="button"
          onClick={submitRename}
          className="p-1 rounded hover:bg-background text-emerald-600"
          title={t('agents.conversation.save')}
        >
          <Check className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={() => {
            setDraft(conv.title || '')
            setEditing(false)
          }}
          className="p-1 rounded hover:bg-background text-muted-foreground"
          title={t('agents.conversation.cancel')}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    )
  }

  return (
    <div
      className={cn(
        'group flex items-center px-3 py-2 text-sm hover:bg-muted',
        active && 'bg-muted',
      )}
    >
      <button
        type="button"
        onClick={onPick}
        className="flex-1 min-w-0 flex items-center gap-2 text-left"
      >
        <MessageSquare className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="flex-1 min-w-0 truncate">{conv.title || t('agents.detail.untitled')}</span>
        <span className="text-[11px] text-muted-foreground shrink-0 tabular-nums group-hover:opacity-0 transition-opacity">
          {formatRelative(conv.updated_at)}
        </span>
      </button>
      <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            setEditing(true)
          }}
          className="p-1 rounded hover:bg-background text-muted-foreground"
          title={t('agents.conversation.rename')}
        >
          <Pencil className="h-3 w-3" />
        </button>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            if (confirm(t('agents.conversation.deleteConfirm', { name: conv.title || t('agents.detail.untitled') }))) {
              deleteMut.mutate()
            }
          }}
          className="p-1 rounded hover:bg-background text-rose-500"
          title={t('agents.conversation.delete')}
          disabled={deleteMut.isPending}
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>
    </div>
  )
}
