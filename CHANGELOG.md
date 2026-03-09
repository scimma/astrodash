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
