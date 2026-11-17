# Securo Helm Chart

[Securo](https://github.com/securo-finance/securo) is an advanced, self-hosted personal finance manager with built-in AI agents. This Helm chart provides a complete, production-ready deployment of Securo to any Kubernetes cluster.

## Features

- **Complete Application Stack**: Deploys the Next.js React frontend and FastAPI backend, fully supporting all features including Open Banking integrations, OIDC authentication, and AI financial agents.
- **Asynchronous Task Scheduler**: Runs Celery workers and a Celery beat singleton for automated background processing (such as bank syncs, recurring transactions, and exchange rate updates).
- **Databases**: Easily configure connections to your external PostgreSQL and Redis instances.
- **Gateway API & Ingress**: Native support for standard Kubernetes Ingress or the modern Kubernetes Gateway API (`HTTPRoute`).
- **Plugins with code (optional)**: Runs plugin code in a separate container that reaches the internet only through an egress proxy which refuses private addresses and hosts the plugin did not declare.

## Prerequisites

- Kubernetes 1.25+
- Helm 3.2.0+
- PostgreSQL 15+ (with the `pgvector` extension installed)
- Redis 7+
- Persistent Volume (PV) provisioner support in the underlying infrastructure

### Persistent Storage Requirements
Since the Backend, Celery Worker, and MCP Server all share the same files (like uploaded attachments and AI knowledge bases), they all mount the same Persistent Volume Claims concurrently.
**If your cluster spans multiple nodes, you MUST use a StorageClass that supports `ReadWriteMany` (RWX) access mode (e.g., NFS, CephFS, or Longhorn RWX).**
If your storage only supports `ReadWriteOnce` (RWO), you must restrict all Securo pods to run on a single node (using `nodeSelector` or `podAffinity`) but that is an antipattern in Kubernetes.

## Quickstart

If you want to install the latest official release from the GitHub Container Registry (GHCR):

```bash
helm install securo oci://ghcr.io/securo-finance/charts/securo --version <VERSION>
```

If you are developing locally and want to install from the source repository:

```bash
helm install securo ./charts/securo
```

## Configuration

All application-level features (such as AI Services, OIDC authentication, and Open Banking integrations) can be configured directly through Helm.
The keys in the `config:` and `secret:` blocks of the `values.yaml` map directly to the `UPPER_SNAKE_CASE` environment variables used in the standard Docker setup (converted to `camelCase`).

For a full list of available environment variables and API keys, please refer to the **[Securo Application Configuration Guide](https://github.com/securo-finance/securo-docs)**.

### Secrets Management (Production)

For production deployments, we highly recommend managing your secrets securely by passing an existing Kubernetes `Secret` rather than putting plain text keys in your `values.yaml`:

```yaml
global:
  existingSecret: "my-securo-secrets"
```

The secret must contain the corresponding keys (e.g., `secretKey`, `databaseUrl`, `agentsOpenaiApiKey`).

### Plugins with code

Data plugins (categories, rules and themes) only need `config.pluginsEnabled: "true"`. Plugins that ship code also need a runner and an egress proxy, both created by the chart:

```yaml
config:
  pluginsEnabled: "true"

plugins:
  runner:
    enabled: true
```

The chart creates a Secret with a random runner token and egress secret, kept across upgrades. To bring your own, set `plugins.runner.existingSecret` to a Secret with `PLUGINS_RUNNER_TOKEN` and `PLUGINS_EGRESS_SECRET`, each at least 32 characters.

To rotate the generated pair, delete the `-plugins` Secret and run `helm upgrade`: the chart creates a new one and the checksum rolls the runner, the backend and the worker.

The runner starts plugin processes as their own unprivileged users, with a read-only root filesystem, `/tmp` on tmpfs and a PID and memory limit. Plugin code leaves the namespace only through `plugins-egress`, which checks each plugin's signed pass against the hosts it declared and refuses private addresses.

Enabling the runner turns `PLUGINS_ENABLED` on and keeps it on, even if `config.pluginsEnabled` says otherwise. Its namespace needs the `baseline` Pod Security Standard or looser: the runner runs as root so it can give each plugin its own uid.

The runner stays at one replica and rolls with `Recreate`: its registry and uid allocation are guarded by locks inside one process, so two pods writing the same volume can hand one uid to two plugins.

Set `plugins.networkPolicy.enabled: true` on a cluster whose CNI enforces `NetworkPolicy` to also stop the runner from reaching other pods. The policy allows the backend (plugin calls that carry an API token talk to it), DNS and the egress proxy, nothing else. The proxy still refuses private addresses while it is off, but the runner can then open connections to anything the cluster allows.

Disabling `plugins.egress` points you at your own proxy through `runner.extraEnv`. Add its destination to `plugins.networkPolicy.extraEgress` too, otherwise the policy the chart applies blocks it.

## Uninstalling the Chart

To uninstall/delete the `securo` deployment:

```bash
helm uninstall securo
```

This command removes all the Kubernetes components associated with the chart and deletes the release. Note that Persistent Volume Claims (PVCs) created by the chart might not be deleted automatically to prevent accidental data loss.
