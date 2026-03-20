# Process Spectrum

Process and classify a single spectrum file or OSC reference.

## Endpoint

```text
POST /api/v1/process
```

## Description

This is the main endpoint for processing and classifying supernova spectra. It accepts either a file upload or an SN name, processes the spectrum according to specified parameters, and returns both the processed spectrum data and classification results.

## Request

### Content-Type

```text
multipart/form-data
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | File | No\* | Spectrum file to upload (FITS, DAT, TXT, or LNW) |
| `params` | String (JSON) | No | Processing parameters as JSON string |

\*Either `file` or `oscRef` in params is required.

### Processing Parameters

The `params` parameter accepts a JSON string with the following fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `oscRef` | String | \- | SN name on Open Supernova Catalog (e.g., "sn2002er") |
| `smoothing` | Integer | 0 | Smoothing parameter |
| `knownZ` | Boolean | false | Whether redshift is known |
| `zValue` | Float | \- | Redshift value (required if knownZ=true) |
| `minWave` | Float | \- | Minimum wavelength in Angstroms |
| `maxWave` | Float | \- | Maximum wavelength in Angstroms |
| `calculateRlap` | Boolean | false | Whether to calculate RLAP values |

## Response

### Success Response

**Status Code:** `200 OK`

```json
{
  "spectrum": {
    "x": [3500.0, 3501.0, "..."],
    "y": [0.1, 0.2, "..."],
    "redshift": 0.05
  },
  "classification": {
    "best_matches": [
      {
        "type": "Ia-norm",
        "confidence": 0.95,
        "age_bin": "4 to 8"
      },
      {
        "type": "Ia-91T",
        "confidence": 0.03,
        "age_bin": "2 to 6"
      }
    ],
    "model_type": "dash_classifier"
  },
  "model_type": "dash"
}
```

### Error Responses

**Status Code:** `400 Bad Request`

```json
{
  "detail": "No spectrum file or OSC reference provided"
}
```

**Status Code:** `500 Internal Server Error`

```json
{
  "detail": "Classification error: Model not found"
}
```

## Examples

### File Upload

#### cURL

```bash
curl -X POST "http://localhost:8000/api/v1/process" \
  -F "file=@spectrum.fits" \
  -F 'params={"smoothing": 6, "knownZ": true, "zValue": 0.5, "calculateRlap": true}'
```

#### Python

```python
import requests

files = {'file': open('spectrum.fits', 'rb')}
data = {
    'params': '{"smoothing": 6, "knownZ": true, "zValue": 0.5}'
}

response = requests.post('http://localhost:8000/api/v1/process',
                        files=files, data=data)
result = response.json()
print(f"Top match: {result['classification']['best_matches'][0]['type']}")
print(f"Confidence: {result['classification']['best_matches'][0]['confidence']:.2f}")
```

#### JavaScript

```javascript
const formData = new FormData();
formData.append('file', fileInput.files[0]);
formData.append('params', JSON.stringify({
  smoothing: 6,
  knownZ: true,
  zValue: 0.5
}));

fetch('http://localhost:8000/api/v1/process', {
  method: 'POST',
  body: formData
})
.then(response => response.json())
.then(data => console.log(data));
```

### OSC Reference

#### cURL

```bash
curl -X POST "http://localhost:8000/api/v1/process" \
  -F 'params={"oscRef": "osc-sn2011fe-0", "smoothing": 4}'
```

#### Python

```python
import requests

data = {
    'params': '{"oscRef": "osc-sn2011fe-0", "smoothing": 4}'
}

response = requests.post('http://localhost:8000/api/v1/process', data=data)
result = response.json()
```

## Processing Details

### Spectrum Processing Pipeline

1. **File Reading**: Supports FITS, DAT, TXT, and LNW formats
2. **Wavelength Range**: Optional filtering by min/max wavelength
3. **Smoothing**: Savitzy-Golay filtering with configurable kernel size
4. **Redshift Correction**: Applies known redshift if provided
5. **Normalization**: Continuum removal and flux normalization
6. **Classification**: ML model prediction with confidence scores
7. **RLAP Calculation**: Optional relative likelihood analysis

### Classification Results

The classification includes:

- **Top Match**: Highest confidence supernova type
- **Confidence**: Probability score (0-1)
- **All Matches**: Complete list of predictions with scores
- **Age Bins**: Temporal classification within each type
- **RLAP Values**: Relative likelihood ratios (if calculated)

## Notes

- **File Size Limit**: 50MB per file
- **Processing Time**: 1-5 seconds depending on spectrum complexity and model used.
- **Supported Formats**: FITS, DAT, TXT, LNW, CSV
- **Wavelength Range**: 3500-10000 Angstroms (configurable)
- **Redshift Range**: 0.0-2.0 (for known redshifts)

## Common Errors

- 400: Unsupported file format

  ```json
  { "detail": "Unsupported file format: .foo. Supported formats: FITS, DAT, TXT, LNW, CSV" }
  ```

- 400: No valid spectrum data found

  ```json
  { "detail": "No valid spectrum data found in file" }
  ```

- 422: Validation error (missing or malformed `params`)

  ```json
  { "detail": "Validation failed." }
  ```
