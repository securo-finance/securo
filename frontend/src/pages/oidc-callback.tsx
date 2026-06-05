import { useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '@/contexts/auth-context'
import { Card, CardContent } from '@/components/ui/card'
import { ShellLogo } from '@/components/shell-logo'

export default function OIDCCallbackPage() {
  const navigate = useNavigate()
  const { loginWithToken } = useAuth()
  const accessToken = useMemo(() => {
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ''))
    return params.get('access_token')
  }, [])

  useEffect(() => {
    if (!accessToken) return
    loginWithToken(accessToken)
    window.history.replaceState(null, '', '/auth/oidc/callback')
    navigate('/', { replace: true })
  }, [accessToken, loginWithToken, navigate])

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background px-4">
      <Card className="w-full max-w-[380px] shadow-sm">
        <CardContent className="flex flex-col items-center gap-4 px-8 py-8 text-center">
          <div className="w-11 h-11 rounded-xl bg-primary/10 flex items-center justify-center">
            <ShellLogo size={22} className="text-primary" />
          </div>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Signing you in…</h1>
            <p className="text-sm text-muted-foreground mt-1">
              {accessToken ? 'Completing secure OIDC login.' : 'OIDC login did not return an access token.'}
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
