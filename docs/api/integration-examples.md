# Integration Examples

This page provides comprehensive examples for integrating AstroDASH 2.0 API with various technologies and platforms, showcasing advanced patterns that go beyond basic API usage.

## Programming Languages

**Note**: The AstroDash API requires specific parameters:

- **params**: JSON string containing processing parameters (required)
- **model_id**: Optional user model ID for custom models
- **File formats**: `.fits`, `.dat`, `.txt`, `.lnw`, `.csv`
- **File size limits**: 50MB per file, 100MB total per request
- **Rate limits**: 600 requests/minute with 100 burst allowance
- **Processing**: All endpoints are async and support concurrent requests

### Python

#### Basic Usage

```python
import requests
import json

# Process a single spectrum
def process_spectrum(file_path):
    with open(file_path, 'rb') as f:
        files = {'file': f}
        data = {
            'params': json.dumps({'smoothing': 3}),
            'model_id': None  # Optional: specify user model ID if using custom model
        }
        response = requests.post(
            'http://localhost:8000/api/v1/process',
            files=files, data=data
        )
        response.raise_for_status()
        return response.json()

# Batch process multiple files
def batch_process(file_paths):
    with zipfile.ZipFile('spectra.zip', 'w') as zipf:
        for file_path in file_paths:
            zipf.write(file_path, os.path.basename(file_path))

    with open('spectra.zip', 'rb') as zip_file:
        files = {'zip_file': zip_file}
        data = {
            'params': '{}',  # Required: processing parameters as JSON string
            'model_id': None  # Optional: specify user model ID if using custom model
        }
        response = requests.post(
            'http://localhost:8000/api/v1/batch-process',
            files=files, data=data
        )
        response.raise_for_status()
        return response.json()
```

### JavaScript/Node.js

#### Basic Usage

```javascript
const axios = require('axios');
const FormData = require('form-data');
const fs = require('fs');

// Process a single spectrum
async function processSpectrum(filePath) {
    const form = new FormData();
    form.append('file', fs.createReadStream(filePath));
    form.append('params', JSON.stringify({ smoothing: 3 }));

    const response = await axios.post(
        'http://localhost:8000/api/v1/process',
        form,
        { headers: form.getHeaders() }
    );
    return response.data;
}

// Batch process with ZIP file
async function batchProcess(filePaths) {
    const JSZip = require('jszip');
    const zip = new JSZip();

    // Add files to ZIP
    for (const filePath of filePaths) {
        const content = fs.readFileSync(filePath);
        zip.file(path.basename(filePath), content);
    }

    const zipBuffer = await zip.generateAsync({ type: 'nodebuffer' });
    const form = new FormData();
    form.append('zip_file', zipBuffer, { filename: 'spectra.zip' });
    form.append('params', '{}');  // Required: processing parameters
    form.append('model_id', '');  // Optional: user model ID

    const response = await axios.post(
        'http://localhost:8000/api/v1/batch-process',
        form,
        { headers: form.getHeaders() }
    );
    return response.data;
}
```

### Go

#### Basic Usage

```go
package main

import (
    "bytes"
    "encoding/json"
    "fmt"
    "io"
    "mime/multipart"
    "net/http"
    "os"
)

type ProcessResponse struct {
    ID       string                 `json:"id"`
    Status   string                 `json:"status"`
    Result   map[string]interface{} `json:"result"`
    Metadata map[string]interface{} `json:"metadata"`
}

func processSpectrum(filePath string) (*ProcessResponse, error) {
    file, err := os.Open(filePath)
    if err != nil {
        return nil, err
    }
    defer file.Close()

    var buf bytes.Buffer
    writer := multipart.NewWriter(&buf)

    part, err := writer.CreateFormFile("file", filePath)
    if err != nil {
        return nil, err
    }

    _, err = io.Copy(part, file)
    if err != nil {
        return nil, err
    }

    params := map[string]interface{}{
        "smoothing": 3,
        "normalize": true,
    }

    paramsJSON, _ := json.Marshal(params)
    writer.WriteField("params", string(paramsJSON))
    writer.WriteField("model_id", "")  // Optional: user model ID
    writer.Close()

    req, err := http.NewRequest("POST", "http://localhost:8000/api/v1/process", &buf)
    if err != nil {
        return nil, err
    }

    req.Header.Set("Content-Type", writer.FormDataContentType())

    client := &http.Client{}
    resp, err := client.Do(req)
    if err != nil {
        return nil, err
    }
    defer resp.Body.Close()

    var result ProcessResponse
    err = json.NewDecoder(resp.Body).Decode(&result)
    return &result, err
}
```

### Rust

#### Basic Usage

```rust
use reqwest;
use tokio;
use serde_json::{json, Value};
use std::fs::File;
use std::io::Read;

#[tokio::main]
async fn process_spectrum(file_path: &str) -> Result<Value, Box<dyn std::error::Error>> {
    let mut file = File::open(file_path)?;
    let mut contents = Vec::new();
    file.read_to_end(&mut contents)?;

    let client = reqwest::Client::new();

    let mut form = reqwest::multipart::Form::new()
        .part("file", reqwest::multipart::Part::bytes(contents)
            .file_name(file_path.to_string()))
        .text("params", json!({
            "smoothing": 3,
            "normalize": true
        }).to_string())
        .text("model_id", "");  // Optional: user model ID

    let response = client
        .post("http://localhost:8000/api/v1/process")
        .multipart(form)
        .send()
        .await?;

    let result: Value = response.json().await?;
    Ok(result)
}
```

## Best Practices

**Note**: The AstroDash API provides structured error responses:

- **422**: Validation errors with detailed field information
- **400**: File errors (unsupported types, size limits)
- **500**: Processing errors (ML model failures)
- **429**: Rate limiting with `Retry-After` header

### Error Handling

- Always check HTTP status codes
- Implement exponential backoff for retries
- Handle rate limiting (429 responses) gracefully
- Log errors with sufficient context for debugging

### Performance Optimization

- Use connection pooling for multiple requests
- Implement caching for frequently accessed data
- Process files in optimal batch sizes
- Use async/await patterns where available

### Security

- Validate file types and sizes before upload
- Sanitize user inputs
- Use HTTPS in production
- Implement proper authentication if required

### Monitoring

- Track request/response times
- Monitor error rates
- Log performance metrics
- Set up alerts for failures

## Implementation Notes

- **Backend**: FastAPI with async/await support throughout
- **File Processing**: Supports ZIP archives and individual file uploads
- **ML Models**: DASH (CNN) and Transformer models with user model support
- **Error Handling**: Structured error responses with HTTP status codes
- **Logging**: Comprehensive request logging with unique IDs
- **Security**: Rate limiting, CORS, security headers, file validation

These examples demonstrate how to build robust, production-ready integrations with the AstroDash API using advanced patterns that go well beyond basic endpoint usage.
