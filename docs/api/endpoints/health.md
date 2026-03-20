# Health Check

Check the health status of the Astrodash API.

## Endpoint

```text
GET /
GET /health
```

## Description

Returns the current health status of the API server. This endpoint is useful for monitoring and ensuring the API is running correctly.

## Request

No parameters required.

## Response

### Success Response

**Status Code:** `200 OK`

```json
{
  "status": "healthy"
}
```

## Example

### cURL

```bash
curl -X GET "http://localhost:8000/health"
```

### Python

```python
import requests

response = requests.get("http://localhost:8000/health")
print(response.json())
# Output: {'status': 'healthy', ...}
```

### JavaScript

```javascript
fetch('http://localhost:8000/health')
  .then(response => response.json())
  .then(data => console.log(data));
```

## Use Cases

- **Monitoring**: Check if the API is running
- **Load Balancers**: Health check for load balancer configuration
- **Development**: Quick verification during development
- **CI/CD**: Automated health checks in deployment pipelines

## Notes

- This endpoint does not require authentication
- Response time is typically < 100ms
- Always returns `200 OK` when the server is running
