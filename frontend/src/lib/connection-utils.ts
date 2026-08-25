export function getConnectionName(connection: { institution_name: string; display_name?: string | null }): string {
  return connection.display_name ?? connection.institution_name
}

/** Only rejected/expired credentials require secret entry. Other failures can
 * be retried with the encrypted credentials already stored by the backend. */
export function connectionRequiresReconnect(status: string): boolean {
  return status === 'expired'
}
