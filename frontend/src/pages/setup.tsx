import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useTheme } from 'next-themes'
import { setup } from '@/lib/api'
import { resolveSupportedLang } from '@/lib/i18n'
import { useAuth } from '@/contexts/auth-context'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardFooter } from '@/components/ui/card'
import { CurrencySelect } from '@/components/currency-select'
import { AuthBrandPanel } from '@/components/auth-brand-panel'
import { cn } from '@/lib/utils'
import { Sun, Moon, Globe } from 'lucide-react'
import { ShellLogo } from '@/components/shell-logo'

export default function SetupPage() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { loginWithToken, token } = useAuth()
  const { theme, setTheme } = useTheme()
  const currentLang = resolveSupportedLang(i18n.resolvedLanguage ?? i18n.language)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [currency, setCurrency] = useState('USD')
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    if (token) {
      navigate('/', { replace: true })
      return
    }
    setup.status().then(({ has_users }) => {
      if (has_users) {
        navigate('/login', { replace: true })
      } else {
        setChecking(false)
      }
    }).catch(() => {
      setChecking(false)
    })
  }, [navigate, token])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (password !== confirmPassword) {
      setError(t('setup.passwordMismatch'))
      return
    }

    setIsLoading(true)
    try {
      const { access_token } = await setup.createAdmin(email, password, currency, name, currentLang)
      localStorage.removeItem('onboarding_completed')
      loginWithToken(access_token)
      navigate('/')
    } catch {
      setError(t('setup.error'))
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
    <div className="min-h-screen lg:grid lg:grid-cols-2">
      <AuthBrandPanel />
      <div className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
      <Card className="w-full max-w-[400px] border-border/60 shadow-sm">
        <form onSubmit={handleSubmit}>
          <div className="flex flex-col items-center pt-8 pb-2 px-8">
            <div className="w-11 h-11 rounded-xl bg-primary/10 flex items-center justify-center mb-4 lg:hidden">
              <ShellLogo size={22} className="text-primary" />
            </div>
            <h1 className="text-xl font-semibold tracking-tight">{t('setup.title')}</h1>
            <p className="text-sm text-muted-foreground mt-1">{t('setup.description')}</p>
          </div>
          <CardContent className="space-y-4 px-8 pt-4">
            {error && (
              <div className="p-3 text-sm text-destructive bg-destructive/10 rounded-lg">
                {error}
              </div>
            )}
            <div className="flex items-center justify-between gap-4">
              <div className="space-y-1.5">
                <Label className="text-sm flex items-center gap-1.5">
                  <Globe size={14} />
                  {t('setup.language')}
                </Label>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => i18n.changeLanguage('en')}
                    className={cn(
                      'px-2.5 py-1 rounded text-[11px] font-semibold transition-colors',
                      currentLang === 'en'
                        ? 'bg-primary/15 text-primary'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    EN
                  </button>
                  <button
                    type="button"
                    onClick={() => i18n.changeLanguage('es')}
                    className={cn(
                      'px-2.5 py-1 rounded text-[11px] font-semibold transition-colors',
                      currentLang === 'es'
                        ? 'bg-primary/15 text-primary'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    ES
                  </button>
                  <button
                    type="button"
                    onClick={() => i18n.changeLanguage('pl')}
                    className={cn(
                      'px-2.5 py-1 rounded text-[11px] font-semibold transition-colors',
                      currentLang === 'pl'
                        ? 'bg-primary/15 text-primary'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    PL
                  </button>
                  <button
                    type="button"
                    onClick={() => i18n.changeLanguage('it')}
                    className={cn(
                      'px-2.5 py-1 rounded text-[11px] font-semibold transition-colors',
                      currentLang === 'it'
                        ? 'bg-primary/15 text-primary'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    IT
                  </button>
                  <button
                    type="button"
                    onClick={() => i18n.changeLanguage('pt-BR')}
                    className={cn(
                      'px-2.5 py-1 rounded text-[11px] font-semibold transition-colors',
                      currentLang === 'pt-BR'
                        ? 'bg-primary/15 text-primary'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    PT
                  </button>
                </div>
              </div>
              <div className="space-y-1.5">
                <Label className="text-sm">{t('setup.theme')}</Label>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setTheme('light')}
                    className={cn(
                      'p-1.5 rounded transition-colors',
                      theme === 'light'
                        ? 'bg-primary/15 text-primary'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    <Sun size={14} />
                  </button>
                  <button
                    type="button"
                    onClick={() => setTheme('dark')}
                    className={cn(
                      'p-1.5 rounded transition-colors',
                      theme === 'dark'
                        ? 'bg-primary/15 text-primary'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    <Moon size={14} />
                  </button>
                </div>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="name" className="text-sm">{t('setup.name')}</Label>
              <Input
                id="name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}

                placeholder={t('setup.namePlaceholder')}
              />
            </div>
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
              <Label htmlFor="confirmPassword" className="text-sm">{t('setup.confirmPassword')}</Label>
              <Input
                id="confirmPassword"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}

                required
                minLength={8}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="currency" className="text-sm">{t('setup.currency')}</Label>
              <CurrencySelect id="currency" value={currency} onChange={setCurrency} />
            </div>
          </CardContent>
          <CardFooter className="px-8 pb-8 pt-2">
            <Button type="submit" className="w-full" disabled={isLoading}>
              {isLoading ? t('setup.creating') : t('setup.createAdmin')}
            </Button>
          </CardFooter>
        </form>
      </Card>
      </div>
    </div>
  )
}
