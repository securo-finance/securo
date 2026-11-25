import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { auth as authApi } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardFooter } from '@/components/ui/card'
import { ShellLogo } from '@/components/shell-logo'
import { isServerUnreachable } from '@/lib/auth-errors'
import type { AxiosError } from 'axios'

type ErrorDetail = string | { code?: string; reason?: string } | undefined

/** The token travels in the link the recovery email carries. */
export default function ResetPasswordPage() {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') ?? ''
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)
  const [isLoading, setIsLoading] = useState(false)

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
    // The backend refuses more than 72 characters (the bcrypt limit), so say
    // it here instead of letting the round trip come back as "not accepted".
    if (password.length > 72) {
      setError(t('auth.passwordTooLong'))
      return
    }

    setIsLoading(true)
    try {
      await authApi.resetPassword(token, password)
      setDone(true)
    } catch (err) {
      const axiosErr = err as AxiosError<{ detail?: ErrorDetail }>
      const detail = axiosErr?.response?.data?.detail
      const code = typeof detail === 'object' ? detail?.code : detail
      if (isServerUnreachable(err)) {
        setError(t('auth.serverError'))
      } else if (axiosErr?.response?.status === 429) {
        setError(t('auth.tooManyAttempts'))
      } else if (code === 'RESET_PASSWORD_INVALID_PASSWORD') {
        setError(t('auth.resetPasswordRejected'))
      } else if (axiosErr?.response?.status === 400) {
        setError(t('auth.resetTokenInvalid'))
      } else {
        setError(t('auth.serverError'))
      }
    } finally {
      setIsLoading(false)
    }
  }

  let body
  if (!token) {
    body = (
      <CardContent className="px-8 pt-4">
        <div role="alert" className="p-3 text-sm text-destructive bg-destructive/10 rounded-lg">
          {t('auth.resetTokenMissing')}
        </div>
      </CardContent>
    )
  } else if (done) {
    body = (
      <CardContent className="px-8 pt-4">
        <div role="status" className="p-3 text-sm bg-muted rounded-lg">
          {t('auth.passwordChanged')}
        </div>
      </CardContent>
    )
  } else {
    body = (
      <form onSubmit={handleSubmit}>
        <CardContent className="space-y-4 px-8 pt-4">
          {error && (
            <div role="alert" className="p-3 text-sm text-destructive bg-destructive/10 rounded-lg">
              {error}
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="new-password" className="text-sm">{t('auth.newPassword')}</Label>
            <Input
              id="new-password"
              type="password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value)
                setError('')
              }}
              autoComplete="new-password"
              required
              minLength={8}
              autoFocus
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="confirm-new-password" className="text-sm">
              {t('auth.confirmPassword')}
            </Label>
            <Input
              id="confirm-new-password"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
              required
            />
          </div>
        </CardContent>
        <CardFooter className="flex flex-col gap-4 px-8 pb-8 pt-2">
          <Button type="submit" className="w-full" disabled={isLoading}>
            {isLoading ? t('common.loading') : t('auth.resetPassword')}
          </Button>
        </CardFooter>
      </form>
    )
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-background px-4">
      <Card className="w-full max-w-[400px] shadow-sm">
        <div className="flex flex-col items-center pt-8 pb-2 px-8">
          <div className="w-11 h-11 rounded-xl bg-primary/10 flex items-center justify-center mb-4">
            <ShellLogo size={22} className="text-primary" />
          </div>
          <h1 className="text-xl font-semibold tracking-tight">{t('auth.resetPassword')}</h1>
          {token && !done && (
            <p className="text-sm text-muted-foreground mt-1 text-center">
              {t('auth.resetPasswordDescription')}
            </p>
          )}
        </div>
        {body}
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
