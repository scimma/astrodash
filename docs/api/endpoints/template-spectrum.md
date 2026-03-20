# Template Spectrum

Get a template spectrum from DASH for a given SN type and age bin.

## Endpoint

```text
GET /api/v1/template-spectrum
```

## Description

Returns a template spectrum for a specified supernova type and age bin. Useful for comparison and visualization.

## Request

### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `sn_type` | String | Yes | "Ia" | Supernova type (e.g., "Ia-norm") |
| `age_bin` | String | Yes | "2 to 6" | Age bin (e.g., "4 to 8") |

## Response

### Success Response

**Status Code:** `200 OK`

```json
{
  "x": [3500.0, 3501.0, "..."],
  "y": [0.1, 0.2, "..."]
}
```

### Error Response

**Status Code:** `404 Not Found`

```json
{
  "detail": "Template data file not found."
}
```

## Example

### cURL

```bash
curl -X GET "http://localhost:8000/api/v1/template-spectrum?sn_type=Ia&age_bin=2%20to%206"
```

### Python

```python
import requests
params = {'sn_type': 'Ia', 'age_bin': '2 to 6'}
response = requests.get('http://localhost:8000/api/v1/template-spectrum', params=params)
print(response.json())
```

## Notes

- Use `/api/v1/analysis-options` to get valid SN types and age bins.
- Returned arrays are suitable for plotting or further analysis.

## Common Errors

- 404: Unknown type/age combination

  ```json
  { "detail": "Template not found for SN type 'Ia' and age bin '2 to 6'." }
  ```
