# Changelog

All notable changes to the Astrodash application will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project (mostly) adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Types of changes:
- `Added`: for new features.
- `Changed`: for changes in existing functionality.
- `Deprecated`: for soon-to-be removed features.
- `Removed`: for now removed features.
- `Fixed`: for any bug fixes.
- `Security`: in case of vulnerabilities.

## [Unreleased]

### Added

- Spectral twins explorer data files (embeddings, payload, PCA, UMAP) added to
  S3 initialization manifest.
- Team members page and team-style layout.
- SCiMMA logo in footer alongside SCIMMA link.
- NSF grant acknowledgement text in footer with linked grant numbers.
- Root URL (`/`) redirects to `/astrodash/`.
- Kubernetes deployment support with Helm chart and ArgoCD GitOps
  (separate repository: `astrodash-k8s-gitops`).
- GitHub Actions CI workflow to build and push Docker images to GitLab
  Container Registry.
- Admin documentation for updating S3 data files (`docs/admin/updating-data-files.md`).
- OpenAPI specification rewritten to accurately document all 16 Django API
  endpoints.
- `whitenoise` middleware for serving static files without nginx (required for
  Kubernetes deployment).
- `DJANGO_ALLOWED_HOSTS` environment variable to configure allowed hosts
  (defaults to `*` for backward compatibility).
- `api_writes_required` decorator to temporarily disable API write endpoints
  (POST/PUT/DELETE) until IAM is implemented. Controlled by
  `ASTRODASH_API_WRITES_ENABLED` environment variable (default: `false`).

### Changed

- Docker image reduced from ~9GB to ~1.8GB by using `python:3.11-slim` base
  image and CPU-only PyTorch.
- Footer contact email changed from `devnull@example.com` to `support@scimma.org`.
- Footer institution link changed from University of Hawai'i IfA to MIT Kavli.
- README.md expanded to serve as the primary project entry point with architecture
  overview, deployment URLs, and links to documentation sections.
- All documentation converted from RST/Sphinx to plain markdown, reorganized
  by audience (`docs/api/`, `docs/guides/`, `docs/developer/`, `docs/admin/`,
  `docs/acknowledgements/`).
- Data manifest updated for Python 3.11 compatibility.

### Removed

- Sphinx documentation build system (conf.py, Makefile, Dockerfile,
  requirements.txt, docker-compose.docs.yaml).
- Read the Docs configuration (`.readthedocs.yml`).
- Pre-commit hooks disabled pending review (`.pre-commit-config.yaml`).

### Fixed

- Remaining Blast references in code comments updated to AstroDash.
- Stale FastAPI references in API architecture and advanced usage documentation
  corrected to Django.
- Incorrect `prod_backend/` file paths in contribution guide corrected to
  `app/astrodash/`.
- Stale OpenAPI specification (FastAPI-generated) replaced with accurate
  Django endpoint documentation.

### Security

- API write endpoints (process, estimate-redshift, upload-model, delete-model,
  update-model, batch-process) disabled by default until identity and access
  management is implemented. Web UI upload functionality is unaffected.

## [1.0.0]

### Changed

- Forked from the Blast repository and excised all Blast-specific code to create
  a standalone Astrodash application.
- Renamed Django project from `app` to `astrodash_project`.
- Renamed DevOps control script from `blastctl` to `astrodashctl`.
- Replaced Blast branding with Astrodash branding throughout templates and configuration.
- Trimmed Python dependencies to only those required by Astrodash.

### Removed

- Removed Blast host galaxy analysis application (`host/`).
- Removed Blast REST API (`api/`).
- Removed Blast batch processing scripts (`batch/`).
- Removed Blast data directories and initialization (`data/`, `validation/`).
- Removed Blast-specific Docker services (MinIO object store, batch worker).
- Removed Blast-specific environment variables and configuration.
- Removed Blast-specific Python packages from requirements.
