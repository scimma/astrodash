# Error Model

AstroDASH 2.0 returns structured JSON errors with appropriate HTTP status codes, designed to be easy to act on.

## Response shape

Current canonical shape:

```json
{ "detail": "Human-readable error message" }
```

FastAPI validation errors (schema-level) may return a list under `detail`:

```json
{
  "detail": [
    {
      "loc": ["body", "params"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

## Status codes and examples

- 400 Bad Request

  - Classification or processing failed due to invalid input
  - Examples:

    ```json
    { "detail": "Unsupported file format: .foo. Supported formats: FITS, DAT, TXT, LNW" }
    { "detail": "Error reading file: spectrum.txt - could not parse column 2" }
    { "detail": "Classification failed." }
    { "detail": "Spectrum processing failed." }
    { "detail": "Batch processing failed." }
    ```

- 404 Not Found

  - Resource not found (spectrum, model, template, element)
  - Examples:

    ```json
    { "detail": "Model with ID 'abc123' not found." }
    { "detail": "Template not found for SN type 'Ia' and age bin '2 to 6'." }
    { "detail": "Element 'Xe' not found in line list." }
    { "detail": "Spectrum with ID 'abc123' not found." }
    { "detail": "File not found: /path/to/file.txt" }
    ```

- 409 Conflict

  - Conflict with an existing resource
  - Example:

    ```json
    { "detail": "Model with name 'mymodel' already exists." }
    ```

- 422 Unprocessable Entity

  - Validation failed for inputs or files (domain validation)
  - Examples:

    ```json
    { "detail": "File validation failed." }
    { "detail": "Model validation failed." }
    { "detail": "Spectrum validation failed." }
    ```

- 429 Too Many Requests

  - Rate limit exceeded; middleware returns a retry hint
  - Example:

    ```json
    { "detail": "Rate limit exceeded. Please try again later.", "retry_after": 60 }
    ```

    Header: `Retry-After: 60`

- 500 Internal Server Error

  - Unexpected server error or configuration issue
  - Examples:

    ```json
    { "detail": "Internal server error." }
    { "detail": "Module import error: some.module.Missing" }
    { "detail": "Configuration error." }
    { "detail": "Storage error." }
    { "detail": "Model configuration error." }
    { "detail": "OSC service error." }
    ```

## Exception to HTTP mapping

Exceptions in `app/core/exceptions.py` are mapped as follows:

| Exception | HTTP | Notes |
|-----------|------|-------|
| ValidationException (and subclasses) | 422 | Input or file-level validation |
| FileValidationException | 422 | File validation errors |
| ModelValidationException | 422 | Model validation errors |
| SpectrumValidationException | 422 | Spectrum validation errors |
| ClassificationException | 400 | Model inference or prep failed |
| SpectrumProcessingException | 400 | Processing pipeline failed |
| BatchProcessingException | 400 | Zip/file list processing failed |
| ModelNotFoundException | 404 | Missing user model by ID |
| SpectrumNotFoundException | 404 | Missing spectrum by ID |
| TemplateNotFoundException | 404 | No matching template |
| LineListNotFoundException | 404 | Missing line list file |
| ElementNotFoundException | 404 | Unknown element key |
| ResourceNotFoundException | 404 | Missing resource by ID |
| ResourceConflictException / ModelConflictException | 409 | Duplicate names or conflicts |
| ConfigurationException | 500 | Misconfiguration |
| ExternalServiceException / OSCServiceException | 500 | Upstream failure |
| FileNotFoundException | 404 | File not found |
| FileReadException | 400 | Error reading file |
| UnsupportedFileFormatException | 400 | Unsupported file format |
| StorageException | 500 | Storage-related errors |
| ModelConfigurationException | 500 | Model configuration errors |

## Operational limits

- Rate limits: the API enforces per-IP limits; 429 responses include `retry_after` and `Retry-After` header
- File sizes: very large files or ZIPs may be constrained by your gateway/environment; consider chunking batches
- Timeouts: typical requests complete within seconds; batch requests vary with file count and size

## Validation details

- `params` is a JSON string in multipart form with fields like `smoothing`, `knownZ`, `zValue`, `minWave`, `maxWave`, `calculateRlap`, `oscRef`
- `upload-model` requires a TorchScript file (`.pth`/`.pt`), a JSON `class_mapping`, and an `input_shape` (single shape or list of shapes)
- FastAPI may return structured validation errors for malformed requests (see example above)

## Per-endpoint common errors

- Process spectrum (`POST /api/v1/process`)

  ```json
  { "detail": "No valid spectrum data found in file" }
  { "detail": "Unsupported file format: .foo. Supported formats: FITS, DAT, TXT, LNW" }
  ```

- Batch process (`POST /api/v1/batch-process`)

  ```json
  { "detail": "Must provide either zip_file or files parameter." }
  ```

- Template spectrum (`GET /api/v1/template-spectrum`)

  ```json
  { "detail": "Template not found for SN type 'Ia' and age bin '2 to 6'." }
  ```

- Redshift (`POST /api/v1/estimate-redshift`)

  ```json
  { "detail": "File upload is required for redshift estimation." }
  ```

- Models (`POST /api/v1/upload-model`)

  ```json
  { "detail": "Model validation failed: Model output shape [1, 4] does not match class mapping size 5" }
  ```

## Troubleshooting checklist

1. Health: `GET /health` should return status and metrics
2. Minimal repro: run a known-good cURL from the docs
3. File parsing: verify text columns are numeric and within wavelength range
4. Options: confirm `sn_type` and `age_bin` via `/api/v1/analysis-options`
5. OSC: ensure `oscRef` uses `osc-<object>-0` with a valid OSC object name
6. Models: verify TorchScript loads locally; ensure class mapping size matches outputs
7. Rate limits: on 429, respect `Retry-After`

## Getting help

Include the endpoint, timestamp, and full error JSON when reporting an issue. Server logs include detailed traces with timing; match by timestamp and path.
