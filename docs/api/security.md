# Security & Authentication

AstroDASH 2.0 implements several security measures to protect the API and its users.

## Rate Limiting

The API enforces rate limiting to prevent abuse and ensure fair usage. Rate limits are configurable via environment variables.

### Limits

- **Default**: 600 requests per minute per IP address
- **Burst**: Up to 100 requests in a single minute window
- **Scope**: Per-IP basis (not per-endpoint)

### Rate Limit Response

When rate limits are exceeded, the API returns:

```text
HTTP/1.1 429 Too Many Requests
Retry-After: 60
Content-Type: application/json

{
  "detail": "Rate limit exceeded. Please try again later.",
  "retry_after": 60
}
```

### Rate Limit Exceeded

When limits are exceeded:

```text
HTTP/1.1 429 Too Many Requests
Retry-After: 60
Content-Type: application/json

{
  "detail": "Rate limit exceeded. Please try again later.",
  "retry_after": 60
}
```

### Configuration

- **Environment Variables**:

  - `RATE_LIMIT_REQUESTS_PER_MINUTE`: Default 600 requests/minute
  - `RATE_LIMIT_BURST_LIMIT`: Default 100 burst requests

- **Cleanup**: Old rate limit entries are cleaned up every 5 minutes
- **IP Detection**: Supports proxy headers (X-Forwarded-For, X-Real-IP)

### Best Practices

- Implement exponential backoff for retries
- Respect the `Retry-After` header
- Monitor 429 responses in production
- Consider implementing client-side rate limiting
- Note: The API does not provide `X-RateLimit-*` headers

## CORS Configuration

The API supports Cross-Origin Resource Sharing for web applications.

### Allowed Origins

- **Default**: `*` (all origins allowed for API usage)
- **Configurable**: Can be restricted to specific origins via `CORS_ORIGINS` environment variable
- **Development**: Can be set to `http://localhost:3000,http://localhost:3001`
- **Production**: Should be restricted to specific domains for security

### CORS Headers

```text
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS
Access-Control-Allow-Headers: *
Access-Control-Max-Age: 3600
```

### Preflight Requests

OPTIONS requests are automatically handled for:

- File uploads (multipart/form-data)
- JSON payloads
- All custom headers (`allow_headers=["*"]`)
- CORS preflight with 1-hour cache (`max_age=3600`)

## File Upload Security

### File Type Validation

- **Allowed Extensions**: `.fits`, `.dat`, `.txt`, `.lnw`, `.csv`
- **MIME Type Checking**: Server validates actual file content
- **Size Limits**: 50MB per file, 100MB per request

### Malicious File Protection

- File extension validation against allowed types
- File size limits (50MB per file, 100MB per request)
- Basic file content validation during spectrum processing
- Filename sanitization in error messages

### Upload Best Practices

```python
# Validate file before upload
import os
allowed_extensions = {'.fits', '.dat', '.txt', '.lnw', '.csv'}
file_ext = os.path.splitext(filename)[1].lower()

if file_ext not in allowed_extensions:
    raise ValueError(f"Unsupported file type: {file_ext}")

# Check file size
if os.path.getsize(filepath) > 50 * 1024 * 1024:  # 50MB
    raise ValueError("File too large")
```

## Security Headers

The API automatically includes comprehensive security headers:

### Essential Security Headers

```text
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Referrer-Policy: strict-origin-when-cross-origin
```

### HSTS Header (HTTPS only)

```text
Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
```

### Content Security Policy

```text
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self'; connect-src 'self' https:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

### Feature Policy

```text
Permissions-Policy: geolocation=(), microphone=(), camera=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=(), ambient-light-sensor=(), autoplay=()
```

### Additional Security Headers

```text
X-Permitted-Cross-Domain-Policies: none
X-Download-Options: noopen
X-DNS-Prefetch-Control: off
```

## Best Practices for Clients

### 1. Input Sanitization

```python
# Sanitize user inputs before sending
import re

def sanitize_filename(filename):
    # Remove dangerous characters
    safe_name = re.sub(r'[^\w\-_.]', '_', filename)
    # Limit length
    return safe_name[:100]

# Validate parameters
def validate_params(params):
    if 'smoothing' in params:
        smoothing = int(params['smoothing'])
        if not 0 <= smoothing <= 10:
            raise ValueError("Smoothing must be 0-10")
```

### 2. Error Handling

```python
import requests
from requests.exceptions import RequestException

try:
    response = requests.post(url, files=files, data=data)
    response.raise_for_status()
except requests.exceptions.HTTPError as e:
    if e.response.status_code == 429:
        retry_after = int(e.response.headers.get('Retry-After', 60))
        print(f"Rate limited. Retry after {retry_after} seconds")
    else:
        print(f"HTTP error: {e.response.status_code}")
except RequestException as e:
    print(f"Request failed: {e}")
```

### 3. File Upload Security

```python
# Validate files before upload
def validate_spectrum_file(filepath):
    # Check extension
    if not filepath.lower().endswith(('.fits', '.dat', '.txt', '.lnw', '.csv')):
        raise ValueError("Invalid file type")

    # Check file size
    if os.path.getsize(filepath) > 50 * 1024 * 1024:
        raise ValueError("File too large")

    # Check file content (basic)
    with open(filepath, 'rb') as f:
        header = f.read(1024)
        if b'<script>' in header.lower():
            raise ValueError("Potentially malicious file content")
```

**Note**: The backend automatically validates file extensions and sizes. Supported formats are:

- `.fits` - FITS astronomical data files
- `.dat` - ASCII data files
- `.txt` - Text files with spectrum data
- `.lnw` - Log wavelength files
- `.csv` - Comma-separated value files

## Monitoring & Alerting

### Security Events to Monitor

- Rate limit violations
- File upload failures
- Validation errors
- Unusual request patterns

### Log Analysis

```bash
# Monitor rate limit violations
grep "429" /var/log/astrodash/app.log

# Check for suspicious file uploads
grep "File validation failed" /var/log/astrodash/app.log

# Monitor CORS violations
grep "CORS" /var/log/astrodash/app.log

# Note: All logs (access, errors, application) are written to app.log
```
