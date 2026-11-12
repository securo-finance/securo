import { createContext, useContext } from 'react'

interface AssistantContextType {
  /** The chat with the agents can be opened here (agents on, and allowed to write). */
  available: boolean
  /** Opens the chat and sends `message` to the active agent, the default one unless another was picked. */
  ask: (message: string) => void
}

export const AssistantContext = createContext<AssistantContextType | null>(null)

/** The assistant, or an unavailable one outside the app shell. */
export function useAssistant(): AssistantContextType {
  return useContext(AssistantContext) ?? { available: false, ask: () => {} }
}
