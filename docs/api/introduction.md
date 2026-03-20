# AstroDASH API

AstroDASH is an API for supernovae spectra classification using machine learning models.

- **Fast and reliable classification** (DASH CNN, Transformer, user models)
- **Single and batch processing** with multiple file formats or SN names
- **Strong contracts** with versioned REST endpoints and thorough docs

Explore the docs to get started, or jump to the [Interactive API Explorer](openapi.md). The v1 API lives under the `/astrodash/api/v1` prefix.

---

## Purpose

AstroDash serves astronomers, astrophysicists, machine learning researchers, and tool builders who need robust, reproducible classification of supernova spectra and related analysis. The API is optimized for interactive web apps and automated pipelines alike.

## Base URL and Versioning

- Base URL: `https://astrodash.scimma.org` (production) or `https://astrodash-dev.scimma.org` (development)
- API prefix: `/astrodash/api/v1`
- Versioning policy: breaking changes are introduced only in new major versions (e.g., `/api/v2`). Backward-compatible changes can ship in minor versions.

## Quick Start

1. Health check: `GET /health`
2. Classify one spectrum: `POST /astrodash/api/v1/process` with `file` (or `params.oscRef`)
3. Explore and test endpoints: Swagger UI at `/docs`, OpenAPI at `/openapi.json`

## API Surface Summary

- **Spectrum and Classification**
  - `POST /astrodash/api/v1/process`: classify a single spectrum (file or OSC reference)

- **Batch**
  - `POST /astrodash/api/v1/batch-process`: classify a ZIP folder or multiple files

- **Templates**
  - `GET /astrodash/api/v1/analysis-options`: list valid SN types and age bins for DASH
  - `GET /astrodash/api/v1/template-spectrum`: return a DASH template spectrum (`x`, `y`)

- **Line Lists**
  - `GET /astrodash/api/v1/line-list[...]`: get a list of single element or compound wavelength markers

- **User Models**
  - `POST /astrodash/api/v1/upload-model`: upload TorchScript model with metadata
  - `GET/PUT/DELETE /astrodash/api/v1/models/...`: manage models

- **Redshift**
  - `POST /astrodash/api/v1/estimate-redshift`: estimate redshift (using DASH templates)

## Next Steps

- [Getting Started Guide](../guides/getting-started.md)
- [Architecture Overview](architecture.md)
- [Error Model](errors.md)
- [API Endpoints Reference](endpoints/health.md)
- [Code Examples](../guides/python-examples.md)
- [Interactive API Explorer](openapi.md)
