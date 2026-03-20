# Troubleshooting & Debugging

Comprehensive guide to diagnosing and resolving issues with AstroDASH 2.0 API.

## Common Failure Scenarios

### 1. File Upload Failures

#### Invalid File Format

**Symptoms:**

- HTTP 400 error with "Unsupported file format" message
- File rejected immediately upon upload

**Common Causes:**

- File has wrong extension
- File content doesn't match expected format
- Corrupted or empty files

**Solutions:**

```bash
# Check file format
file spectrum.fits
# Should show: spectrum.fits: FITS image data

# Verify file content
head -20 spectrum.fits
# Should start with: SIMPLE  =                    T

# Check file size
ls -lh spectrum.fits
# Should be > 0 bytes
```

#### File Size Exceeded

**Symptoms:**

- HTTP 413 Payload Too Large
- Upload fails for large files

**Solutions:**

```bash
# Check file size
du -h spectrum.fits

# If > 50MB, consider:
# 1. Compress the file
# 2. Split into smaller chunks
# 3. Use batch processing with ZIP
```

#### Corrupted Files

**Symptoms:**

- HTTP 400 with "No valid spectrum data found"
- Processing fails during file reading

**Diagnosis:**

```python
import numpy as np
import astropy.io.fits as fits

try:
    # Try to read FITS file
    hdul = fits.open('spectrum.fits')
    data = hdul[1].data  # Assuming data is in extension 1
    print(f"Data shape: {data.shape}")
    print(f"Data type: {data.dtype}")
    print(f"First few values: {data[:5]}")
except Exception as e:
    print(f"File corruption detected: {e}")
```

### 2. Processing Failures

#### Classification Errors

**Symptoms:**

- HTTP 500 with "Classification error" message
- Model loading failures

**Common Causes:**

- Missing model files
- GPU memory issues
- Corrupted model weights

**Diagnosis:**

```bash
# Check model files exist
ls -la /path/to/models/

# Check GPU memory (if using CUDA)
nvidia-smi

# Check model file integrity
md5sum model.pth
```

#### Memory Issues

**Symptoms:**

- HTTP 500 with "Out of memory" or similar
- Process killed by system

**Solutions:**

```bash
# Check available memory
free -h

# Check process memory usage
ps aux | grep astrodash

# Reduce batch size or file chunk size
```

### 3. Network and Connectivity Issues

#### Connection Timeouts

**Symptoms:**

- Request hangs indefinitely
- HTTP 408 Request Timeout

**Diagnosis:**

```bash
# Test basic connectivity
ping localhost

# Test port accessibility
telnet localhost 8000

# Check firewall rules
sudo ufw status
```

#### Rate Limiting

**Symptoms:**

- HTTP 429 Too Many Requests
- Response includes "Rate limit exceeded"

**Solutions:**

```python
import time
import requests

def handle_rate_limit(response):
    if response.status_code == 429:
        retry_after = int(response.headers.get('Retry-After', 60))
        print(f"Rate limited. Waiting {retry_after} seconds...")
        time.sleep(retry_after)
        return True
    return False

# Implement exponential backoff
def make_request_with_backoff(url, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = requests.get(url)
            if handle_rate_limit(response):
                continue
            return response
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)
```

## Log Analysis

### 1. Application Logs

#### Understanding Log Format

The API uses a single consolidated log file (`app.log`) with structured JSON formatting:

```bash
# Typical log entry (JSON format)
{"timestamp": "2023-12-10 14:30:45", "level": "INFO", "logger": "app.api.v1.spectrum", "message": "Requested analysis options", "module": "spectrum", "function": "get_analysis_options", "line": 45}

# Components:
# timestamp: When the event occurred
# level: Log level (DEBUG, INFO, WARN, ERROR)
# logger: Logger name (usually module path)
# message: Human-readable message
# module: Python module name
# function: Function name where log was created
# line: Line number in source code
```

#### Useful Log Queries

```bash
# Find all errors
grep '"level": "ERROR"' logs/app.log

# Find specific error types
grep "ClassificationException\|SpectrumProcessingException\|ValidationException" logs/app.log

# Find errors in last hour
grep "$(date '+%Y-%m-%d %H')" logs/app.log | grep '"level": "ERROR"'

# Count error types
grep '"level": "ERROR"' logs/app.log | jq -r '.message' | sort | uniq -c

# Find specific endpoint usage
grep "Requested analysis options" logs/app.log

# Find requests from specific user/context
grep "user_id.*123" logs/app.log

# Show only INFO and above
grep -E '("level": "INFO"|"level": "WARN"|"level": "ERROR")' logs/app.log

# Show only errors with context
grep -A5 -B5 '"level": "ERROR"' logs/app.log
```

### 2. Log File Management

#### Log Rotation

The API automatically rotates logs:

- **Max file size**: 10MB per log file
- **Backup count**: 5 rotated files
- **Location**: Configured via `LOG_DIR` environment variable (default: `logs/`)
- **Format**: JSON for structured parsing

```bash
# Check log directory
ls -la logs/  # Default log directory

# Check current log file size
du -h logs/app.log

# View rotated logs
ls -la logs/app.log.*

# Monitor log file in real-time
tail -f logs/app.log

# Search across all log files
grep "ERROR" logs/app.log*
```

## Performance Tuning

### 1. Response Time Analysis

#### Identifying Slow Endpoints

```bash
# Extract response times from logs (if timing is logged)
grep "processing_time" /var/log/astrodash/app.log

# Calculate average response time per endpoint
grep "processing_time" /var/log/astrodash/app.log | jq -r '.message' | awk '{sum[$1]+=$2; count[$1]++} END {for (i in sum) print i, sum[i]/count[i]}'
```

#### Performance Bottlenecks

**Common Issues:**

- Large file processing
- Model loading delays
- Database queries
- External API calls

**Solutions:**

```python
# Implement caching for expensive operations
import functools
import time

@functools.lru_cache(maxsize=128)
def load_model(model_name):
    # Model loading logic
    pass

# Use async processing for I/O operations
import asyncio
import aiohttp

async def process_spectrum_async(file_path):
    async with aiohttp.ClientSession() as session:
        # Async processing logic
        pass
```

### 2. Resource Optimization

#### Memory Management

```bash
# Monitor memory usage
watch -n 1 'free -h && echo "---" && ps aux | grep astrodash | head -5'

# Check for memory leaks
valgrind --tool=memcheck --leak-check=full python app.py
```

#### CPU Optimization

```bash
# Monitor CPU usage
top -p $(pgrep -f astrodash)

# Profile Python code
python -m cProfile -o profile.stats app.py
python -c "import pstats; p = pstats.Stats('profile.stats'); p.sort_stats('cumulative').print_stats(20)"
```

## Debugging Tools

### 1. API Testing Tools

#### cURL Debugging

```bash
# Verbose output
curl -v -X POST "http://localhost:8000/api/v1/process" \
  -F "file=@spectrum.fits" \
  -F 'params={"smoothing": 6}'

# Show response headers
curl -i -X GET "http://localhost:8000/health"

# Test with different content types
curl -H "Content-Type: application/json" \
  -d '{"test": "data"}' \
  "http://localhost:8000/health"
```

#### Postman/Insomnia

- Use environment variables for base URLs
- Set up request templates
- Use collections for organized testing
- Enable request/response logging

### 2. Python Debugging

#### Interactive Debugging

```python
import pdb
import requests

def debug_request():
    url = "http://localhost:8000/api/v1/process"

    # Set breakpoint
    pdb.set_trace()

    response = requests.post(url, files={'file': open('spectrum.fits', 'rb')})
    return response.json()

# Run with: python -m pdb script.py
```

#### Logging Configuration

```python
import logging

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('debug.log'),
        logging.StreamHandler()
    ]
)

# Enable specific module logging
logging.getLogger('astrodash').setLevel(logging.DEBUG)
logging.getLogger('urllib3').setLevel(logging.DEBUG)
```

### 3. System Monitoring

#### Real-time Monitoring

```bash
# Monitor system resources
htop

# Monitor network connections
netstat -tulpn | grep :8000

# Monitor disk I/O
iotop

# Monitor file changes
inotifywait -m /var/log/astrodash/
```

## Troubleshooting Checklist

### 1. Quick Diagnosis

- [ ] Check API health endpoint: `GET /health`
- [ ] Verify service is running: `systemctl status astrodash` (if using systemd)
- [ ] Check port accessibility: `telnet localhost 8000`
- [ ] Review recent error logs: `tail -100 logs/app.log | grep '"level": "ERROR"'`
- [ ] Test with minimal request: `curl -X GET "http://localhost:8000/health"`

### 2. File Processing Issues

- [ ] Verify file format and extension
- [ ] Check file size limits (50MB max)
- [ ] Validate file content integrity
- [ ] Test with known good file
- [ ] Check file permissions

### 3. Performance Issues

- [ ] Monitor response times
- [ ] Check resource usage (CPU, memory, disk)
- [ ] Review slow query logs
- [ ] Analyze request patterns
- [ ] Check for rate limiting

### 4. Network Issues

- [ ] Test local connectivity
- [ ] Check firewall rules
- [ ] Verify DNS resolution
- [ ] Test with different clients
- [ ] Check load balancer configuration

### 5. Environment Issues

- [ ] Verify environment variables
- [ ] Check dependency versions
- [ ] Review configuration files
- [ ] Test in isolated environment
- [ ] Compare with working setup

## Getting Help

### 1. Information to Collect

When reporting issues, include:

- **Error messages** and stack traces
- **Request details** (endpoint, parameters, file info)
- **Environment details** (OS, Python version, dependencies)
- **Log files** (relevant sections from app.log)
- **Steps to reproduce** the issue
- **Expected vs actual behavior**

### 2. Debugging Commands

```bash
# Collect system information
uname -a
python --version
pip list
systemctl status astrodash

# Collect logs
tail -1000 logs/app.log > app_log.txt

# Test connectivity
curl -v "http://localhost:8000/health" > health_test.txt
```

### 3. Support Channels

- **GitHub Issues**: For bug reports and feature requests
- **Documentation**: Check this troubleshooting guide first

## Prevention Best Practices

### 1. Monitoring Setup

- Implement health checks
- Set up alerting for errors
- Monitor performance metrics
- Track resource usage

### 2. Testing Strategy

- Unit tests for critical functions
- Integration tests for API endpoints
- Load testing for performance validation
- Regular regression testing

### 3. Documentation

- Keep runbooks updated
- Document common issues and solutions
- Maintain troubleshooting guides
- Share knowledge with team members
