# Brainstorm: Multi-Environment GitOps for Dev and Prod

**Date:** 2026-03-18
**Status:** Ready for planning

## What We're Building

Extend the existing GitOps setup to manage both a development deployment (`astrodash-dev.scimma.org`) and a production deployment (`astrodash.scimma.org`) of the Astrodash web application. Each environment runs on its own k3s cluster on Jetstream2 with its own ArgoCD instance.

## Why This Approach

**Chosen: Single GitOps repo with shared Helm templates and per-environment values files**

Both environments run the same application with the same Kubernetes resource structure. The only differences are configuration values: hostname, image tag, secrets, and resource limits. Sharing templates ensures consistency and avoids duplication.

```
astrodash-k8s-gitops/
├── apps/astrodash/
│   ├── Chart.yaml
│   ├── values.yaml            # shared defaults
│   ├── values-dev.yaml        # dev: image tag, hostname, resources
│   ├── values-prod.yaml       # prod: image tag, hostname, resources
│   └── templates/             # shared across both environments
├── argocd-apps/
│   ├── astrodash-dev.yaml     # ArgoCD app for dev cluster
│   ├── astrodash-prod.yaml    # ArgoCD app for prod cluster
│   ├── traefik.yaml           # shared infra (identical on both)
│   ├── cert-manager.yaml      # shared infra
│   └── sealed-secrets.yaml    # shared infra
```

Each cluster's ArgoCD reads from the same repo but references different value files via the ArgoCD Application spec:
```yaml
helm:
  valueFiles:
    - values.yaml
    - values-dev.yaml   # or values-prod.yaml
```

**Rejected alternatives:**
- **Separate directories per environment:** Template duplication, drift risk, every fix applied twice.
- **Separate repos:** Maximum isolation but overkill for a small team. Same duplication problems.

## Key Decisions

- **Cluster architecture:** Separate k3s clusters on Jetstream2, same setup for both
- **ArgoCD:** Separate instance per cluster (fully independent, no cross-cluster management)
- **GitOps repo:** Single repo (`astrodash-k8s-gitops`), shared templates, per-environment values
- **Image tags:** Dev runs dev/latest tags, prod runs stable release tags (e.g., `v1.0.0`)
- **Config differences:** Hostname, image tag, secrets, resource limits, CORS origins, CSRF origins
- **Infrastructure (Traefik, cert-manager, Sealed Secrets):** Mostly identical config, shared ArgoCD Application CRDs applied to each cluster independently
- **Secrets:** Sealed separately per cluster (each cluster has its own Sealed Secrets controller keys)

## What Differs Between Environments

| Setting | Dev | Prod |
|---------|-----|------|
| `image.tag` | `dev2-v1.0.0` (moving tag) | `v1.0.0` (stable release) |
| `ingress.host` | `astrodash-dev.scimma.org` | `astrodash.scimma.org` |
| `DJANGO_ALLOWED_HOSTS` | `astrodash-dev.scimma.org` | `astrodash.scimma.org` |
| `DJANGO_HOSTNAMES` | `astrodash-dev.scimma.org` | `astrodash.scimma.org` |
| `CORS_ALLOWED_ORIGINS` | `https://astrodash-dev.scimma.org` | `https://astrodash.scimma.org` |
| `DJANGO_DEBUG` | `false` | `false` |
| `image.pullPolicy` | `Always` (moving tag) | `IfNotPresent` (stable tag) |
| Resource limits | Conservative (dev cluster) | May be larger (prod cluster) |
| Sealed secrets | Sealed with dev cluster keys | Sealed with prod cluster keys |

## Resolved Questions

- **Prod cluster type:** Same k3s setup on Jetstream2 as dev.
- **Prod cluster status:** Provisioned as of 2026-03-19.
- **Floating IP for prod:** Already provisioned. DNS for `astrodash.scimma.org` not yet configured.
- **Infrastructure config:** Mostly identical across environments. ArgoCD Application CRDs for Traefik, cert-manager, and Sealed Secrets can be reused as-is on the prod cluster.

## Lessons Learned from Dev Deployment

These should be incorporated when bootstrapping the prod cluster:

- **ArgoCD Helm values:** Must include custom Ingress health check (hostPort mode has no LB status). Use `infrastructure/argocd/values.yaml` when installing.
- **Traefik:** Uses DaemonSet with `hostPort` 80/443, not LoadBalancer. Service is disabled. Needs toleration for control-plane taint.
- **Cinder StorageClass:** Both PVCs use `storageClassName: cinder` with `reclaimPolicy: Retain`.
- **Resource limits:** Celery beat and flower need at least 512Mi memory limit. Web and celery worker need at least 1536Mi.
- **Readiness probes:** Use `/admin/login/` with Host header (root `/` returns 404, bare requests return 400 from ALLOWED_HOSTS).
- **wait-for-it.sh:** Needs `chmod +x` before execution (script not executable in image).
- **Sealed secrets:** Must be re-sealed per cluster (different controller keys).
- **ClusterIssuer:** May need manual `kubectl apply` if ArgoCD sync wave doesn't apply it in time. Use `letsencrypt-production` directly.
