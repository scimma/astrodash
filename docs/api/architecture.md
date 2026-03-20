# Architecture Overview

AstroDASH follows a clean, layered architecture for maintainability and testability.

## High-level Layers

1. **API Layer (Django REST Framework)**

   - URL patterns in `app/astrodash/api_urls.py` expose versioned endpoints under `/astrodash/api/v1/`
   - Validation via Django REST Framework serializers; global exception handlers and middleware

2. **Domain Services**

   - Business logic for spectrum processing, classification, redshift, models, batches, templates, line lists
   - Reside in `app/astrodash/domain/services/`

3. **Repositories and Storage**

   - Abstractions for spectrum and model persistence and retrieval
   - File-based storage, Django ORM repositories, and OSC API client
   - Reside in `app/astrodash/domain/repositories/` and `app/astrodash/infrastructure/`

4. **ML Infrastructure**

   - Classifiers (Dash, Transformer, User) and preprocessing utilities
   - Template handling, RLAP computation, and data processing
   - Reside in `app/astrodash/infrastructure/ml/`

5. **Core & Config**

   - Exception handling, middleware, logging, settings
   - Reside in `app/astrodash/shared/` and `app/astrodash/config/`

## Request Flow (example: POST /astrodash/api/v1/process)

1. Django URL router dispatches to the API view, which parses form data (`file`, `params`)
2. `SpectrumService` obtains spectrum data (file or OSC)
3. `SpectrumProcessingService` applies filtering, smoothing, normalization, metadata
4. `ClassificationService` selects appropriate classifier (Dash/Transformer/User) via `ModelFactory`
5. ML classifier runs inference
6. Response is serialized with sanitized numeric types

## Error Handling

All exceptions are mapped to structured JSON errors in `app/astrodash/shared/exceptions.py`, with precise HTTP status codes and sanitized messages.
