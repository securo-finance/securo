import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { auth as authApi } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardFooter } from '@/components/ui/card'
import { ShellLogo } from '@/components/shell-logo'
import { isServerUnreachable } from '@/lib/auth-errors'
import type { AxiosError } from 'axios'

export default function ForgotPasswordPage() {
  const { t } = useTranslation()
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const [sentTo, setSentTo] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setIsLoading(true)
    try {
      await authApi.forgotPassword(email.trim())
      setSentTo(email.trim())
    } catch (err) {
      const axiosErr = err as AxiosError<{ detail?: unknown }>
      // The backend's own 503 carries a detail: email is off here. A 503
      // without one is a proxy in front of a dead service.
      const switchOff =
        axiosErr?.response?.status === 503 &&
        typeof axiosErr.response.data?.detail === 'string'
      if (switchOff) {
        setError(t('auth.resetUnavailable'))
      } else if (isServerUnreachable(err)) {
        setError(t('auth.serverError'))
      } else if (axiosErr?.response?.status === 429) {
        setError(t('auth.tooManyAttempts'))
      } else {
        setError(t('auth.resetRequestError'))
      }
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background px-4">
      <Card className="w-full max-w-[400px] shadow-sm">
        <div className="flex flex-col items-center pt-8 pb-2 px-8">
          <div className="w-11 h-11 rounded-xl bg-primary/10 flex items-center justify-center mb-4">
            <ShellLogo size={22} className="text-primary" />
          </div>
          <h1 className="text-xl font-semibold tracking-tight">{t('auth.forgotPassword')}</h1>
          <p className="text-sm text-muted-foreground mt-1 text-center">
            {t('auth.forgotPasswordDescription')}
          </p>
        </div>
        {sentTo ? (
          <CardContent className="px-8 pt-4">
            <div role="status" className="p-3 text-sm bg-muted rounded-lg space-y-1">
              <p className="font-medium">{t('auth.resetSentTitle')}</p>
              <p className="text-muted-foreground">{t('auth.resetSentBody', { email: sentTo })}</p>
            </div>
          </CardContent>
        ) : (
          <form onSubmit={handleSubmit}>
            <CardContent className="space-y-4 px-8 pt-4">
              {error && (
                <div role="alert" className="p-3 text-sm text-destructive bg-destructive/10 rounded-lg">
                  {error}
                </div>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="email" className="text-sm">{t('auth.email')}</Label>
                <Input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value)
                    setError('')
                  }}
                  placeholder="you@example.com"
                  autoComplete="email"
                  required
                  autoFocus
                />
              </div>
            </CardContent>
            <CardFooter className="flex flex-col gap-4 px-8 pb-8 pt-2">
              <Button type="submit" className="w-full" disabled={isLoading}>
                {isLoading ? t('common.loading') : t('auth.sendResetLink')}
              </Button>
            </CardFooter>
          </form>
        )}
        <CardFooter className="px-8 pb-8 pt-0">
          <p className="text-sm text-muted-foreground">
            <Link to="/login" className="text-primary font-medium hover:underline">
              {t('auth.login')}
            </Link>
          </p>
        </CardFooter>
      </Card>
    </div>
  )
}
