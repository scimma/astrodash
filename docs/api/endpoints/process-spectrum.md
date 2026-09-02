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
| `modelType` | String | \- | Classifier to use. **Required** unless `model_id` is supplied. Must be one of the active built-in model ids (`dash`, `transformer`, `1dCNN_z`, `1dCNN_noz`, `latent_z`, `latent_noz`); an omitted, unknown, or retired value returns `400`. The valid values track the model registry, so they change as models are added or retired. See [Built-in models](#built-in-models). |
| `oscRef` | String | \- | SN name on Open Supernova Catalog (e.g., "sn2002er") |
| `smoothing` | Integer | 0 | Smoothing parameter |
| `knownZ` | Boolean | false | Whether redshift is known. Required (with `zValue`) for `transformer`, `1dCNN_z`, and `latent_z`. Not used as a model input for `1dCNN_noz` or `latent_noz`. Optional for `dash`. |
| `zValue` | Float | \- | Redshift value (required if `knownZ` is true, and for models that require redshift as an input) |
| `minWave` | Float | \- | Minimum wavelength in Angstroms |
| `maxWave` | Float | \- | Maximum wavelength in Angstroms |
| `calculateRlap` | Boolean | false | Whether to calculate RLAP values (DASH only) |

### Built-in models

All of the following ids are **public** (listed, ungated). Send them as `params.modelType` the same way as `dash` or `transformer`.

| `modelType` | Redshift as input | Labels |
|-------------|-------------------|--------|
| `dash` | Optional | DASH type + age-bin templates (for example `Ia-norm` with an `age_bin`) |
| `transformer` | Required | Transformer class names |
| `1dCNN_z` | Required | Five classes: `SN Ia`, `SN Ib/c`, `SN II`, `SN IIn`, `SLSN-I` |
| `1dCNN_noz` | Not an input | Same five classes |
| `latent_z` | Required | Same five classes |
| `latent_noz` | Not an input | Same five classes |

`1dCNN_*` and `latent_*` are not the original DASH type+age template stack. They do not return DASH age bins, twins, or RLAP.

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
  -F 'params={"modelType": "dash", "smoothing": 6, "knownZ": true, "zValue": 0.5, "calculateRlap": true}'
```

#### Python

```python
import requests

files = {'file': open('spectrum.fits', 'rb')}
data = {
    'params': '{"modelType": "dash", "smoothing": 6, "knownZ": true, "zValue": 0.5}'
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
  modelType: 'dash',
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
  -F 'params={"modelType": "dash", "oscRef": "osc-sn2011fe-0", "smoothing": 4}'
```

#### Python

```python
import requests

data = {
    'params': '{"modelType": "dash", "oscRef": "osc-sn2011fe-0", "smoothing": 4}'
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
- **Age Bins** (`dash` only): Temporal classification within each DASH type
- **RLAP Values** (`dash` only, if `calculateRlap` is true): Relative likelihood ratios

For `1dCNN_z`, `1dCNN_noz`, `latent_z`, and `latent_noz`, `best_matches[].type` is one of `SN Ia`, `SN Ib/c`, `SN II`, `SN IIn`, `SLSN-I`.

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

- 400: `modelType` omitted without a `model_id`

  ```json
  { "detail": "modelType is required." }
  ```

- 400: unknown `modelType` (not a registry model id)

  ```json
  { "detail": "Unknown model type: bogus." }
  ```

- 400: retired `modelType` (a model no longer active in the registry)

  ```json
  { "detail": "Model type transformer is not available." }
  ```

- 422: Validation error (missing or malformed `params`)

  ```json
  { "detail": "Validation failed." }
  ```
