---
title: "feat: Add Production Environment to GitOps"
type: feat
status: completed
date: 2026-03-18
origin: docs/brainstorms/2026-03-18-multi-environment-gitops-brainstorm.md
---

# feat: Add Production Environment to GitOps

## Overview

Extend the existing `astrodash-k8s-gitops` repo to support both dev (`astrodash-dev.scimma.org`) and prod (`astrodash.scimma.org`) deployments. Shared Helm templates, per-environment values files, separate ArgoCD instances per cluster.

## Proposed Solution

Refactor the current `values.yaml` into a layered structure:
- `values.yaml` — shared defaults (resource limits, ports, non-environment-specific config)
- `values-dev.yaml` — dev overrides (hostname, image tag, pull policy)
- `values-prod.yaml` — prod overrides (hostname, stable image tag, pull policy)

Create separate ArgoCD Application CRDs (`astrodash-dev.yaml` and `astrodash-prod.yaml`) that reference different value files. Infrastructure ArgoCD apps (Traefik, cert-manager, Sealed Secrets) are identical and reused on both clusters.

(See brainstorm: `docs/brainstorms/2026-03-18-multi-environment-gitops-brainstorm.md`)

## Acceptance Criteria

- [ ] `values.yaml` contains only shared defaults (no environment-specific hostnames or image tags)
- [ ] `values-dev.yaml` contains dev-specific overrides
- [ ] `values-prod.yaml` contains prod-specific overrides
- [ ] `argocd-apps/astrodash-dev.yaml` references `values.yaml` + `values-dev.yaml`
- [ ] `argocd-apps/astrodash-prod.yaml` references `values.yaml` + `values-prod.yaml`
- [ ] Existing `argocd-apps/astrodash.yaml` removed (replaced by `astrodash-dev.yaml`)
- [ ] Helm chart lints and renders correctly with both value file combinations
- [ ] Prod cluster bootstrapped: ArgoCD installed, repo registered, infra deployed
- [ ] Secrets sealed with prod cluster keys
- [ ] Production deployment running at `https://astrodash.scimma.org`

## Implementation Phases

### Phase 1: Refactor GitOps Repo for Multi-Environment

**`apps/astrodash/values.yaml`** — Extract environment-specific values, keep shared defaults:

```yaml
image:
  repository: registry.gitlab.com/ncsa-caps-rse/astrodash-k8s-gitops
  # tag and pullPolicy set per-environment

namespace: astrodash

web:
  replicas: 1
  port: 8000
  gunicornWorkers: 2
  gunicornTimeout: 300
  resources:
    requests:
      cpu: "100m"
      memory: "512Mi"
    limits:
      cpu: "500m"
      memory: "1536Mi"

# ... all other shared resource configs unchanged ...

# ingress.host, clusterIssuer — set per-environment
# config values that differ (hostnames, CORS) — set per-environment
# Shared config values stay here

config:
  DB_HOST: "astrodash-postgresql"
  DB_PORT: "5432"
  DB_NAME: "astrodash_db"
  DB_USER: "astrodash"
  REDIS_SERVICE: "astrodash-redis"
  MESSAGE_BROKER_HOST: "astrodash-redis"
  MESSAGE_BROKER_PORT: "6379"
  WEB_APP_HOST: "astrodash-web"
  WEB_APP_PORT: "8000"
  CELERY_QUEUES: "celery"
  CELERY_CONCURRENCY: "4"
  CELERY_MAX_MEMORY_PER_CHILD: "12000"
  API_AUTHENTICATION: "AllowAny"
  SKIP_INITIALIZATION: "false"
  DISABLE_CELERY_BEAT: "false"
  FORCE_INITIALIZATION: "true"
  ASTRODASH_DATA_DIR: "/mnt/astrodash-data"
  FLOWER_PORT: "8888"
  DJANGO_SUPERUSER_USERNAME: "admin"
  DJANGO_SUPERUSER_EMAIL: "skoranda@illinois.edu"
  DJANGO_DEBUG: "false"
```

**`apps/astrodash/values-dev.yaml`** — Dev-specific overrides:

```yaml
image:
  tag: dev2-v1.0.0
  pullPolicy: Always

ingress:
  enabled: true
  host: astrodash-dev.scimma.org
  clusterIssuer: letsencrypt-production

config:
  DJANGO_ALLOWED_HOSTS: "astrodash-dev.scimma.org"
  DJANGO_HOSTNAMES: "astrodash-dev.scimma.org"
  ASTRO_DASH_CORS_ALLOWED_ORIGINS: "https://astrodash-dev.scimma.org"
```

**`apps/astrodash/values-prod.yaml`** — Prod-specific overrides:

```yaml
image:
  tag: v1.0.0
  pullPolicy: IfNotPresent

ingress:
  enabled: true
  host: astrodash.scimma.org
  clusterIssuer: letsencrypt-production

config:
  DJANGO_ALLOWED_HOSTS: "astrodash.scimma.org"
  DJANGO_HOSTNAMES: "astrodash.scimma.org"
  ASTRO_DASH_CORS_ALLOWED_ORIGINS: "https://astrodash.scimma.org"
```

**`argocd-apps/astrodash-dev.yaml`** (replaces `astrodash.yaml`):

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: astrodash
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://gitlab.com/ncsa-caps-rse/astrodash-k8s-gitops.git
    targetRevision: HEAD
    path: apps/astrodash
    helm:
      valueFiles:
        - values.yaml
        - values-dev.yaml
  destination:
    server: https://kubernetes.default.svc
    namespace: astrodash
  ignoreDifferences:
    - group: networking.k8s.io
      kind: Ingress
      jsonPointers:
        - /status
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

**`argocd-apps/astrodash-prod.yaml`**:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: astrodash
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://gitlab.com/ncsa-caps-rse/astrodash-k8s-gitops.git
    targetRevision: HEAD
    path: apps/astrodash
    helm:
      valueFiles:
        - values.yaml
        - values-prod.yaml
  destination:
    server: https://kubernetes.default.svc
    namespace: astrodash
  ignoreDifferences:
    - group: networking.k8s.io
      kind: Ingress
      jsonPointers:
        - /status
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

- [ ] Refactor `values.yaml` into shared + environment files
- [ ] Create `values-dev.yaml` and `values-prod.yaml`
- [ ] Create `astrodash-dev.yaml` and `astrodash-prod.yaml` ArgoCD apps
- [ ] Remove old `astrodash.yaml`
- [ ] Verify with `helm template` using both value file combinations
- [ ] Commit and push
- [ ] Re-apply `astrodash-dev.yaml` on the dev cluster (replaces old `astrodash.yaml`)

### Phase 2: Bootstrap Production Cluster

Same bootstrap process as dev (see K8s deployment plan):

- [x] Provision k3s prod cluster on Jetstream2 (with `--disable traefik`)
- [ ] Install Cinder CSI and create `cinder` StorageClass (with `reclaimPolicy: Retain`)
- [ ] Install ArgoCD via Helm (use `infrastructure/argocd/values.yaml` for Ingress health customization)
- [ ] Register GitLab repo with ArgoCD (same deploy token)
- [ ] Apply infrastructure ArgoCD apps (Traefik, cert-manager, Sealed Secrets)
- [ ] Wait for all three to be Synced + Healthy
- [ ] Manually apply ClusterIssuer if ArgoCD sync wave doesn't apply it in time
- [ ] Verify Traefik binds to floating IP on ports 80/443

### Phase 3: Seal Prod Secrets and Deploy

- [ ] Seal application secrets with prod cluster's Sealed Secrets keys
- [ ] Seal GitLab registry pull secret with prod cluster keys
- [ ] Commit sealed secrets as `sealedsecret-prod.yaml` and `sealedsecret-registry-prod.yaml`
  (or overwrite the existing files — since only one ArgoCD per cluster, each cluster only reads its own sealed secrets)
- [ ] Push to GitLab
- [ ] Apply `astrodash-prod.yaml` on prod cluster
- [ ] Watch pods come up

> **Note on sealed secrets:** Since each cluster has its own ArgoCD and reads from the same repo, but sealed secrets are cluster-specific, the sealed secret files need to be environment-specific. Options: (a) use separate filenames (`sealedsecret-dev.yaml`, `sealedsecret-prod.yaml`) with conditional rendering in templates, or (b) keep the same filenames and accept that only one cluster can unseal them (the other will show errors but the SealedSecret controller will ignore secrets it can't decrypt). Option (b) is simpler for now.

### Phase 4: DNS and TLS

- [x] Request DNS CNAME for `astrodash.scimma.org` pointing to the prod floating IP
- [ ] Once DNS propagates, verify cert-manager obtains Let's Encrypt production certificate
- [ ] Test HTTPS access to `https://astrodash.scimma.org`

## Dependencies & Prerequisites

| Dependency | Status | Blocker? |
|-----------|--------|----------|
| Prod k3s cluster on Jetstream2 | Provisioned (2026-03-19) | No |
| Prod floating IP | Provisioned | No |
| DNS for astrodash.scimma.org | Configured (2026-03-19) | No |
| Stable image tag for prod (e.g., v1.0.0) | Needs to be created | Blocks prod deployment |

## Risk Analysis & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Helm template change breaks both environments | Medium | Dev gets changes first via moving tag; prod uses stable tag, deployed only after dev validation |
| Sealed secrets conflict (two environments, one repo) | Low | Each cluster's Sealed Secrets controller ignores secrets it can't decrypt |
| ArgoCD on dev auto-syncs prod changes | None | Each cluster has its own ArgoCD; they don't share state |

## Sources

- **Origin brainstorm:** [docs/brainstorms/2026-03-18-multi-environment-gitops-brainstorm.md](docs/brainstorms/2026-03-18-multi-environment-gitops-brainstorm.md) — Key decisions: single repo with per-env values, separate ArgoCD per cluster, shared infra configs
- **Dev K8s deployment plan:** `docs/plans/2026-03-15-001-feat-kubernetes-gitops-deployment-plan.md`
- **GitOps repo:** `gitlab.com/ncsa-caps-rse/astrodash-k8s-gitops`
