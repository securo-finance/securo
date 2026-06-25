import { useEffect, useRef } from 'react'
import { getFintoc } from '@fintoc/fintoc-js'

interface FintocConnectWidgetProps {
  widgetToken: string
  onSuccess: (exchangeToken: string) => void
  onExit: () => void
}

export function FintocConnectWidget({ widgetToken, onSuccess, onExit }: FintocConnectWidgetProps) {
  const onSuccessRef = useRef(onSuccess)
  const onExitRef = useRef(onExit)

  useEffect(() => {
    onSuccessRef.current = onSuccess
    onExitRef.current = onExit
  })

  useEffect(() => {
    let active = true
    let widget: { open: () => void; destroy?: () => void } | null = null

    getFintoc()
      .then((Fintoc) => {
        if (!active || !Fintoc) return

        widget = Fintoc.create({
          publicKey: import.meta.env.VITE_FINTOC_PUBLIC_KEY,
          widgetToken,
          onSuccess: (data: any) => {
            // The Fintoc SDK delivers the Link Intent object in camelCase.
            // exchangeToken is the one-time token that must be exchanged server-side.
            const token = data?.exchangeToken
            if (token) {
              onSuccessRef.current(token)
            } else {
              onExitRef.current()
            }
          },
          onExit: () => onExitRef.current(),
          onError: () => onExitRef.current(),
        })
        widget.open()
      })
      .catch((err) => {
        console.error('[FintocLink]', err)
        onExitRef.current()
      })

    return () => {
      active = false
      widget?.destroy?.()
    }
  }, [widgetToken])

  return null
}

