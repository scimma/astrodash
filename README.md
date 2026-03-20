# AstroDash

AstroDash is a Django web application for ML-based supernova spectrum
classification using deep learning. It provides both a web interface and a
REST API for classifying astronomical spectra using DASH CNN, Transformer, and
user-uploaded models.

## Features

- **Single spectrum classification** — upload a spectrum file or reference a
  supernova by name from the Open Supernova Catalog
- **Batch processing** — classify multiple spectra via ZIP file upload
- **Multiple classifier models** — DASH CNN, Transformer, and user-uploaded
  TorchScript models
- **Spectral twins explorer** — find similar spectra using embeddings and UMAP
- **Redshift estimation** — estimate redshift using DASH templates
- **REST API** — full API for programmatic access

## Architecture

AstroDash follows a layered architecture:

- **Web layer** — Django views and templates with Bootstrap for the interactive
  UI
- **API layer** — Django REST Framework endpoints under `/astrodash/api/v1/`
- **Domain services** — business logic for spectrum processing, classification,
  and model management
- **ML infrastructure** — PyTorch-based classifiers, preprocessing, and
  template handling
- **Async processing** — Celery workers with Redis for batch classification
  tasks

The application is containerized with Docker and deployed to Kubernetes on
Jetstream2 using ArgoCD for GitOps.

## Deployments

| Environment | URL |
|------------|-----|
| Production | https://astrodash.scimma.org |
| Development | https://astrodash-dev.scimma.org |

## Documentation

- [API Reference](docs/api/introduction.md) — endpoints, data formats, error
  handling
- [User Guides](docs/guides/getting-started.md) — getting started, code
  examples
- [Developer Guide](docs/developer/getting-started.md) — local setup,
  contributing, running tests
- [Admin Guide](docs/admin/updating-data-files.md) — managing data files and
  deployments
- [Contributing a Classifier](docs/guides/contributing-classifiers.md) — how
  to add a new ML model

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines and the
[Developer Guide](docs/developer/getting-started.md) for local setup
instructions.

## Acknowledgements

This project is supported by National Science Foundation grants
[OAC-1841625](https://www.nsf.gov/awardsearch/showAward?AWD_ID=1841625),
[OAC-1934752](https://www.nsf.gov/awardsearch/showAward?AWD_ID=1934752),
[OAC-2311355](https://www.nsf.gov/awardsearch/showAward?AWD_ID=2311355),
[AST-2432428](https://www.nsf.gov/awardsearch/showAward?AWD_ID=2432428).

AstroDash builds on the [DASH](https://github.com/daniel-perrett/astrodash)
spectral classification tool and the original
[Blast](https://github.com/astrophpeter/blast) web application.

See [acknowledgements](docs/acknowledgements/) for full contributor, data
source, and software credits.
