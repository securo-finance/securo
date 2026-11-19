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

The following shared-file requirement applies to **local storage**. With
`config.storageProvider: s3`, attachments and invoice logos use object storage;
you can disable `persistence.attachments.enabled` after migrating existing files.
AI knowledge documents, embedding caches and managed backups still need their configured volumes.

Since the Backend, Celery Worker, Celery Beat, and MCP Server share persistent files (like uploaded attachments, managed backups, and AI knowledge bases), they mount the same Persistent Volume Claims concurrently.
**If your cluster spans multiple nodes, you MUST use a StorageClass that supports `ReadWriteMany` (RWX) access mode (e.g., NFS, CephFS, or Longhorn RWX).**
If your storage only supports `ReadWriteOnce` (RWO), you must restrict all Securo pods to run on a single node (using `nodeSelector` or `podAffinity`) but that is an antipattern in Kubernetes.

Managed backup persistence is opt-in. Enable it with
`--set persistence.backups.enabled=true`; without that value, managed ZIPs use
pod-local storage and scheduled backups are disabled because the API and worker
do not share a filesystem. Direct backup downloads remain available.

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

The existing Secret must contain environment-variable keys (e.g., `SECRET_KEY`,
`DATABASE_URL`, `AGENTS_OPENAI_API_KEY`). The camelCase names in `values.yaml`
are converted to these names only when the chart creates the Secret itself.

### S3 attachment storage

```yaml
config:
  storageProvider: s3
  storageS3Bucket: securo-attachments
  storageS3Region: garage
  storageS3EndpointUrl: https://s3.example.com
  storageS3AddressingStyle: path
persistence:
  attachments:
    enabled: false
global:
  existingSecret: securo-secrets
```

S3 support is part of the regular backend image, so `backend.image.tag` stays
as it is.

In addition to the usual application secrets, provide `STORAGE_S3_ACCESS_KEY`
and `STORAGE_S3_SECRET_KEY` in `securo-secrets` (optionally
`STORAGE_S3_SESSION_TOKEN`). Alternatively, use `secret.storageS3AccessKey`,
`secret.storageS3SecretKey`, and `secret.storageS3SessionToken` when letting Helm
create the Secret. Leave credentials unset when using an SDK-supported workload
identity. For AWS S3, omit the endpoint and use addressing style `auto`.
`http://` endpoints are rejected unless `config.storageS3AllowInsecureHttp: "true"`
is set (development only; traffic is unencrypted).

Create a private bucket before deployment and migrate existing attachments
before disabling their PVC. Consult the repository README's Attachment Storage
section for permissions, migration, and integration testing. S3 mode does not
move AI knowledge files to object storage.

### Extra environment variables, volumes and volume mounts

`backend`, `celeryWorker`, `celeryBeat` and `migration` each accept `extraEnv`,
`extraEnvFrom`, `extraVolumes` and `extraVolumeMounts`, empty by default. The
entries keep the Kubernetes shapes (`EnvVar`, `EnvFromSource`, `Volume`,
`VolumeMount`) and are rendered untouched, so `valueFrom` and every volume type
work. `extraEnv` is read after the ConfigMap and the Secret and therefore wins
over both, as `env` does over `envFrom` in Kubernetes. `migration` configures
the post-install/post-upgrade Job, which reaches the database before the pods do.

This is what an external PostgreSQL with TLS client-certificate authentication
needs, for example:

```yaml
backend:
  extraEnv:
    - name: PGSSLMODE
      value: verify-full
    - name: PGSSLCERT
      value: /etc/pg-tls/tls.crt
    - name: PGSSLKEY
      value: /etc/pg-tls/tls.key
    - name: PGSSLROOTCERT
      value: /etc/pg-tls/ca.crt
  extraVolumes:
    - name: pg-client-tls
      secret:
        secretName: pg-client-tls
  extraVolumeMounts:
    - name: pg-client-tls
      mountPath: /etc/pg-tls
      readOnly: true
```

Repeat the block under `celeryWorker`, `celeryBeat` and `migration` if those
pods connect to the same database.

To mount a PersistentVolumeClaim you already have instead of letting the chart
create one, set `persistence.attachments.existingClaim`. Keep
`persistence.attachments.enabled` true so the pods mount it; the chart then
renders no PVC and leaves your claim untouched on uninstall.

On a release that is already running, do not set it in the same upgrade that
follows the first install. The chart's PVC leaves the release when the value is
set, Helm deletes resources that leave the release, and with a `Delete` reclaim
policy that takes the attachments with it. Hand it over in two steps: annotate
the chart's PVC with `helm.sh/resource-policy: keep` and upgrade, then set
`persistence.attachments.existingClaim` and upgrade again. The claim then also
survives `helm uninstall`, so delete it by hand when you really want it gone. A
claim you created yourself, outside the release, needs no handoff.

### AI Agents and the MCP server

`mcpServer.enabled` runs the built-in MCP server inside the cluster. It is not added to the Ingress or the HTTPRoute unless you also set `mcpServer.publicRoute: true`, so publishing it to your public entry point is a deliberate step; when you do, set `config.agentsExternalMcpUrl` to the URL clients should use. Tokens are signed with `secret.agentsMcpJwtSecret` when it is set, or with a key derived from `secretKey` otherwise, so a random `secretKey` is enough. A known public value is ignored and the derived key used instead, since anyone holding the signing key could mint a token for any user.

### Plugins with code

Data plugins (categories, rules and themes) only need `config.pluginsEnabled: "true"`. Plugins that ship code also need a runner and an egress proxy, both created by the chart:

```yaml
config:
  pluginsEnabled: "true"

plugins:
  runner:
    enabled: true
```

The chart creates a Secret with a random runner token and egress secret, kept across upgrades. To bring your own, set `plugins.runner.existingSecret` to a Secret with `PLUGINS_RUNNER_TOKEN` and `PLUGINS_EGRESS_SECRET`, each at least 32 characters, or set `plugins.runner.token` and `plugins.runner.egressSecret` as values.

To rotate, set new `plugins.runner.token` and `plugins.runner.egressSecret` values (or update your own Secret): the checksum is computed from the credentials in use, so the runners, the backend, the worker and beat roll together. Deleting the Secret in place is not a rotation path when the chart generates the pair outside the cluster, since the render cannot tell that the old credentials are gone.

Under GitOps tools (Argo CD, Flux) and plain `helm template`, `lookup` returns nothing, so every sync would generate a new pair and restart those pods for nothing. Manage the Secret yourself there and set `plugins.runner.existingSecret` to it.

The runner starts plugin processes as their own unprivileged users, with a read-only root filesystem, `/tmp` on tmpfs and a PID and memory limit. Plugin code reaches the internet only through `plugins-egress`, which checks each plugin's signed pass against the hosts it declared and refuses private addresses. DNS is the exception: the runner resolves the backend and the proxy through the cluster resolver, and plugin code can send its own queries there, so that resolver is a low-bandwidth channel of its own unless it filters undeclared names.

Enabling the runner turns `PLUGINS_ENABLED` on and keeps it on, even if `config.pluginsEnabled` says otherwise. Its namespace needs the `baseline` Pod Security Standard or looser: the runner runs as root so it can give each plugin its own uid.

The runner is a one-replica StatefulSet: its registry and uid allocation are guarded by locks inside one process, so two pods writing the same volume can hand one uid to two plugins. The controller never creates the replacement before the previous pod is fully gone, which covers a manual `kubectl delete pod` as well as an upgrade. The claim is annotated to survive the runner being disabled, so installed plugin code is still there when it is turned back on.

The NetworkPolicy is on by default, like the internal network in Compose: it allows the backend (plugin calls that carry an API token talk to it), the worker, beat, DNS and the egress proxy, nothing else. Without it, plugin code can open sockets straight to Redis, Postgres or the cloud metadata endpoint, because the proxy only guards traffic that goes through it. Leave it off only when the CNI does not enforce `NetworkPolicy`; the runner is not isolated then.

Disabling `plugins.egress` points you at your own proxy through `runner.extraEnv`. Add its destination to `plugins.networkPolicy.extraEgress` too, otherwise the policy the chart applies blocks it. Clusters that resolve through a NodeLocal DNSCache behind Cilium Local Redirect also need that resolver's address there, since the built-in rule matches `kube-dns` only.

## Uninstalling the Chart

To uninstall/delete the `securo` deployment:

```bash
helm uninstall securo
```

This command removes all the Kubernetes components associated with the chart and deletes the release. Note that Persistent Volume Claims (PVCs) created by the chart might not be deleted automatically to prevent accidental data loss.
