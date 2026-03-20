# Batch Process

Process and classify multiple spectra from a single uploaded ZIP file.

## Endpoint

```text
POST /api/v1/batch-process
```

## Description

This endpoint accepts a ZIP file containing multiple spectrum files, processes and classifies each, and returns results per file.

## Request

### Content-Type

```text
multipart/form-data
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `zip_file` | File | Yes | ZIP file containing spectrum files (FITS, DAT, TXT, LNW) |
| `params` | String (JSON) | No | Processing parameters as JSON string (see `/process` endpoint) |

## Response

### Success Response

**Status Code:** `200 OK`

```json
{
  "file1.txt": {
    "spectrum": {},
    "classification": {}
  },
  "file2.fits": {
    "error": "Unsupported file type"
  }
}
```

### Error Response

**Status Code:** `500 Internal Server Error`

```json
{
  "detail": "Unhandled exception in /api/batch-process: ..."
}
```

## Examples

### cURL

```bash
curl -X POST "http://localhost:8000/api/v1/batch-process" \
  -F "zip_file=@spectra.zip" \
  -F 'params={"smoothing": 6}'
```

### Python

```python
import requests

files = {'zip_file': open('spectra.zip', 'rb')}
data = {'params': '{"smoothing": 6}'}
response = requests.post('http://localhost:8000/api/v1/batch-process', files=files, data=data)
print(response.json())
```

## Notes

- Only supported file types in the ZIP will be processed.
- Each file's result is keyed by its filename.
- Use the `/process` endpoint for single spectrum processing.

## Common Errors

- 400: Must provide either `zip_file` or `files`

  ```json
  { "detail": "Must provide either zip_file or files parameter." }
  ```

- 400: Unsupported file types inside ZIP

  ```json
  { "file2.xyz": { "error": "Unsupported file type" } }
  ```
