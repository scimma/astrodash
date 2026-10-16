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
| `params` | String (JSON) | Yes | Processing parameters as JSON string (see `/process` endpoint). Must include `modelType` unless `model_id` is supplied. |

The `params` JSON must include `modelType` (one of the active built-in model
ids: `dash`, `1dCNN_z`, `1dCNN_noz`, `latent_z`, `latent_noz`)
unless a `model_id` is supplied. An omitted, unknown, or retired `modelType`
returns `400`, validated against the model registry's active definitions --
the same contract as `/process`. Redshift rules and label spaces match
[Process Spectrum](process-spectrum.md#built-in-models): `1dCNN_z` and
`latent_z` require redshift; `1dCNN_noz` and `latent_noz` do not take redshift
as an input; those four return the five-class set `SN Ia`, `SN Ib/c`, `SN II`,
`SN IIn`, `SLSN-I` (not DASH type+age templates).

### Redshift for a batch

`params.zValue` applies one redshift to every spectrum in the batch.

To vary it per spectrum, send `params.zByFilename`, an object keyed by
filename:

```json
{"modelType": "1dCNN_z", "zByFilename": {"sn_a.dat": 0.01, "sn_b.dat": 0.02}}
```

A zip entry matches on its full archive path first, then on its basename, so
`spectra/sn_a.dat` is matched by `sn_a.dat`. When `zByFilename` is supplied it
must name every spectrum in the batch: a spectrum with no entry, or an entry
naming a file the batch does not contain, returns `400` and names the file.
That is deliberate -- a redshift silently attached to the wrong spectrum
produces a confidently wrong classification rather than an error.

`zByFilename` and `zValue` are not combined; if both are present, the
per-filename map wins.

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
  -F 'params={"modelType": "dash", "smoothing": 6}'
```

### Python

```python
import requests

files = {'zip_file': open('spectra.zip', 'rb')}
data = {'params': '{"modelType": "dash", "smoothing": 6}'}
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

- 400: `modelType` omitted (without `model_id`), unknown, or retired

  ```json
  { "detail": "modelType is required." }
  ```

- 400: Unsupported file types inside ZIP

  ```json
  { "file2.xyz": { "error": "Unsupported file type" } }
  ```
