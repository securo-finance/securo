# Securo Helm Chart

[Securo](https://github.com/securo-finance/securo) is an advanced, self-hosted personal finance manager with built-in AI agents. This Helm chart provides a complete, production-ready deployment of Securo to any Kubernetes cluster.

## Features

- **Complete Application Stack**: Deploys the Next.js React frontend and FastAPI backend, fully supporting all features including Open Banking integrations, OIDC authentication, and AI financial agents.
- **Asynchronous Task Scheduler**: Runs Celery workers and a Celery beat singleton for automated background processing (such as bank syncs, recurring transactions, and exchange rate updates).
- **Databases**: Easily configure connections to your external PostgreSQL and Redis instances.
- **Gateway API & Ingress**: Native support for standard Kubernetes Ingress or the modern Kubernetes Gateway API (`HTTPRoute`).

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

## Upgrades and database migrations

Every install and upgrade creates a migration Job that runs `alembic upgrade
head` with the release's new image and configuration. The backend, Celery
worker, Celery beat and MCP server pods start with a `wait-for-schema` init
container that runs `alembic current --check-heads` every 5 seconds and lets the
pod start only once the database is at the migration head of its image.

- On a first install, the Job retries until the database accepts connections,
  so the bundled PostgreSQL can come up next to it. The other pods stay in
  `Init` until the database reaches that head, which on a new database is when
  the Job finishes.
- On an upgrade, the new pods stay in `Init` until the database is at the head
  of the new image, and the Deployments keep the old pods serving until the new
  ones are ready. A migration that rewrites a large table keeps them in `Init`
  for as long as it takes. `helm upgrade --wait` waits for that too, so give it
  a `--timeout` above how long you expect the migration to run.
- If the database is already at the head of the new image, the pods can start
  before the Job finishes; the Job then finds nothing to do.
- The Job's name ends in a hash of its pod template and the release revision,
  so each upgrade gets a fresh Job and Helm deletes the previous one. Follow it
  with `kubectl logs job/<name>`.
- If the Job fails before the database reaches that head, the new pods stay in
  `Init` and the old ones keep serving. Fix the cause and run `helm upgrade`
  again.
- Going back to an older image on this chart does not downgrade the database:
  the older pods stay in `Init`, because the database is ahead of their image.
  Going back to a chart from before this change has no schema gate at all, so
  its backend pods start `uvicorn` directly against the newer database.
  Restore a backup taken before the upgrade, or stay on the newer version.

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
before disabling their PVC. The permissions and migration steps are in the
[deployment guide](https://docs.usesecuro.com/docs/deployment/docker/#storing-attachments-in-s3).
S3 mode does not move AI knowledge files to object storage.

### Client IP behind the Ingress

Login, second factor and registration are rate limited per client IP, read
from `X-Forwarded-For` as far as `config.trustedProxyHops` proxies. The default,
`"1"`, fits the Ingress and the HTTPRoute, which send `/api` straight to the
backend. When a load balancer in front of the controller also appends to
`X-Forwarded-For`, set `"2"`; set it too low and every visitor shares the
address of the nearest proxy, and with it one rate-limit bucket.

A hop is only crossed while the address in hand is in `TRUSTED_PROXY_NETWORKS`
(loopback and the private ranges by default). If that load balancer reaches
the controller from a public address, `"2"` alone still stops at the load
balancer and every visitor keeps sharing its bucket: add its address range to
`TRUSTED_PROXY_NETWORKS` through `backend.extraEnv` as well.

Trusting a hop also means the backend reads `X-Forwarded-For` from any peer in
those private ranges, which includes any pod in the cluster that reaches the
backend Service directly. The chart renders no NetworkPolicy, so on a cluster
that runs workloads you do not trust, restrict ingress to the backend pods to
the Ingress controller or gateway (and the frontend pods); otherwise such a pod
can pick a new rate-limit bucket on every request.

### Extra environment variables, volumes and volume mounts

`backend`, `celeryWorker`, `celeryBeat`, `migration` and `mcpServer` each
accept `extraEnv`, `extraEnvFrom`, `extraVolumes` and `extraVolumeMounts`,
empty by default. The entries keep the Kubernetes shapes (`EnvVar`,
`EnvFromSource`, `Volume`, `VolumeMount`) and are rendered untouched, so
`valueFrom` and every volume type work. `extraEnv` is read after the ConfigMap
and the Secret and therefore wins over both, as `env` does over `envFrom` in
Kubernetes. `migration` configures the migration Job. The `wait-for-schema` init
container of each pod takes that pod's own extras, so it reaches the database
the same way the pod does.

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

Repeat the block under `celeryWorker`, `celeryBeat`, `migration` and `mcpServer`
if those pods connect to the same database. The MCP server belongs on that list
even though it is off by default: when `mcpServer.enabled` and
`config.agentsEnabled` are on, it opens its own database sessions for the agents'
tools, so without the variables and the mounted certificate those tools fail to
reach the database.

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

## Uninstalling the Chart

To uninstall/delete the `securo` deployment:

```bash
helm uninstall securo
```

This command removes all the Kubernetes components associated with the chart and deletes the release. Note that Persistent Volume Claims (PVCs) created by the chart might not be deleted automatically to prevent accidental data loss.
