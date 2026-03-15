# Brainstorm: Kubernetes GitOps Deployment for Astrodash

**Date:** 2026-03-15
**Status:** Ready for planning

## What We're Building

A Kubernetes deployment for the astrodash web application on a Jetstream2 cluster (Indiana University), managed via GitOps with ArgoCD. The deployment includes the full application stack plus cluster infrastructure components.

### Target Environment

- **Cluster:** Jetstream2 Kubernetes cluster (KUBECONFIG already configured)
- **Hostname:** `astrodash-dev.scimma.org`
- **GitOps repo:** New GitLab repository (not yet created), single repo for infra + app

### Application Stack (Full)

- Django app (gunicorn only, no nginx — Traefik handles ingress directly)
- PostgreSQL 16 (in-cluster StatefulSet with PVC)
- Redis (message broker for Celery)
- Celery workers (2 replicas, async task processing)
- Celery beat (scheduled tasks)
- Flower (Celery monitoring)
- Shared astrodash-data volume (PVC, populated by init container from S3)

### Infrastructure Components

- **Traefik** - Ingress controller, deployed as a Service of type LoadBalancer to provision an Octavia load balancer. NOT used for TLS certificate management.
- **cert-manager** - Handles TLS certificates via Let's Encrypt (staging initially, switch to production later)
- **ArgoCD** - GitOps operator, manages all deployments declaratively
- **Sealed Secrets** - Bitnami Sealed Secrets controller for encrypting secrets that can be safely committed to git

## Why This Approach

**Chosen: Custom Helm chart for astrodash + official Helm charts for infra, all managed by ArgoCD**

- ArgoCD is bootstrapped manually once, then manages itself and all other components
- Traefik and cert-manager installed via their official Helm charts with values overrides
- Astrodash gets a custom Helm chart with `values.yaml` for environment-specific configuration
- Single GitLab repo with clear directory separation between infrastructure and application

**Rejected alternatives:**
- **Kustomize + manual infra:** Infrastructure wouldn't be GitOps-managed, leading to drift.
- **App-of-Apps bootstrap:** Overly complex for a single application. Chicken-and-egg problem with ArgoCD bootstrapping itself.

## Key Decisions

- **Container registry:** GitLab Container Registry (not Docker Hub)
- **Database:** In-cluster PostgreSQL StatefulSet with PersistentVolumeClaim
- **TLS:** cert-manager with Let's Encrypt staging issuer (ACME HTTP-01 challenge via Traefik ingress)
- **Ingress:** Traefik as LoadBalancer Service, provisions Octavia LB on Jetstream2
- **Data volume:** PVC populated by init container running the existing S3 download script
- **Manifest format:** Helm chart for astrodash application
- **Repo structure:** Single GitLab repo, directories for infrastructure and application
- **Scope:** Full stack from the start (app, DB, Redis, Celery workers, Celery beat, Flower)

## Repo Structure

```
astrodash-gitops/
├── infrastructure/
│   ├── argocd/              # ArgoCD self-management Application
│   ├── traefik/             # Traefik Helm values override
│   ├── cert-manager/        # cert-manager Helm values + ClusterIssuer
│   └── sealed-secrets/      # Sealed Secrets controller Helm values
├── apps/
│   └── astrodash/           # Custom Helm chart
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
│           ├── deployment-app.yaml
│           ├── deployment-celery-worker.yaml
│           ├── deployment-celery-beat.yaml
│           ├── deployment-flower.yaml
│           ├── statefulset-postgres.yaml
│           ├── deployment-redis.yaml
│           ├── service-app.yaml
│           ├── service-postgres.yaml
│           ├── service-redis.yaml
│           ├── service-flower.yaml
│           ├── ingress.yaml          # Traefik IngressRoute
│           ├── certificate.yaml      # cert-manager Certificate
│           ├── pvc-data.yaml
│           ├── pvc-db.yaml
│           ├── pvc-static.yaml
│           ├── configmap.yaml
│           └── sealedsecret.yaml
└── argocd-apps/             # ArgoCD Application CRDs
    ├── traefik.yaml
    ├── cert-manager.yaml
    ├── sealed-secrets.yaml
    └── astrodash.yaml
```

## Resolved Questions

- **Kubernetes namespace:** Dedicated `astrodash` namespace for isolation and easier RBAC.
- **Secrets management:** Bitnami Sealed Secrets, so encrypted secrets can be committed to the GitOps repo safely. Adds `sealed-secrets` as an additional infrastructure component.
- **Flower exposure:** Internal only, accessed via `kubectl port-forward` when needed. No ingress for Flower.
- **Shared data volume:** Use ReadWriteOnce PVC with single-node assumption. All pods that need `astrodash-data` will be scheduled on the same node. Revisit with RWX storage if multi-node scaling is needed.
- **Nginx:** Dropped. Traefik handles ingress/TLS termination and routes directly to gunicorn. Static files served via Django (whitenoise or collectstatic to a shared volume).
- **OIDC:** Disabled initially, use local Django auth only. Configure OIDC later.
- **Cluster size:** Small (1-3 nodes), resource requests/limits should be conservative.
- **CI/CD:** Keep image builds in GitHub Actions, push to GitLab Container Registry. GitOps repo just references images by tag.
- **Floating IP:** Pre-provisioned Jetstream2 floating IP `149.165.154.142` will be assigned to the Octavia load balancer via Traefik's `loadBalancerIP` service spec, so the IP is stable and known in advance.
- **DNS:** A record for `astrodash-dev.scimma.org` → `149.165.154.142` has been requested (ticket submitted ~2026-03-15, expected ~2026-03-16). cert-manager HTTP-01 challenge will retry until DNS resolves — not a blocker for deployment, only for TLS.
