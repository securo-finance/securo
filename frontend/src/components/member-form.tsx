import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Link2 } from 'lucide-react'
import { users as usersApi } from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface MemberFormProps {
  name: string
  onChangeName: (value: string) => void
  email: string
  onChangeEmail: (value: string) => void
  linkedUserId: string | null
  onChangeLinkedUserId: (value: string | null) => void
}

export function MemberForm({
  name,
  onChangeName,
  email,
  onChangeEmail,
  linkedUserId,
  onChangeLinkedUserId,
}: MemberFormProps) {
  const { t } = useTranslation()
  const { user } = useAuth()

  // Directory of all Securo users on the instance
  const { data: userDirectory } = useQuery({
    queryKey: ['users', 'directory'],
    queryFn: () => usersApi.directory(),
    staleTime: 60_000,
  })

  // Resolve a typed email to an existing Securo user
  const trimmedEmail = email.trim()
  const { data: lookupResult } = useQuery({
    queryKey: ['users', 'lookup', trimmedEmail.toLowerCase()],
    queryFn: () => usersApi.lookupByEmail(trimmedEmail),
    enabled: trimmedEmail.length >= 3 && trimmedEmail.includes('@'),
    staleTime: 60_000,
    retry: false,
  })

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>{t('splitGroups.linkedUser')}</Label>
        <select
          className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background h-9 focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
          value={linkedUserId ?? ''}
          onChange={(e) => {
            const id = e.target.value || null
            onChangeLinkedUserId(id)
            if (id) {
              const picked = userDirectory?.find((u) => u.id === id)
              if (picked) {
                onChangeEmail(picked.email)
                onChangeName(picked.email.split('@')[0])
              }
            }
          }}
        >
          <option value="">{t('splitGroups.linkedUserNone')}</option>
          {(userDirectory ?? []).map((u) => (
            <option key={u.id} value={u.id}>
              {u.email}
              {u.id === user?.id ? ` (${t('splitGroups.you')})` : ''}
            </option>
          ))}
        </select>
        <p className="text-xs text-muted-foreground">
          {t('splitGroups.linkedUserHint')}
        </p>
      </div>

      <div className="space-y-2">
        <Label>{t('splitGroups.memberName')}</Label>
        <Input
          value={name}
          onChange={(e) => onChangeName(e.target.value)}
          disabled={linkedUserId !== null}
        />
      </div>

      <div className="space-y-2">
        <Label>{t('splitGroups.memberEmail')}</Label>
        <Input
          type="email"
          value={email}
          onChange={(e) => onChangeEmail(e.target.value)}
          disabled={linkedUserId !== null}
        />
        {linkedUserId !== null ? (
          <p className="text-xs text-emerald-600 inline-flex items-center gap-1">
            <Link2 size={11} />
            {t('splitGroups.willLinkToUser', { email })}
          </p>
        ) : lookupResult ? (
          <p className="text-xs text-emerald-600 inline-flex items-center gap-1">
            <Link2 size={11} />
            {t('splitGroups.willLinkToUser', { email: lookupResult.email })}
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">
            {t('splitGroups.memberEmailHint')}
          </p>
        )}
      </div>
    </div>
  )
}
