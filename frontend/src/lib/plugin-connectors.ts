import type { InstalledPlugin, PluginIntegration } from '@/types'

/** An installed plugin's integration that syncs as a connected bank. */
export interface PluginConnector {
  plugin: InstalledPlugin
  integration: PluginIntegration
}

/** Integrations of the installed plugins that provide records, in catalog order. */
export function pluginConnectors(installed: InstalledPlugin[] | undefined): PluginConnector[] {
  return (installed ?? []).flatMap((plugin) =>
    plugin.integrations
      .filter((integration) => integration.provides?.length)
      .map((integration) => ({ plugin, integration })),
  )
}
