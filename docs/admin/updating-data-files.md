# Updating Astrodash Data Files

This guide walks through the process of adding new data files or updating
existing data files that are downloaded from the Jetstream2 S3 bucket during
container initialization.

## Overview

Astrodash data files are stored in an S3-compatible bucket on Jetstream2 and
downloaded to `/mnt/astrodash-data/` when the application container starts. A
JSON manifest (`app/entrypoints/astrodash-data.json`) tracks the files, their
versions, and etag checksums. When the container starts, the initialization
script compares local files against the manifest and downloads any that are
missing or have changed.

### Data file categories

| Category | S3 path prefix | Local path | Description |
|----------|---------------|------------|-------------|
| Pre-trained models | `init/data/pre_trained_models/` | `/mnt/astrodash-data/pre_trained_models/` | PyTorch models for DASH and Transformer classifiers |
| Spectra | `init/data/spectra/` | `/mnt/astrodash-data/spectra/` | JSON spectrum example files |
| Explorer | `init/data/explorer/` | `/mnt/astrodash-data/explorer/` | Spectral twins embeddings, PCA, and UMAP data |
| User models | `init/data/user_models/` | `/mnt/astrodash-data/user_models/` | User-uploaded trained models |

### S3 bucket configuration

| Setting | Value |
|---------|-------|
| Endpoint | `https://js2.jetstream-cloud.org:8001` |
| Bucket | `astrodash` |
| Root path | `init/data/` |
| Read access | Anonymous (no credentials required) |
| Write access | Requires credentials (`ASTRODASH_S3_ACCESS_KEY_ID` and `ASTRODASH_S3_SECRET_ACCESS_KEY`) |

## Prerequisites

- The MinIO client (`mc`) installed and configured with an alias for the
  Jetstream2 S3 endpoint (referred to as `js-blast` below)
- Write credentials for the `astrodash` S3 bucket
- A running Docker Compose environment or access to the application container
  (for regenerating the manifest)
- Access to the Kubernetes clusters (for forcing re-downloads in dev and prod)

## Step 1: Upload files to S3

### Adding new files

Upload the new files to the appropriate path under `init/data/` in the
`astrodash` bucket:

```bash
mc cp /path/to/new/file1 /path/to/new/file2 \
  js-blast/astrodash/init/data/<category>/
```

For example, to add new explorer data files:

```bash
mc cp /path/to/new_embeddings.npy /path/to/new_payload.json \
  js-blast/astrodash/init/data/explorer/
```

### Updating existing files

Upload the replacement files to the same path. The S3 bucket uses versioning,
so the old version is retained but the new version becomes the latest:

```bash
mc cp /path/to/updated_model.pth \
  js-blast/astrodash/init/data/pre_trained_models/dash/pytorch_model.pth
```

### Verify the upload

List the uploaded files to confirm they are in place:

```bash
mc ls js-blast/astrodash/init/data/<category>/
```

## Step 2: Regenerate the manifest

The manifest must be regenerated from the S3 bucket contents so that the
initialization script knows about the new or updated files.

Run the manifest command inside the application container. If you have a
running Docker Compose environment:

```bash
docker exec <app-container-name> python entrypoints/initialize_data.py manifest
```

For example:

```bash
docker exec astrodash-dev-app-1 python entrypoints/initialize_data.py manifest
```

Then copy the updated manifest from the container to the host:

```bash
docker cp <app-container-name>:/app/entrypoints/astrodash-data.json \
  app/entrypoints/astrodash-data.json
```

If you do not have a running container but have the `minio` Python package
installed locally:

```bash
cd app
ASTRODASH_S3_ENDPOINT_URL=https://js2.jetstream-cloud.org:8001 \
ASTRODASH_S3_ACCESS_KEY_ID=<your-access-key> \
ASTRODASH_S3_SECRET_ACCESS_KEY=<your-secret-key> \
ASTRODASH_S3_BUCKET=astrodash \
python entrypoints/initialize_data.py manifest
```

## Step 3: Verify the manifest

Confirm that the manifest contains entries for the new or updated files:

```bash
grep '<filename>' app/entrypoints/astrodash-data.json
```

Each entry should include the file path, version ID, etag, and size:

```json
{
  "path": "explorer/dash_twins_embeddings.npy",
  "version_id": "alzVOKhe8vKa3B-tGx9CMZmA59zwN9J",
  "etag": "21a135a84bcdb98aa1ffb655d5cbdaca",
  "size": 15630464
}
```

## Step 4: Commit and push the updated manifest

```bash
git add app/entrypoints/astrodash-data.json
git commit -m "feat: update data manifest for <description of changes>"
git push origin main
```

If working on a feature branch, create a pull request and merge to `main`.

## Step 5: Build and push a new container image

The updated manifest is baked into the container image, so a new image must be
built and pushed for the changes to take effect.

### Option A: Trigger the GitHub Actions workflow

Create and push a new version tag:

```bash
git tag dev<N>-v<X.Y.Z>
git push origin dev<N>-v<X.Y.Z>
```

The GitHub Actions workflow will build the image and push it to the GitLab
Container Registry.

### Option B: Build and push manually

```bash
docker build -t registry.gitlab.com/ncsa-caps-rse/astrodash-k8s-gitops:<tag> \
  -f app/Dockerfile app/
docker push registry.gitlab.com/ncsa-caps-rse/astrodash-k8s-gitops:<tag>
```

## Step 6: Update the local Docker Compose environment

If you are running a local Docker Compose development environment, the data
files are stored in a Docker volume that persists across container restarts.

### Force re-download of data files

Stop the containers, remove the data volume, and restart:

```bash
bash run/astrodashctl <profile> down
docker volume rm astrodash-data
bash run/astrodashctl <profile> up
```

The containers will re-download all data files from S3 on the next start.

Alternatively, if you only want to re-download changed files without removing
the entire volume, exec into the running container and run the download
command:

```bash
docker exec <app-container-name> python entrypoints/initialize_data.py download
```

The initialization script compares local etags against the manifest and only
re-downloads files that have changed.

## Step 7: Update the Kubernetes environments

### Update the image tag in the GitOps repo

If you built a new image with a new tag, update the image tag in the
appropriate values file in the `astrodash-k8s-gitops` repository:

For dev:

```bash
# Edit apps/astrodash/values-dev.yaml
# Change image.tag to the new tag
cd /path/to/astrodash-k8s-gitops
git add apps/astrodash/values-dev.yaml
git commit -m "chore: update dev image tag to <new-tag>"
git push
```

For prod:

```bash
# Edit apps/astrodash/values-prod.yaml
# Change image.tag to the new tag
cd /path/to/astrodash-k8s-gitops
git add apps/astrodash/values-prod.yaml
git commit -m "chore: update prod image tag to <new-tag>"
git push
```

ArgoCD will auto-sync and deploy pods with the new image. The init container
will compare the local data files against the updated manifest and download
any new or changed files.

### Force re-download with the same image tag

If the image tag has not changed (e.g., you are using a moving tag like
`dev2-v1.0.0`), ArgoCD will not detect a change. Force a rollout restart to
pull the new image and re-run the init containers:

**Dev cluster:**

```bash
KUBECONFIG=/path/to/kubeconfig.dev \
  kubectl rollout restart deployment astrodash-web -n astrodash
```

**Prod cluster:**

```bash
KUBECONFIG=/path/to/kubeconfig.prod \
  kubectl rollout restart deployment astrodash-web -n astrodash
```

The new pod's init container (`init-data`) will run the download script. Since
the data PVC persists, only files with changed etags will be re-downloaded.

### Delete the data PVC to force a full re-download

If you need to force a complete re-download of all files (e.g., to remove
deleted files from the local volume):

```bash
KUBECONFIG=/path/to/kubeconfig.<env> \
  kubectl delete pvc astrodash-data -n astrodash
```

Then restart the deployment:

```bash
KUBECONFIG=/path/to/kubeconfig.<env> \
  kubectl rollout restart deployment astrodash-web -n astrodash
```

A new PVC will be provisioned automatically and all files will be downloaded
fresh.

**Note:** The celery worker and celery beat pods also mount the data PVC. They
will restart automatically when the PVC is recreated.

## Troubleshooting

### Check init container logs

To see if files were downloaded during container startup:

```bash
# Docker Compose
docker logs <app-container-name> 2>&1 | grep -i "download\|integrity"

# Kubernetes
KUBECONFIG=/path/to/kubeconfig.<env> \
  kubectl logs -n astrodash -l component=web -c init-data
```

### Verify data files in a running container

```bash
# Docker Compose
docker exec <app-container-name> ls /mnt/astrodash-data/<category>/

# Kubernetes
KUBECONFIG=/path/to/kubeconfig.<env> \
  kubectl exec -n astrodash deployment/astrodash-web -c web -- \
  ls /mnt/astrodash-data/<category>/
```

### Run integrity verification without downloading

```bash
docker exec <app-container-name> python entrypoints/initialize_data.py verify
```

This checks all local files against the manifest and exits with code 1 if any
files are missing or have mismatched checksums.
