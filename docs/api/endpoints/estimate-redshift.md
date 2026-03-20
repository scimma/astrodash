# Estimate Redshift

Estimate the redshift for a given spectrum and SN type/age via template matching and cross-correlation.

## Endpoint

```text
POST /api/v1/estimate-redshift
```

## Description

Estimates the redshift of an input spectrum using template matching for a specified SN type and age bin.

## Request

### Content-Type

```text
multipart/form-data
```

### Form Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | File | Yes | Spectrum file (.dat, .txt, .lnw) |
| `sn_type` | String | Yes | SN type (e.g., "Ia") |
| `age_bin` | String | Yes | Age bin (e.g., "2 to 6") |

## Response

### Success Response

**Status Code:** `200 OK`

```json
{
  "estimated_redshift": 0.123,
  "estimated_redshift_error": 0.005,
  "message": "No valid templates found for this type/age."
}
```

## Example

### Python

```python
import requests

files = {'file': open('spectrum.dat', 'rb')}
data = {'sn_type': 'Ia', 'age_bin': '2 to 6'}
response = requests.post('http://localhost:8000/api/v1/estimate-redshift', files=files, data=data)
print(response.json())
```

### cURL

```bash
curl -X POST "http://localhost:8000/api/v1/estimate-redshift" \
  -F "file=@spectrum.dat" \
  -F "sn_type=Ia" \
  -F "age_bin=2 to 6"
```

## Notes

- Use `/api/v1/analysis-options` to get valid SN types and age bins.

## Common Errors

- 422: File required

  ```json
  { "detail": "File upload is required for redshift estimation." }
  ```

- 404: Unknown type/age combination

  ```json
  { "detail": "Template not found for SN type 'Ia' and age bin '2 to 6'." }
  ```

- The `message` field is present if no valid templates are found.
