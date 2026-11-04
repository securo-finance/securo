import { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useTheme } from 'next-themes'
import { useAuth } from '@/contexts/auth-context'
import { admin as adminApi, auth as authApi } from '@/lib/api'
import { resolveSupportedLang } from '@/lib/i18n'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardFooter } from '@/components/ui/card'
import { CurrencySelect } from '@/components/currency-select'
import { ShellLogo } from '@/components/shell-logo'
import { setThemeBasedOnSystem } from '@/lib/theme-utils'
import { isServerUnreachable } from '@/lib/auth-errors'
import type { AxiosError } from 'axios'

export default function RegisterPage() {
  const { t, i18n } = useTranslation()
  const { register } = useAuth()
  const navigate = useNavigate()
  const { resolvedTheme } = useTheme()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [currency, setCurrency] = useState('USD')
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    let active = true
    Promise.all([
      adminApi.registrationStatus(),
      authApi.oidcConfig().catch(() => null),
    ]).then(([registration, authConfig]) => {
      if (!active) return
      if (!registration.enabled || authConfig?.local_auth_enabled === false) {
        navigate('/login', { replace: true })
        return
      }
      setChecking(false)
    }).catch(() => {
      if (active) setChecking(false)
    })
    adminApi.defaultColors().then(({ light, dark }) => {
      setThemeBasedOnSystem(light, dark, resolvedTheme)
    }).catch(() => {})
    return () => {
      active = false
    }
  }, [navigate, resolvedTheme])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (password !== confirmPassword) {
      setError(t('auth.passwordMismatch'))
      return
    }

    if (password.length < 8) {
      setError(t('auth.passwordTooShort'))
      return
    }

    setIsLoading(true)
    try {
      const lang = resolveSupportedLang(i18n.resolvedLanguage ?? i18n.language)
      await register(email, password, {
        currency_display: currency,
        language: lang,
      })
      navigate('/')
    } catch (err) {
      const axiosErr = err as AxiosError
      if (isServerUnreachable(err)) {
        setError(t('auth.serverError'))
      } else if (axiosErr?.response?.status === 429) {
        setError(t('auth.tooManyAttempts'))
      } else {
        setError(t('auth.registrationError'))
      }
    } finally {
      setIsLoading(false)
    }
  }

  if (checking) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background px-4">
      <Card className="w-full max-w-[400px] shadow-sm">
        <form onSubmit={handleSubmit}>
          <div className="flex flex-col items-center pt-8 pb-2 px-8">
            <div className="w-11 h-11 rounded-xl bg-primary/10 flex items-center justify-center mb-4">
              <ShellLogo size={22} className="text-primary" />
            </div>
            <h1 className="text-xl font-semibold tracking-tight">{t('auth.register')}</h1>
            <p className="text-sm text-muted-foreground mt-1">{t('auth.registerDescription')}</p>
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
                minLength={8}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="confirmPassword" className="text-sm">{t('auth.confirmPassword')}</Label>
              <Input
                id="confirmPassword"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="currency" className="text-sm">{t('auth.currency')}</Label>
              <CurrencySelect id="currency" value={currency} onChange={setCurrency} />
            </div>
          </CardContent>
          <CardFooter className="flex flex-col gap-4 px-8 pb-8 pt-2">
            <Button type="submit" className="w-full" disabled={isLoading}>
              {isLoading ? t('common.loading') : t('auth.register')}
            </Button>
            <p className="text-sm text-muted-foreground">
              {t('auth.hasAccount')}{' '}
              <Link to="/login" className="text-primary font-medium hover:underline">
                {t('auth.login')}
              </Link>
            </p>
          </CardFooter>
        </form>
      </Card>
    </div>
  )
}
