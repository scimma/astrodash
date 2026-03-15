---
title: "feat: Kubernetes GitOps Deployment for Astrodash"
type: feat
status: active
date: 2026-03-15
origin: docs/brainstorms/2026-03-15-kubernetes-gitops-deployment-brainstorm.md
---

# feat: Kubernetes GitOps Deployment for Astrodash

## Overview

Deploy the astrodash web application to a Jetstream2 Kubernetes cluster using GitOps with ArgoCD. The deployment includes the full application stack (Django, PostgreSQL, Redis, Celery, Flower) and cluster infrastructure (Traefik, cert-manager, Sealed Secrets). All manifests live in a single GitLab repository managed by ArgoCD.

## Proposed Solution

Custom Helm chart for astrodash + official Helm charts for infrastructure, all managed by ArgoCD. ArgoCD is bootstrapped manually once, then manages itself and all other components declaratively from Git (see brainstorm: `docs/brainstorms/2026-03-15-kubernetes-gitops-deployment-brainstorm.md`).

**Key architectural choice:** Use standard Kubernetes Ingress resources (not Traefik IngressRoute CRDs). This enables cert-manager's automatic certificate management via Ingress annotations and avoids vendor lock-in. Traefik fully supports standard Ingress via `ingressClassName: traefik`.

## Technical Approach

### Architecture

```
Internet
    │
    ▼
Octavia LB (provisioned by Traefik LoadBalancer Service)
    │
    ▼
Traefik (TLS termination using cert-manager certificate)
    │
    ▼
Kubernetes Ingress → astrodash-web Service (port 8000)
                         │
                         ▼
                    gunicorn (Django app)
                         │
                    ┌────┴────┐
                    ▼         ▼
               PostgreSQL   Redis
                    ▲         ▲
                    │         │
              ┌─────┴───┐    │
              ▼         ▼    │
         Celery       Celery │
         Worker       Beat   │
              │              │
              └──────────────┘
                    │
                    ▼
                  Flower (internal only, port-forward)
```

**Namespace:** `astrodash` (dedicated namespace for isolation)

### Implementation Phases

#### Phase 1: Repository and Infrastructure Bootstrap

Create GitLab repo and install cluster infrastructure components.

- [ ] Create GitLab repository `astrodash-gitops`
- [ ] Initialize repo with directory structure:
  ```
  astrodash-gitops/
  ├── infrastructure/
  │   ├── traefik/
  │   ├── cert-manager/
  │   └── sealed-secrets/
  ├── apps/
  │   └── astrodash/
  └── argocd-apps/
  ```
- [ ] Install ArgoCD via Helm (manual bootstrap):
  ```bash
  helm repo add argo https://argoproj.github.io/argo-helm
  kubectl create namespace argocd
  helm install argocd argo/argo-cd --namespace argocd
  ```
- [ ] Connect ArgoCD to the GitLab repository
- [ ] Create ArgoCD Application CRDs for infrastructure components:

  **`argocd-apps/traefik.yaml`** — Traefik as LoadBalancer with Octavia:
  ```yaml
  apiVersion: argoproj.io/v1alpha1
  kind: Application
  metadata:
    name: traefik
    namespace: argocd
  spec:
    project: default
    source:
      chart: traefik
      repoURL: https://traefik.github.io/charts
      targetRevision: "39.0.5"
      helm:
        releaseName: traefik
        valuesObject:
          service:
            type: LoadBalancer
            spec:
              loadBalancerIP: "149.165.154.142"
          providers:
            kubernetesIngress:
              enabled: true
              publishedService:
                enabled: true
            kubernetesCRD:
              enabled: true
          ports:
            web:
              port: 8000
              expose:
                default: true
              exposedPort: 80
              redirections:
                entryPoint:
                  to: websecure
                  scheme: https
                  permanent: true
            websecure:
              port: 8443
              expose:
                default: true
              exposedPort: 443
              tls:
                enabled: true
          ingressClass:
            enabled: true
            isDefaultClass: true
          ingressRoute:
            dashboard:
              enabled: false
          resources:
            requests:
              cpu: "100m"
              memory: "128Mi"
            limits:
              cpu: "500m"
              memory: "256Mi"
    destination:
      server: https://kubernetes.default.svc
      namespace: traefik
    syncPolicy:
      automated:
        prune: true
        selfHeal: true
      syncOptions:
        - CreateNamespace=true
  ```

  **`argocd-apps/cert-manager.yaml`**:
  ```yaml
  apiVersion: argoproj.io/v1alpha1
  kind: Application
  metadata:
    name: cert-manager
    namespace: argocd
  spec:
    project: default
    source:
      chart: cert-manager
      repoURL: https://charts.jetstack.io
      targetRevision: "v1.19.1"
      helm:
        releaseName: cert-manager
        valuesObject:
          crds:
            install: true
    destination:
      server: https://kubernetes.default.svc
      namespace: cert-manager
    syncPolicy:
      automated:
        prune: true
        selfHeal: true
      syncOptions:
        - CreateNamespace=true
  ```

  **`argocd-apps/sealed-secrets.yaml`**:
  ```yaml
  apiVersion: argoproj.io/v1alpha1
  kind: Application
  metadata:
    name: sealed-secrets
    namespace: argocd
  spec:
    project: default
    source:
      chart: sealed-secrets
      repoURL: https://bitnami-labs.github.io/sealed-secrets
      targetRevision: "2.16.1"
      helm:
        releaseName: sealed-secrets
    destination:
      server: https://kubernetes.default.svc
      namespace: kube-system
    syncPolicy:
      automated:
        prune: true
        selfHeal: true
  ```

- [ ] Apply ArgoCD Application CRDs:
  ```bash
  kubectl apply -f argocd-apps/traefik.yaml
  kubectl apply -f argocd-apps/cert-manager.yaml
  kubectl apply -f argocd-apps/sealed-secrets.yaml
  ```
- [ ] Verify Traefik provisions an Octavia load balancer with the pre-provisioned floating IP:
  ```bash
  kubectl get svc -n traefik traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
  # Should return: 149.165.154.142
  ```
- [ ] Create ClusterIssuer for Let's Encrypt staging. Include as a template in the astrodash Helm chart (since the cert-manager ArgoCD Application points to the official chart, not local files). Alternatively, create a separate ArgoCD Application for cert-manager post-install resources:

  **`apps/astrodash/templates/clusterissuer.yaml`**:
  ```yaml
  apiVersion: cert-manager.io/v1
  kind: ClusterIssuer
  metadata:
    name: letsencrypt-staging
  spec:
    acme:
      server: https://acme-staging-v02.api.letsencrypt.org/directory
      email: skoranda@illinois.edu
      privateKeySecretRef:
        name: letsencrypt-staging-account-key
      solvers:
        - http01:
            ingress:
              ingressClassName: traefik
  ```

  > **Note:** ClusterIssuer is cluster-scoped (not namespaced), so it can live in any ArgoCD Application. Placing it in the astrodash chart is pragmatic since it's the only app consuming certificates.

#### Phase 2: Astrodash Helm Chart

Create the custom Helm chart for the astrodash application.

- [ ] Create `apps/astrodash/Chart.yaml`:
  ```yaml
  apiVersion: v2
  name: astrodash
  version: 0.1.0
  appVersion: "latest"
  description: AstroDash - Astronomical Spectra Classification
  ```

- [ ] Create `apps/astrodash/values.yaml` with configuration for all components:

  ```yaml
  image:
    repository: registry.gitlab.com/<group>/astrodash
    tag: latest
    pullPolicy: IfNotPresent

  namespace: astrodash

  # --- Django web app (gunicorn) ---
  web:
    replicas: 1
    port: 8000
    resources:
      requests:
        cpu: "250m"
        memory: "512Mi"
      limits:
        cpu: "1000m"
        memory: "1Gi"

  # --- Celery Worker ---
  celeryWorker:
    replicas: 2
    resources:
      requests:
        cpu: "250m"
        memory: "1Gi"
      limits:
        cpu: "1000m"
        memory: "4Gi"

  # --- Celery Beat ---
  celeryBeat:
    enabled: true
    resources:
      requests:
        cpu: "100m"
        memory: "128Mi"
      limits:
        cpu: "250m"
        memory: "256Mi"

  # --- Flower ---
  flower:
    enabled: true
    port: 8888
    resources:
      requests:
        cpu: "100m"
        memory: "128Mi"
      limits:
        cpu: "250m"
        memory: "256Mi"

  # --- PostgreSQL ---
  postgresql:
    image: postgres:16
    storage: 10Gi
    resources:
      requests:
        cpu: "250m"
        memory: "256Mi"
      limits:
        cpu: "1000m"
        memory: "1Gi"

  # --- Redis ---
  redis:
    image: redis:alpine
    resources:
      requests:
        cpu: "100m"
        memory: "128Mi"
      limits:
        cpu: "250m"
        memory: "256Mi"

  # --- Data volume (S3 init) ---
  data:
    storage: 5Gi
    s3:
      endpointUrl: "https://js2.jetstream-cloud.org:8001"
      bucket: "astrodash"

  # --- Ingress ---
  ingress:
    enabled: true
    host: astrodash-dev.scimma.org
    clusterIssuer: letsencrypt-staging

  # --- Django config (non-secret) ---
  config:
    DJANGO_DEBUG: "false"
    DJANGO_ALLOWED_HOSTS: "astrodash-dev.scimma.org"
    DJANGO_HOSTNAMES: "astrodash-dev.scimma.org"
    ASTRO_DASH_CORS_ALLOWED_ORIGINS: "https://astrodash-dev.scimma.org"
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
  ```

- [ ] Create Helm templates:

  **`templates/_helpers.tpl`** — Common labels and name helpers

  **`templates/configmap.yaml`** — Non-secret environment variables from `values.config`

  **`templates/deployment-app.yaml`** — Django web app:
  - Init container 1: runs `python entrypoints/initialize_data.py download` to populate data PVC from S3 (no DB dependency, can run immediately)
  - Init container 2: runs `bash entrypoints/wait-for-it.sh ${DB_HOST}:${DB_PORT} -- python init_app.py` which handles migrate → superuser creation → collectstatic in the correct order with lock-file protection

  > **Note:** Do NOT decompose `init_app.py` into separate init containers. It runs migrate, `setup_superuser.py`, and collectstatic in a specific order with a lock-file mechanism to prevent concurrent initialization. The existing entrypoint script `docker-entrypoint.app.sh` is NOT used as the main container command because it includes a `wait-for-it.sh` call for nginx (which is removed). Instead, the Deployment spec overrides the command to run gunicorn directly.
  - Main container: `gunicorn astrodash_project.wsgi --timeout 300 --workers 3 -b 0.0.0.0:8000`
  - Static files: add `whitenoise` to `requirements.txt` and `WhiteNoiseMiddleware` to Django middleware (after `SecurityMiddleware`). No separate static volume needed — whitenoise serves from `STATIC_ROOT` directly.
  - Volumes: `astrodash-data` PVC
  - Readiness/liveness probes: HTTP GET on `/` with `initialDelaySeconds: 120`, `periodSeconds: 10`, `failureThreshold: 6`
  - `envFrom`: ConfigMap + Secret
  - `podAffinity`: require co-scheduling on the same node as other pods using the `astrodash-data` PVC
  - `securityContext`: `runAsNonRoot: true`, `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true` (with writable `emptyDir` for `/tmp` and `/app/static`)

  **`templates/deployment-celery-worker.yaml`** — Celery workers:
  - Uses `wait-for-it.sh` to wait for DB, Redis, and web app
  - Command: `celery -A astrodash_project worker -l info -Q ${CELERY_QUEUES} --max-memory-per-child=${CELERY_MAX_MEMORY_PER_CHILD} -c ${CELERY_CONCURRENCY}`
  - Volumes: `astrodash-data` PVC (same RWO, single-node assumption)
  - `podAffinity`: must co-schedule on the same node as the web Deployment (for RWO PVC)

  **`templates/deployment-celery-beat.yaml`** — Celery beat (1 replica):
  - Conditional on `.Values.celeryBeat.enabled`
  - Command: `celery -A astrodash_project beat -l info`
  - Uses `wait-for-it.sh` to wait for DB, Redis, and web app
  - `podAffinity`: must co-schedule on the same node as the web Deployment (for RWO PVC)

  **`templates/deployment-flower.yaml`** — Flower monitoring:
  - Conditional on `.Values.flower.enabled`
  - ClusterIP Service only (no ingress, access via `kubectl port-forward`)

  **`templates/statefulset-postgres.yaml`** — PostgreSQL 16:
  - StatefulSet with volumeClaimTemplate for data persistence
  - Needs `POSTGRES_PASSWORD`, `POSTGRES_USER`, `POSTGRES_DB` env vars (postgres image convention)
  - The app uses `DB_PASS`, `DB_USER`, `DB_NAME` — map both from the same Secret/ConfigMap

  **`templates/deployment-redis.yaml`** — Redis:
  - Simple Deployment with no persistence (message broker only)

  **`templates/service-app.yaml`** — ClusterIP Service for web app (port 8000)
  **`templates/service-postgres.yaml`** — ClusterIP Service for PostgreSQL (port 5432)
  **`templates/service-redis.yaml`** — ClusterIP Service for Redis (port 6379)
  **`templates/service-flower.yaml`** — ClusterIP Service for Flower (port 8888)

  **`templates/ingress.yaml`** — Standard Kubernetes Ingress:
  ```yaml
  apiVersion: networking.k8s.io/v1
  kind: Ingress
  metadata:
    name: {{ .Release.Name }}
    namespace: {{ .Values.namespace }}
    annotations:
      cert-manager.io/cluster-issuer: {{ .Values.ingress.clusterIssuer }}
  spec:
    ingressClassName: traefik
    tls:
      - hosts:
          - {{ .Values.ingress.host }}
        secretName: {{ .Release.Name }}-tls
    rules:
      - host: {{ .Values.ingress.host }}
        http:
          paths:
            - path: /
              pathType: Prefix
              backend:
                service:
                  name: {{ .Release.Name }}-web
                  port:
                    number: {{ .Values.web.port }}
  ```

  **`templates/pvc-data.yaml`** — PVC for astrodash-data (ReadWriteOnce)

  > **Note:** No PVC needed for static files — whitenoise serves them from the application container directly. An `emptyDir` for `STATIC_ROOT` is sufficient.

  **`templates/sealedsecret.yaml`** — SealedSecret placeholder (sealed with `kubeseal`):
  - `SECRET_KEY` (Django reads `os.environ.get("SECRET_KEY")` — do NOT use `DJANGO_SECRET_KEY`)
  - `DB_PASS` (also used as `POSTGRES_PASSWORD` for the PostgreSQL StatefulSet via env var mapping)
  - `DJANGO_SUPERUSER_PASSWORD`

  > **Note:** S3 credentials (`ASTRODASH_S3_ACCESS_KEY_ID`, `ASTRODASH_S3_SECRET_ACCESS_KEY`) are not needed for the init container — the existing download script supports anonymous read access from the Jetstream bucket. Only include S3 creds if write access is needed (e.g., user model uploads).

  **`templates/networkpolicy.yaml`** — Restrict ingress to astrodash namespace:
  - PostgreSQL (5432): only from pods with `app: astrodash` label
  - Redis (6379): only from pods with `app: astrodash` label
  - Web (8000): only from Traefik namespace

  **`templates/clusterissuer.yaml`** — cert-manager ClusterIssuer for Let's Encrypt staging
  - Add annotation `argocd.argoproj.io/sync-wave: "1"` to ensure cert-manager CRDs are registered before this resource is applied

#### Phase 3: Seal Secrets and Deploy

- [ ] Install `kubeseal` CLI locally
- [ ] Create plaintext secret (dry-run), seal it, commit:
  ```bash
  kubectl create secret generic astrodash-secrets \
    --namespace astrodash \
    --from-literal=SECRET_KEY='<generated>' \
    --from-literal=DB_PASS='<password>' \
    --from-literal=DJANGO_SUPERUSER_PASSWORD='<password>' \
    --dry-run=client -o yaml | \
    kubeseal --format yaml --scope namespace-wide \
    > apps/astrodash/templates/sealedsecret.yaml
  ```
- [ ] Create ArgoCD Application for astrodash:

  **`argocd-apps/astrodash.yaml`**:
  ```yaml
  apiVersion: argoproj.io/v1alpha1
  kind: Application
  metadata:
    name: astrodash
    namespace: argocd
  spec:
    project: default
    source:
      repoURL: https://gitlab.com/<group>/astrodash-gitops.git
      targetRevision: HEAD
      path: apps/astrodash
      helm:
        valueFiles:
          - values.yaml
    destination:
      server: https://kubernetes.default.svc
      namespace: astrodash
    syncPolicy:
      automated:
        prune: true
        selfHeal: true
      syncOptions:
        - CreateNamespace=true
  ```

- [ ] Push to GitLab, apply ArgoCD Application:
  ```bash
  kubectl apply -f argocd-apps/astrodash.yaml
  ```
- [ ] Verify pods start and become ready:
  ```bash
  kubectl get pods -n astrodash -w
  ```

#### Phase 4: DNS, TLS, and Verification

- [ ] Confirm DNS A record `astrodash-dev.scimma.org` → `149.165.154.142` is active (ticket submitted ~2026-03-15)
- [ ] Once DNS propagates, verify cert-manager obtains certificate:
  ```bash
  kubectl get certificate -n astrodash
  kubectl describe certificate astrodash-tls -n astrodash
  ```
- [ ] Test HTTPS access to `https://astrodash-dev.scimma.org`
- [ ] Verify init container downloaded data files to `/mnt/astrodash-data/explorer/`

#### Phase 5: CI/CD Integration

- [ ] Update GitHub Actions workflow to push images to GitLab Container Registry
- [ ] Create GitLab Container Registry access token for Kubernetes image pulls
- [ ] Create `imagePullSecret` in the `astrodash` namespace (seal it with kubeseal)
- [ ] Update Helm chart to reference `imagePullSecrets`

## Alternative Approaches Considered

- **Kustomize + manual infra:** Rejected — infrastructure wouldn't be GitOps-managed, leading to drift (see brainstorm).
- **App-of-Apps bootstrap:** Rejected — overly complex for a single application, chicken-and-egg problem (see brainstorm).
- **Traefik IngressRoute CRDs:** Rejected — cert-manager doesn't support IngressRoute natively (issue #3808 closed as "not planned"). Standard Kubernetes Ingress with `ingressClassName: traefik` provides automatic cert-manager integration.
- **Traefik built-in ACME:** Rejected — stores state in `acme.json` requiring persistent storage, problematic for multi-replica deployments.

## Acceptance Criteria

### Functional Requirements

- [ ] GitLab repository created with proper directory structure
- [ ] ArgoCD installed and self-managing
- [ ] Traefik deployed as LoadBalancer, Octavia LB provisioned
- [ ] cert-manager deployed with Let's Encrypt staging ClusterIssuer
- [ ] Sealed Secrets controller deployed
- [ ] Astrodash Helm chart deployed with all components (app, DB, Redis, Celery, Flower)
- [ ] Init container downloads data from S3 on first pod start
- [ ] TLS certificate obtained after DNS resolves (non-blocking)
- [ ] Application accessible at `https://astrodash-dev.scimma.org`

### Non-Functional Requirements

- [ ] All secrets sealed and committed to git (no plaintext secrets in repo)
- [ ] Resource requests/limits set conservatively for small cluster
- [ ] ArgoCD auto-syncs from git (no manual `kubectl apply` after bootstrap)

## Dependencies & Prerequisites

| Dependency | Status | Blocker? |
|-----------|--------|----------|
| Jetstream2 K8s cluster | Ready (KUBECONFIG set) | No |
| Floating IP 149.165.154.142 | Pre-provisioned on Jetstream2 | No |
| DNS A record astrodash-dev.scimma.org → 149.165.154.142 | Ticket submitted, expected ~2026-03-16 | Blocks TLS only, not deployment |
| GitLab repository | Not yet created | Blocks everything |
| Container image in GitLab registry | Needs CI/CD update | Blocks app deployment (can use Docker Hub image initially) |
| `kubeseal` CLI | Needs installation | Blocks secret sealing |

## Risk Analysis & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Octavia LB not provisioning | High — no external access | Check Jetstream2 OpenStack quotas; verify cloud-provider integration in K8s |
| `loadBalancerIP` deprecated | Medium — floating IP not assigned | `spec.loadBalancerIP` is deprecated in K8s v1.24+. If Jetstream2's OpenStack cloud controller ignores it, use annotation `loadbalancer.openstack.org/load-balancer-address` instead. Test early in Phase 1. |
| DNS delay | Low — only blocks TLS | App accessible via IP; cert-manager retries automatically |
| S3 download timeout in init container | Medium — pod won't start | Existing retry logic (5 attempts); increase `initialDelaySeconds` on probes |
| ReadWriteOnce PVC scheduling | Medium — pods on wrong node | All pods sharing the PVC must have `nodeAffinity` or tolerate co-scheduling |
| GitLab registry auth | Medium — image pull failure | Create imagePullSecret early; test with `docker pull` first |

## Future Considerations

- Switch cert-manager from Let's Encrypt staging to production issuer
- Enable OIDC authentication
- Consider RWX storage (NFS) if multi-node scaling is needed
- Add HorizontalPodAutoscaler for web and celery workers
- ArgoCD notifications (Slack/email on sync failures)
- Monitoring stack (Prometheus + Grafana)
- PostgreSQL backup strategy (CronJob with `pg_dump` to S3)
- Redis authentication (`requirepass`)
- Dedicated ArgoCD Project with restricted source repos and destination namespaces
- Add `USER` directive to Dockerfile for non-root container execution
- Conditionally disable Django Silk profiler middleware in production
- Add `whitenoise` to `requirements.txt` and Django middleware; update `settings.py` `ALLOWED_HOSTS` to read from env var

## Sources & References

### Origin

- **Brainstorm document:** [docs/brainstorms/2026-03-15-kubernetes-gitops-deployment-brainstorm.md](docs/brainstorms/2026-03-15-kubernetes-gitops-deployment-brainstorm.md) — Key decisions: Helm chart for app, ArgoCD-managed infra, Traefik LoadBalancer, cert-manager TLS, Sealed Secrets, single GitLab repo, no nginx

### Internal References

- Docker Compose services: `docker/docker-compose.yml`
- App Dockerfile: `app/Dockerfile`
- Entrypoint scripts: `app/entrypoints/docker-entrypoint.app.sh`, `docker-entrypoint.celery.sh`, `docker-entrypoint.celery_beat.sh`, `docker-entrypoint.flower.sh`
- Environment configuration: `env/.env.default`
- S3 initialization: `app/entrypoints/initialize_data.py`
- GitHub Actions CI: `.github/workflows/docker_image_workflow.yml`

### External References

- ArgoCD cluster bootstrapping: https://argo-cd.readthedocs.io/en/latest/operator-manual/cluster-bootstrapping/
- Traefik Helm chart: https://github.com/traefik/traefik-helm-chart
- cert-manager + Traefik + Let's Encrypt: https://traefik.io/blog/secure-web-applications-with-traefik-proxy-cert-manager-and-lets-encrypt
- Sealed Secrets: https://github.com/bitnami-labs/sealed-secrets
- cert-manager HTTP-01 with Ingress: https://cert-manager.io/docs/usage/ingress/
