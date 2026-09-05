// Provider proper names — product names, not translated.
const PROVIDER_LABELS: Record<string, string> = {
  simplefin: 'SimpleFIN',
  pluggy: 'Pluggy',
  enable_banking: 'Enable Banking',
}

export function getConnectionName(
  connection: {
    provider?: string
    institution_name: string
    display_name?: string | null
    institutions?: { name: string }[]
  },
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  if (connection.display_name) return connection.display_name
  const insts = connection.institutions ?? []
  // A link spanning several institutions (SimpleFIN — issue #345) is labeled
  // by the provider it runs through, not by one arbitrary bank out of many.
  if (insts.length > 1) {
    const provider = PROVIDER_LABELS[connection.provider ?? ''] ?? connection.provider ?? ''
    return t('accounts.multiInstitutionLink', { provider, count: insts.length })
  }
  return insts[0]?.name ?? connection.institution_name
}
