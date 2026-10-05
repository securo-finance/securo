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
AI knowledge documents and embedding caches still need their configured volumes.

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

In addition to the usual application secrets, provide `STORAGE_S3_ACCESS_KEY`
and `STORAGE_S3_SECRET_KEY` in `securo-secrets` (optionally
`STORAGE_S3_SESSION_TOKEN`). Alternatively, use `secret.storageS3AccessKey`,
`secret.storageS3SecretKey`, and `secret.storageS3SessionToken` when letting Helm
create the Secret. Leave credentials unset when using an SDK-supported workload
identity. For AWS S3, omit the endpoint and use addressing style `auto`.

Create a private bucket before deployment and migrate existing attachments
before disabling their PVC. Consult the repository README's Attachment Storage
section for permissions, migration, and integration testing. S3 mode does not
move AI knowledge files to object storage.

## Uninstalling the Chart

To uninstall/delete the `securo` deployment:

```bash
helm uninstall securo
```

This command removes all the Kubernetes components associated with the chart and deletes the release. Note that Persistent Volume Claims (PVCs) created by the chart might not be deleted automatically to prevent accidental data loss.
