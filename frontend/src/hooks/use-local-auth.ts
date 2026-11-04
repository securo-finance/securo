import { useQuery } from '@tanstack/react-query'
import { auth as authApi } from '@/lib/api'
import { resolveLocalAuthEnabled } from '@/lib/auth-config-utils'

/**
 * Whether the screens that expose local credential controls (change password,
 * 2FA, passkeys, password-backed invites) should render them.
 *
 * `retry` and `networkMode` are pinned here on purpose. With the defaults a
 * failing request does not settle on `isError`: the retry can be parked, so the
 * query sits in `pending` and the controls stay hidden for as long as that
 * lasts. Failing once and settling immediately is what makes the fallback in
 * `resolveLocalAuthEnabled` actually reachable. The backend stays authoritative
 * either way and still rejects local auth in OIDC-only mode.
 */
export function useLocalAuthEnabled(): boolean {
  const { data, isError } = useQuery({
    queryKey: ['auth', 'oidc-config'],
    queryFn: authApi.oidcConfig,
    staleTime: 60_000,
    retry: false,
    networkMode: 'always',
  })
  return resolveLocalAuthEnabled(data, isError)
}
