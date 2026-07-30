import { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/contexts/auth-context'
import { setup, auth as authApi, admin as adminApi } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardFooter } from '@/components/ui/card'
import { ShellLogo } from '@/components/shell-logo'
import type { AxiosError } from 'axios'
import { isServerUnreachable } from '@/lib/auth-errors'
import { useTheme } from 'next-themes'
import { setThemeBasedOnSystem } from '@/lib/theme-utils'
import { isPasskeySupported, passkeyFailure, startPasskeyAuthentication } from '@/lib/webauthn'
import type { PasskeyFailure } from '@/lib/webauthn'

const PASSKEY_LOGIN_FAILURE_KEYS: Record<PasskeyFailure, string> = {
  cancelled: 'auth.passkeyCancelled',
  domain: 'auth.passkeyDomainError',
  mismatch: 'auth.passkeyDomainMismatch',
  ip: 'auth.passkeyIpAddress',
  insecure: 'auth.passkeyInsecureContext',
  unsupported: 'auth.passkeyUnsupported',
  duplicate: 'auth.passkeyLoginError',
  unknown: 'auth.passkeyLoginError',
}

export default function LoginPage() {
  const { t } = useTranslation()
  const { login, verify2fa, loginWithToken, token } = useAuth()
  const navigate = useNavigate()
  const { resolvedTheme } = useTheme()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [isPasskeyLoading, setIsPasskeyLoading] = useState(false)
  const [passkeySupported, setPasskeySupported] = useState(false)
  const [registrationEnabled, setRegistrationEnabled] = useState(true)
  const [oidcConfig, setOidcConfig] = useState<{ enabled: boolean; provider_name: string }>({ enabled: false, provider_name: 'OIDC' })

  // 2FA state
  const [requires2fa, setRequires2fa] = useState(false)
  const [tempToken, setTempToken] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [available2faMethods, setAvailable2faMethods] = useState<Array<'totp' | 'passkey'>>(['totp'])
  const [selected2faMethod, setSelected2faMethod] = useState<'totp' | 'passkey'>('totp')

  useEffect(() => {
    setPasskeySupported(isPasskeySupported())
    if (token) {
      navigate('/', { replace: true })
      return
    }
    setup.status().then(({ has_users }) => {
      if (!has_users) {
        navigate('/setup', { replace: true })
      }
    }).catch(() => {})
    adminApi.registrationStatus().then(({ enabled }) => {
      setRegistrationEnabled(enabled)
    }).catch(() => {})
    authApi.oidcConfig().then(setOidcConfig).catch(() => {})
    adminApi.defaultColors().then(({ light, dark }) => {
      setThemeBasedOnSystem(light, dark, resolvedTheme)
    }).catch(() => {})
  }, [navigate, token, resolvedTheme])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setIsLoading(true)
    try {
      const result = await login(email, password)
      if (result.requires_2fa) {
        const methods: Array<'totp' | 'passkey'> = result.available_methods?.length ? result.available_methods : ['totp']
        setRequires2fa(true)
        setTempToken(result.temp_token ?? '')
        setAvailable2faMethods(methods)
        setSelected2faMethod(methods.includes('passkey') ? 'passkey' : methods[0])
      } else {
        navigate('/')
      }
    } catch (err) {
      const axiosErr = err as AxiosError
      if (isServerUnreachable(err)) {
        setError(t('auth.serverError'))
      } else if (axiosErr?.response?.status === 429) {
        setError(t('auth.tooManyAttempts'))
      } else {
        setError(t('auth.invalidCredentials'))
      }
    } finally {
      setIsLoading(false)
    }
  }

  const handleOIDCLogin = () => {
    window.location.href = '/api/auth/oidc/login'
  }

  const handlePasskeyLogin = async () => {
    setError('')
    setIsPasskeyLoading(true)
    try {
      const trimmedEmail = email.trim()
      const options = await authApi.passkeyAuthenticationOptions(trimmedEmail || undefined)
      const credential = await startPasskeyAuthentication(options.options)
      const result = await authApi.verifyPasskeyAuthentication(options.challenge_id, credential)
      loginWithToken(result.access_token)
      navigate('/')
    } catch (err) {
      const axiosErr = err as AxiosError
      if (axiosErr?.response?.status === 429) {
        setError(t('auth.tooManyAttempts'))
      } else {
        setError(t(PASSKEY_LOGIN_FAILURE_KEYS[passkeyFailure(err)]))
      }
    } finally {
      setIsPasskeyLoading(false)
    }
  }

  const handleVerify2fa = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setIsLoading(true)
    try {
      await verify2fa(tempToken, totpCode)
      navigate('/')
    } catch (err) {
      const axiosErr = err as AxiosError
      if (isServerUnreachable(err)) {
        setError(t('auth.serverError'))
      } else if (axiosErr?.response?.status === 401) {
        setError(t('auth.invalidCredentials'))
        // Token expired, go back to login
        resetSecondFactor()
      } else {
        setError(t('auth.invalid2faCode'))
      }
    } finally {
      setIsLoading(false)
    }
  }

  function resetSecondFactor() {
    setRequires2fa(false)
    setTempToken('')
    setTotpCode('')
    setAvailable2faMethods(['totp'])
    setSelected2faMethod('totp')
    setError('')
  }

  const handlePasskeySecondFactor = async () => {
    setError('')
    setIsPasskeyLoading(true)
    try {
      const options = await authApi.passkeySecondFactorOptions(tempToken)
      const credential = await startPasskeyAuthentication(options.options)
      const result = await authApi.verifyPasskeySecondFactor(tempToken, options.challenge_id, credential)
      loginWithToken(result.access_token)
      navigate('/')
    } catch (err) {
      const axiosErr = err as AxiosError
      const domErr = err as { name?: string }
      if (domErr?.name === 'NotAllowedError') {
        setError(t('auth.passkeyCancelled'))
      } else if (axiosErr?.response?.status === 401) {
        setError(t('auth.invalidCredentials'))
        resetSecondFactor()
      } else if (axiosErr?.response?.status === 429) {
        setError(t('auth.tooManyAttempts'))
      } else {
        setError(t('auth.passkeyLoginError'))
      }
    } finally {
      setIsPasskeyLoading(false)
    }
  }

  if (requires2fa) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen bg-background px-4">
        <Card className="w-full max-w-[380px] shadow-sm">
          <form onSubmit={handleVerify2fa}>
            <div className="flex flex-col items-center pt-8 pb-2 px-8">
              <div className="w-11 h-11 rounded-xl bg-primary/10 flex items-center justify-center mb-4">
                <ShellLogo size={22} className="text-primary" />
              </div>
              <h1 className="text-xl font-semibold tracking-tight">
                {selected2faMethod === 'passkey' ? t('auth.passkeySecondFactorTitle') : t('auth.twoFactorTitle')}
              </h1>
              <p className="text-sm text-muted-foreground mt-1 text-center">
                {selected2faMethod === 'passkey'
                  ? t('auth.passkeySecondFactorDescription')
                  : t('auth.twoFactorDescription')}
              </p>
            </div>
            <CardContent className="space-y-4 px-8 pt-4">
              {error && (
                <div className="p-3 text-sm text-destructive bg-destructive/10 rounded-lg">
                  {error}
                </div>
              )}
              {available2faMethods.length > 1 && (
                <div className="grid grid-cols-2 gap-2">
                  {available2faMethods.includes('passkey') && (
                    <Button
                      type="button"
                      variant={selected2faMethod === 'passkey' ? 'default' : 'outline'}
                      onClick={() => setSelected2faMethod('passkey')}
                    >
                      {t('auth.passkeyMethod')}
                    </Button>
                  )}
                  {available2faMethods.includes('totp') && (
                    <Button
                      type="button"
                      variant={selected2faMethod === 'totp' ? 'default' : 'outline'}
                      onClick={() => setSelected2faMethod('totp')}
                    >
                      {t('auth.totpMethod')}
                    </Button>
                  )}
                </div>
              )}
              {selected2faMethod === 'totp' && (
                <div className="space-y-1.5">
                  <Label htmlFor="totp-code" className="text-sm">{t('auth.twoFactor')}</Label>
                  <Input
                    id="totp-code"
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    value={totpCode}
                    onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                    placeholder="000000"
                    className="text-center text-lg tracking-[0.3em] font-mono"
                    maxLength={6}
                    required
                    autoFocus
                  />
                </div>
              )}
              {selected2faMethod === 'passkey' && (
                <p className="text-sm text-muted-foreground text-center">
                  {t('auth.passkeySecondFactorPrompt')}
                </p>
              )}
            </CardContent>
            <CardFooter className="flex flex-col gap-4 px-8 pb-8 pt-2">
              {selected2faMethod === 'totp' ? (
                <Button type="submit" className="w-full" disabled={isLoading || totpCode.length !== 6}>
                  {isLoading ? t('common.loading') : t('auth.verify')}
                </Button>
              ) : (
                <Button
                  type="button"
                  className="w-full"
                  onClick={handlePasskeySecondFactor}
                  disabled={isLoading || isPasskeyLoading || !passkeySupported}
                >
                  {isPasskeyLoading ? t('common.loading') : t('auth.usePasskeySecondFactor')}
                </Button>
              )}
              <button
                type="button"
                onClick={resetSecondFactor}
                className="text-sm text-muted-foreground hover:text-foreground"
              >
                {t('auth.login')}
              </button>
            </CardFooter>
          </form>
        </Card>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background px-4">
      <Card className="w-full max-w-[380px] shadow-sm">
        <form onSubmit={handleSubmit}>
          <div className="flex flex-col items-center pt-8 pb-2 px-8">
            <div className="w-11 h-11 rounded-xl bg-primary/10 flex items-center justify-center mb-4">
              <ShellLogo size={22} className="text-primary" />
            </div>
            <h1 className="text-xl font-semibold tracking-tight">{t('auth.login')}</h1>
            <p className="text-sm text-muted-foreground mt-1">{t('auth.loginDescription')}</p>
          </div>
          <CardContent className="space-y-4 px-8 pt-4">
            {error && (
              <div className="p-3 text-sm text-destructive bg-destructive/10 rounded-lg">
                {error}
              </div>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="email" className="text-sm">{t('auth.email')}</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password" className="text-sm">{t('auth.password')}</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
          </CardContent>
          <CardFooter className="flex flex-col gap-4 px-8 pb-8 pt-2">
            <Button type="submit" className="w-full" disabled={isLoading || isPasskeyLoading}>
              {isLoading ? t('common.loading') : t('auth.login')}
            </Button>
            {(passkeySupported || oidcConfig.enabled) && (
              <div className="flex items-center gap-3 w-full">
                <div className="h-px flex-1 bg-border" />
                <span className="text-xs text-muted-foreground">{t('auth.or')}</span>
                <div className="h-px flex-1 bg-border" />
              </div>
            )}
            {passkeySupported && (
              <Button
                type="button"
                variant="outline"
                className="w-full"
                onClick={handlePasskeyLogin}
                disabled={isLoading || isPasskeyLoading}
              >
                {isPasskeyLoading ? t('common.loading') : t('auth.loginWithPasskey')}
              </Button>
            )}
            {oidcConfig.enabled && (
              <Button type="button" variant="outline" className="w-full" onClick={handleOIDCLogin}>
                {t('auth.loginWithProvider', { provider: oidcConfig.provider_name })}
              </Button>
            )}
            {registrationEnabled && (
              <p className="text-sm text-muted-foreground">
                {t('auth.noAccount')}{' '}
                <Link to="/register" className="text-primary font-medium hover:underline">
                  {t('auth.register')}
                </Link>
              </p>
            )}
          </CardFooter>
        </form>
      </Card>
    </div>
  )
}
