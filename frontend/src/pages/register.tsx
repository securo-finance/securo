import { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/contexts/auth-context'
import { admin as adminApi } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardFooter } from '@/components/ui/card'
import { ShellLogo } from '@/components/shell-logo'
import { cn } from '@/lib/utils'
import type { AxiosError } from 'axios'

const currencies = [
  { code: 'USD', flag: '\u{1F1FA}\u{1F1F8}', symbol: '$' },
  { code: 'EUR', flag: '\u{1F1EA}\u{1F1FA}', symbol: '\u20AC' },
  { code: 'GBP', flag: '\u{1F1EC}\u{1F1E7}', symbol: '\u00A3' },
  { code: 'BRL', flag: '\u{1F1E7}\u{1F1F7}', symbol: 'R$' },
  { code: 'CAD', flag: '\u{1F1E8}\u{1F1E6}', symbol: 'C$' },
  { code: 'AUD', flag: '\u{1F1E6}\u{1F1FA}', symbol: 'A$' },
  { code: 'CHF', flag: '\u{1F1E8}\u{1F1ED}', symbol: 'Fr' },
  { code: 'ARS', flag: '\u{1F1E6}\u{1F1F7}', symbol: '$' },
  { code: 'DKK', flag: '\u{1F1E9}\u{1F1F0}', symbol: 'kr' },
  { code: 'NOK', flag: '\u{1F1F3}\u{1F1F4}', symbol: 'kr' },
  { code: 'PLN', flag: '\u{1F1F5}\u{1F1F1}', symbol: 'zł' },
  { code: 'CZK', flag: '\u{1F1E8}\u{1F1FF}', symbol: 'Kč' },
  { code: 'HUF', flag: '\u{1F1ED}\u{1F1FA}', symbol: 'Ft' },
  { code: 'RON', flag: '\u{1F1F7}\u{1F1F4}', symbol: 'lei' },
  { code: 'CRC', flag: '\u{1F1E8}\u{1F1F7}', symbol: '₡' },
  { code: 'IDR', flag: '\u{1F1EE}\u{1F1E9}', symbol: 'Rp' },
  { code: 'COP', flag: '\u{1F1E8}\u{1F1F4}', symbol: '$' },
  { code: 'CLP', flag: '\u{1F1E8}\u{1F1F1}', symbol: '$' },
] as const

export default function RegisterPage() {
  const { t, i18n } = useTranslation()
  const { register } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [currency, setCurrency] = useState('USD')
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  useEffect(() => {
    adminApi.registrationStatus().then(({ enabled }) => {
      if (!enabled) navigate('/login', { replace: true })
    }).catch(() => {})
  }, [navigate])

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
      const lang = i18n.language?.startsWith('pt') ? 'pt-BR' : 'en'
      await register(email, password, {
        currency_display: currency,
        language: lang,
      })
      navigate('/')
    } catch (err) {
      const axiosErr = err as AxiosError
      if (axiosErr?.response?.status === 429) {
        setError(t('auth.tooManyAttempts'))
      } else {
        setError(t('auth.registrationError'))
      }
    } finally {
      setIsLoading(false)
    }
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
            <div className="space-y-2 pt-1">
              <Label className="text-sm">{t('auth.currency')}</Label>
              <div className="grid grid-cols-4 gap-2">
                {currencies.map(({ code, flag, symbol }) => (
                  <button
                    key={code}
                    type="button"
                    onClick={() => setCurrency(code)}
                    className={cn(
                      'flex flex-col items-center gap-1 py-2.5 rounded-lg border transition-all',
                      currency === code
                        ? 'border-primary bg-primary/10 text-primary ring-1 ring-primary/30'
                        : 'border-border text-muted-foreground hover:border-foreground/20 hover:text-foreground'
                    )}
                  >
                    <span className="text-lg leading-none">{flag}</span>
                    <span className="text-[11px] font-bold">{code}</span>
                    <span className="text-[10px] opacity-60">{symbol}</span>
                  </button>
                ))}
              </div>
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
