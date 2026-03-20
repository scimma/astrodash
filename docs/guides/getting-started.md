# Getting Started (v1 API)

Welcome to the AstroDASH 2.0 API! This guide will help you get up and running with spectrum processing and supernova classification in minutes.

## Prerequisites

Before you begin, make sure you have:

- A spectrum file (FITS, DAT, TXT, or LNW format)
- Basic knowledge of HTTP requests
- Your preferred programming language (Python, JavaScript, etc.)

## Quick Start

### Step 1: Check API Health

First, verify that the API is running:

```bash
curl http://localhost:8000/health
```

You should see:

```json
{"status": "healthy"}
```

### Step 2: Process Your First Spectrum

Upload a spectrum file for classification:

```bash
curl -X POST "http://localhost:8000/api/v1/process" \
  -F "file=@your_spectrum.fits" \
  -F 'params={"smoothing": 6, "knownZ": true, "zValue": 0.5}'
```

### Step 3: Understand the Response

The API returns both processed spectrum data and classification results:

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
      }
    ],
    "model_type": "dash_classifier"
  },
  "model_type": "dash"
}
```

## Your First Python Script

Here's a complete Python script to get you started:

```python
import requests
import json

def process_spectrum(file_path, smoothing=6, known_z=False, z_value=None):
    """
    Process a spectrum file and get classification results.
    """
    # Prepare the request
    files = {'file': open(file_path, 'rb')}
    params = {
        'smoothing': smoothing,
        'knownZ': known_z
    }

    if known_z and z_value is not None:
        params['zValue'] = z_value

    data = {'params': json.dumps(params)}

    # Make the request
    response = requests.post('http://localhost:8000/api/v1/process',
                           files=files, data=data)

    if response.status_code == 200:
        result = response.json()
        return result
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        return None

# Example usage
if __name__ == "__main__":
    # Process a spectrum with known redshift
    result = process_spectrum(
        file_path="spectrum.fits",
        smoothing=6,
        known_z=True,
        z_value=0.5
    )

    if result:
        print(f"Top classification: {result['classification']['best_matches'][0]['type']}")
        print(f"Confidence: {result['classification']['best_matches'][0]['confidence']}")
        print(f"Age bin: {result['classification']['best_matches'][0]['age_bin']}")
```

## Your First JavaScript Application

Here's a simple HTML/JavaScript example:

```html
<!DOCTYPE html>
<html>
<head>
    <title>Astrodash API Demo</title>
</head>
<body>
    <h1>Spectrum Classification</h1>
    <input type="file" id="spectrumFile" accept=".fits,.dat,.txt,.lnw">
    <button onclick="processSpectrum()">Process Spectrum</button>
    <div id="result"></div>

    <script>
        async function processSpectrum() {
            const fileInput = document.getElementById('spectrumFile');
            const resultDiv = document.getElementById('result');

            if (!fileInput.files[0]) {
                alert('Please select a file');
                return;
            }

            const formData = new FormData();
            formData.append('file', fileInput.files[0]);
            formData.append('params', JSON.stringify({
                smoothing: 6,
                knownZ: true,
                zValue: 0.5
            }));

            try {
                const response = await fetch('http://localhost:8000/api/v1/process', {
                    method: 'POST',
                    body: formData
                });

                const result = await response.json();

                resultDiv.innerHTML = `
                    <h3>Classification Results</h3>
                    <p><strong>Top Match:</strong> ${result.classification.best_matches[0].type}</p>
                    <p><strong>Confidence:</strong> ${(result.classification.best_matches[0].confidence * 100).toFixed(1)}%</p>
                    <p><strong>Age Bin:</strong> ${result.classification.best_matches[0].age_bin}</p>
                `;
            } catch (error) {
                resultDiv.innerHTML = `<p style="color: red;">Error: ${error.message}</p>`;
            }
        }
    </script>
</body>
</html>
```
