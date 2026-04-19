export function getAccountName(account: { name: string; display_name?: string | null }): string {
  return account.display_name ?? account.name
}
