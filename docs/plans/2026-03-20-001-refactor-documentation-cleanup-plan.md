---
title: "refactor: Documentation Cleanup and RST-to-Markdown Conversion"
type: refactor
status: active
date: 2026-03-20
origin: docs/brainstorms/2026-03-20-documentation-refactor-requirements.md
---

# refactor: Documentation Cleanup and RST-to-Markdown Conversion

## Overview

Remove Sphinx and its build infrastructure, convert all RST documentation to markdown with correctness review, reorganize the docs/ directory by audience, and expand the README as the project entry point. (See origin: `docs/brainstorms/2026-03-20-documentation-refactor-requirements.md`)

## Implementation Phases

### Phase 1: Remove Sphinx Infrastructure

Delete the Sphinx build system and related files:

- [ ] `docs/conf.py`
- [ ] `docs/Makefile`
- [ ] `docs/make.bat`
- [ ] `docs/Dockerfile`
- [ ] `docs/requirements.txt`
- [ ] `docs/index.rst`
- [ ] `docs/astrodash/index.rst`
- [ ] `docs/_static/` (entire directory — contains PR workflow screenshots)
- [ ] `docker/docker-compose.docs.yaml`

### Phase 2: Convert RST Files to Markdown

Convert each RST file to markdown. Files marked STALE or PARTIALLY_STALE need content fixes during conversion.

#### API Documentation → `docs/api/`

| Source RST | Target MD | Status | Fixes Needed |
|-----------|-----------|--------|--------------|
| `astrodash/api/intro.rst` | `docs/api/introduction.md` | PARTIALLY_STALE | Update base URL placeholder (line 22) |
| `astrodash/api/architecture_overview.rst` | `docs/api/architecture.md` | **STALE** | Replace all FastAPI references with Django. Fix paths from `app/api/v1/` to `app/astrodash/`. Remove FastAPI dependency injection content. |
| `astrodash/api/errors.rst` | `docs/api/errors.md` | CURRENT | Direct conversion |
| `astrodash/api/security.rst` | `docs/api/security.md` | CURRENT | Direct conversion |
| `astrodash/api/advanced_usage.rst` | `docs/api/advanced-usage.md` | **STALE** | Replace "FastAPI and uses async/await" claims with Django. Fix framework-specific patterns. Keep generic caching/monitoring content. |
| `astrodash/api/integration_examples.rst` | `docs/api/integration-examples.md` | CURRENT | Direct conversion |
| `astrodash/api/troubleshooting.rst` | `docs/api/troubleshooting.md` | CURRENT | Direct conversion |
| `astrodash/api/data_formats.rst` | `docs/api/data-formats.md` | CURRENT | Direct conversion |
| `astrodash/api/swagger.rst` | `docs/api/openapi.md` | CURRENT | Direct conversion |
| `astrodash/api/openapi.json` | `docs/api/openapi.json` | PARTIALLY_STALE | Review completeness against actual endpoints |

#### API Endpoint Documentation → `docs/api/endpoints/`

All endpoint docs are CURRENT. Direct RST-to-markdown conversion:

- [ ] `endpoints/health.rst` → `docs/api/endpoints/health.md`
- [ ] `endpoints/process_spectrum.rst` → `docs/api/endpoints/process-spectrum.md`
- [ ] `endpoints/batch_process.rst` → `docs/api/endpoints/batch-process.md`
- [ ] `endpoints/analysis_options.rst` → `docs/api/endpoints/analysis-options.md`
- [ ] `endpoints/template_spectrum.rst` → `docs/api/endpoints/template-spectrum.md`
- [ ] `endpoints/estimate_redshift.rst` → `docs/api/endpoints/estimate-redshift.md`
- [ ] `endpoints/line_list.rst` → `docs/api/endpoints/line-list.md`
- [ ] `endpoints/models.rst` → `docs/api/endpoints/models.md`

#### User Guides → `docs/guides/`

| Source RST | Target MD | Status | Fixes Needed |
|-----------|-----------|--------|--------------|
| `astrodash/guides/getting_started.rst` | `docs/guides/getting-started.md` | CURRENT | Direct conversion |
| `astrodash/guides/contribute.rst` | `docs/guides/contributing-classifiers.md` | **PARTIALLY_STALE** | Fix all `prod_backend/` paths to `app/astrodash/` |
| `astrodash/guides/code_examples/python.rst` | `docs/guides/python-examples.md` | CURRENT | Direct conversion |

#### Developer Documentation → `docs/developer/`

| Source RST | Target MD | Status | Fixes Needed |
|-----------|-----------|--------|--------------|
| `developer_guide/dev_getting_started.rst` | `docs/developer/getting-started.md` | CURRENT | Direct conversion. Also incorporate quickstart content currently in README.md. |
| `developer_guide/dev_documentation.rst` | `docs/developer/documentation.md` | CURRENT | Convert but update to reflect new markdown-only approach (no more Sphinx build). |

#### Acknowledgements → `docs/acknowledgements/`

| Source RST | Target MD | Status | Fixes Needed |
|-----------|-----------|--------|--------------|
| `acknowledgements/contributors.rst` | `docs/acknowledgements/contributors.md` | CURRENT | Direct conversion |
| `acknowledgements/data_sources.rst` | `docs/acknowledgements/data-sources.md` | CURRENT | Direct conversion |
| `acknowledgements/software.rst` | `docs/acknowledgements/software.md` | CURRENT | Direct conversion |

### Phase 3: Clean Up Old RST Directories

After conversion, remove the old RST source directories:

- [ ] `docs/astrodash/` (entire directory)
- [ ] `docs/developer_guide/` (entire directory — replaced by `docs/developer/`)
- [ ] `docs/acknowledgements/` old RST files (replaced by markdown versions)

### Phase 4: Expand README.md

Rewrite README.md as the project entry point:

- [ ] Project description and purpose
- [ ] Architecture overview (brief — link to `docs/api/architecture.md` for details)
- [ ] Links to documentation sections:
  - API documentation (`docs/api/`)
  - User guides (`docs/guides/`)
  - Developer documentation (`docs/developer/`)
  - Admin guides (`docs/admin/`)
- [ ] Deployment overview (link to GitOps repo)
- [ ] Acknowledgements and funding (brief — link to `docs/acknowledgements/`)
- [ ] Do NOT include quickstart (that lives in `docs/developer/getting-started.md`)

### Phase 5: Fix Code Comments

- [ ] `app/astrodash/infrastructure/django_repositories.py:15` — Change "Blast Django database" to "AstroDash Django database"
- [ ] `app/astrodash/ui_views.py:73` — Change "Blast-style layout" to "team-style layout"

## Acceptance Criteria

- [ ] No RST files remain in the repository
- [ ] No Sphinx build infrastructure remains (conf.py, Makefile, Dockerfile, requirements.txt)
- [ ] All documentation is markdown, organized into: `docs/api/`, `docs/guides/`, `docs/developer/`, `docs/admin/`, `docs/acknowledgements/`, `docs/brainstorms/`, `docs/plans/`
- [ ] README.md serves as project overview with links to documentation sections
- [ ] No stale FastAPI references (architecture_overview, advanced_usage)
- [ ] No incorrect `prod_backend/` paths (contribute)
- [ ] Quickstart moved from README to `docs/developer/getting-started.md`
- [ ] `docs/developer/documentation.md` updated to describe markdown workflow (no Sphinx)
- [ ] Two Blast code comments fixed
- [ ] Application continues to work unchanged

## Target Directory Structure

```
docs/
├── api/
│   ├── introduction.md
│   ├── architecture.md
│   ├── errors.md
│   ├── security.md
│   ├── advanced-usage.md
│   ├── integration-examples.md
│   ├── troubleshooting.md
│   ├── data-formats.md
│   ├── openapi.md
│   ├── openapi.json
│   └── endpoints/
│       ├── health.md
│       ├── process-spectrum.md
│       ├── batch-process.md
│       ├── analysis-options.md
│       ├── template-spectrum.md
│       ├── estimate-redshift.md
│       ├── line-list.md
│       └── models.md
├── guides/
│   ├── getting-started.md
│   ├── contributing-classifiers.md
│   └── python-examples.md
├── developer/
│   ├── getting-started.md
│   └── documentation.md
├── admin/
│   └── updating-data-files.md
├── acknowledgements/
│   ├── contributors.md
│   ├── data-sources.md
│   └── software.md
├── brainstorms/    (unchanged)
└── plans/          (unchanged)
```

## Sources

- **Origin document:** [docs/brainstorms/2026-03-20-documentation-refactor-requirements.md](docs/brainstorms/2026-03-20-documentation-refactor-requirements.md) — Key decisions: drop Sphinx, all markdown, keep brainstorms/plans, expand README without quickstart
- **RST correctness audit:** 14 files CURRENT, 4 PARTIALLY_STALE, 3 STALE (FastAPI references)
- **Stale files requiring content fixes:** `architecture_overview.rst`, `advanced_usage.rst`, `contribute.rst`
