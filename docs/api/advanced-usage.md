# Advanced Usage Patterns

Learn advanced techniques for integrating with the AstroDASH API effectively.

## Batch Processing Strategies

**Note**: The AstroDash API supports two batch processing modes:

- **ZIP file processing**: Upload a ZIP archive containing multiple spectrum files
- **Individual file processing**: Upload multiple individual files simultaneously

**Supported file formats**: `.fits`, `.dat`, `.txt`, `.lnw`

**File size limits**: 50MB per file, 100MB total per request

### Efficient Batch Processing

#### Optimal Batch Sizes

```python
# For large datasets, process in chunks
def process_large_dataset(file_list, chunk_size=50):
    results = {}

    for i in range(0, len(file_list), chunk_size):
        chunk = file_list[i:i + chunk_size]

        # Create ZIP file for chunk
        with zipfile.ZipFile(f'chunk_{i//chunk_size}.zip', 'w') as zipf:
            for file_path in chunk:
                zipf.write(file_path, os.path.basename(file_path))

        # Process chunk
        with open(f'chunk_{i//chunk_size}.zip', 'rb') as zip_file:
            response = requests.post(
                'http://localhost:8000/astrodash/api/v1/batch-process',
                files={'zip_file': zip_file},
                data={'params': '{}', 'model_id': None}
            )
            chunk_results = response.json()
            results.update(chunk_results)

        # Clean up
        os.remove(f'chunk_{i//chunk_size}.zip')

    return results
```

#### Parallel Processing

```python
import concurrent.futures
import threading

class BatchProcessor:
    def __init__(self, max_workers=3):
        self.max_workers = max_workers
        self.session = requests.Session()
        self.lock = threading.Lock()

    def process_chunk(self, chunk_files):
        # Create temporary ZIP
        zip_path = f'temp_{threading.get_ident()}.zip'
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for file_path in chunk_files:
                zipf.write(file_path, os.path.basename(file_path))

        try:
            with open(zip_path, 'rb') as zip_file:
                response = self.session.post(
                    'http://localhost:8000/astrodash/api/v1/batch-process',
                    files={'zip_file': zip_file},
                    data={'params': '{}', 'model_id': None}
                )
                return response.json()
        finally:
            os.remove(zip_path)

    def process_all(self, all_files, chunk_size=20):
        chunks = [all_files[i:i + chunk_size]
                 for i in range(0, len(all_files), chunk_size)]

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(self.process_chunk, chunk) for chunk in chunks]
            results = {}

            for future in concurrent.futures.as_completed(futures):
                chunk_result = future.result()
                results.update(chunk_result)

        return results
```

### Progress Tracking

```python
import time
from tqdm import tqdm

def process_with_progress(file_list):
    results = {}
    total_files = len(file_list)

    with tqdm(total=total_files, desc="Processing spectra") as pbar:
        for i in range(0, total_files, 10):
            chunk = file_list[i:i + 10]

            # Process chunk
            chunk_results = process_chunk(chunk)
            results.update(chunk_results)

            # Update progress
            processed = min(i + 10, total_files)
            pbar.update(processed - i)

            # Rate limiting consideration
            # API allows 600 requests/minute with 100 burst limit
            time.sleep(0.1)

    return results
```

## Error Handling Strategies

**Note**: The AstroDash API provides structured error responses:

- **Validation Errors**: 422 status with detailed field-level error information
- **File Errors**: 400 status for unsupported file types or sizes
- **Processing Errors**: 500 status for ML model or processing failures
- **Rate Limiting**: 429 status with `Retry-After` header

### Retry with Exponential Backoff

```python
import time
import random
from requests.exceptions import RequestException

def retry_with_backoff(func, max_retries=3, base_delay=1):
    """Retry function with exponential backoff and jitter."""

    for attempt in range(max_retries + 1):
        try:
            return func()
        except RequestException as e:
            if attempt == max_retries:
                raise e

            # Calculate delay with jitter
            delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
            print(f"Attempt {attempt + 1} failed. Retrying in {delay:.2f}s...")
            time.sleep(delay)

    raise Exception("Max retries exceeded")

# Usage
def process_spectrum_with_retry(file_path):
    def _process():
        with open(file_path, 'rb') as f:
            response = requests.post(
                'http://localhost:8000/astrodash/api/v1/process',
                files={'file': f}
            )
            response.raise_for_status()
            return response.json()

    return retry_with_backoff(_process)
```

### Circuit Breaker Pattern

```python
import time
from enum import Enum

class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered

class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED

    def call(self, func):
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
            else:
                raise Exception("Circuit breaker is OPEN")

        try:
            result = func()
            self.on_success()
            return result
        except Exception as e:
            self.on_failure()
            raise e

    def on_success(self):
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def on_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN

# Usage
breaker = CircuitBreaker()

def safe_process_spectrum(file_path):
    def _process():
        with open(file_path, 'rb') as f:
            response = requests.post(
                'http://localhost:8000/astrodash/api/v1/process',
                files={'file': f}
            )
            response.raise_for_status()
            return response.json()

    return breaker.call(_process)
```

### Graceful Degradation

```python
def process_spectrum_with_fallback(file_path, primary_endpoint=None):
    """Process spectrum with fallback to alternative methods."""

    endpoints = [
        primary_endpoint or 'http://localhost:8000/astrodash/api/v1/process',
        'http://localhost:8000/astrodash/api/v1/process'  # Fallback
    ]

    for endpoint in endpoints:
        try:
            with open(file_path, 'rb') as f:
                response = requests.post(
                    endpoint,
                    files={'file': f},
                    timeout=30
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            print(f"Failed with {endpoint}: {e}")
            continue

    raise Exception("All endpoints failed")
```

## Caching Strategies

**Note**: The AstroDash backend has limited built-in caching:

- **Model Loading**: ML models are cached in memory after first load
- **Line List Data**: Line list data is cached in memory for performance
- **No Response Caching**: Individual API responses are not cached

For production use, consider implementing client-side caching or using a reverse proxy with caching.

### Simple In-Memory Cache

```python
import hashlib
from datetime import datetime, timedelta

class SpectrumCache:
    def __init__(self, max_size=1000, ttl_hours=24):
        self.cache = {}
        self.max_size = max_size
        self.ttl = timedelta(hours=ttl_hours)

    def _generate_key(self, file_path, params):
        """Generate cache key from file and parameters."""
        content = f"{file_path}:{params}"
        return hashlib.md5(content.encode()).hexdigest()

    def get(self, file_path, params):
        key = self._generate_key(file_path, params)

        if key in self.cache:
            entry = self.cache[key]
            if datetime.now() - entry['timestamp'] < self.ttl:
                return entry['result']
            else:
                del self.cache[key]

        return None

    def set(self, file_path, params, result):
        key = self._generate_key(file_path, params)

        # Evict oldest entries if cache is full
        if len(self.cache) >= self.max_size:
            oldest_key = min(self.cache.keys(),
                           key=lambda k: self.cache[k]['timestamp'])
            del self.cache[oldest_key]

        self.cache[key] = {
            'result': result,
            'timestamp': datetime.now()
        }
```

### Persistent Cache with Redis

```python
import redis
import json
import pickle
import hashlib

class RedisSpectrumCache:
    def __init__(self, redis_url="redis://localhost:6379", ttl_hours=24):
        self.redis = redis.from_url(redis_url)
        self.ttl = ttl_hours * 3600  # Convert to seconds

    def _generate_key(self, file_path, params):
        content = f"{file_path}:{params}"
        return f"spectrum:{hashlib.md5(content.encode()).hexdigest()}"

    def get(self, file_path, params):
        key = self._generate_key(file_path, params)
        cached = self.redis.get(key)

        if cached:
            return pickle.loads(cached)
        return None

    def set(self, file_path, params, result):
        key = self._generate_key(file_path, params)
        self.redis.setex(
            key,
            self.ttl,
            pickle.dumps(result)
        )

    def invalidate(self, file_path, params):
        key = self._generate_key(file_path, params)
        self.redis.delete(key)
```

## Performance Optimization

### Connection Pooling

```python
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def create_optimized_session():
    """Create a session with connection pooling and retry logic."""

    session = requests.Session()

    # Connection pooling
    adapter = HTTPAdapter(
        pool_connections=10,
        pool_maxsize=20,
        max_retries=Retry(
            total=3,
            backoff_factor=0.1,
            status_forcelist=[500, 502, 503, 504]
        )
    )

    session.mount('http://', adapter)
    session.mount('https://', adapter)

    return session
```

### Async Processing

```python
import asyncio
import aiohttp
import aiofiles
import os

async def process_spectrum_async(session, file_path):
    """Process a single spectrum asynchronously."""

    async with aiofiles.open(file_path, 'rb') as f:
        file_content = await f.read()

        data = aiohttp.FormData()
        data.add_field('file', file_content, filename=os.path.basename(file_path))

        async with session.post(
            'http://localhost:8000/astrodash/api/v1/process',
            data=data
        ) as response:
            return await response.json()

async def process_batch_async(file_paths, max_concurrent=5):
    """Process multiple spectra concurrently."""

    connector = aiohttp.TCPConnector(limit=max_concurrent)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            process_spectrum_async(session, file_path)
            for file_path in file_paths
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return results
```

**Note**: The AstroDash backend is built with Django. All API endpoints support concurrent processing via gunicorn workers. CPU-intensive operations like ML model inference are handled synchronously within each worker.

## Monitoring and Observability

**Note**: The AstroDash backend provides:

- **Structured Logging**: All API calls are logged with request IDs and timing
- **IP Address Tracking**: Real IP extraction from proxy headers
- **Error Logging**: Detailed error logging with stack traces
- **Performance Metrics**: Request duration and success/failure tracking

### Request Logging

```python
import logging
import time
from functools import wraps

def log_api_calls(logger=None):
    """Decorator to log API call details."""
    if logger is None:
        logger = logging.getLogger(__name__)

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()

            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time

                logger.info(
                    f"API call successful: {func.__name__} "
                    f"took {duration:.2f}s"
                )
                return result

            except Exception as e:
                duration = time.time() - start_time
                logger.error(
                    f"API call failed: {func.__name__} "
                    f"failed after {duration:.2f}s with error: {e}"
                )
                raise

        return wrapper
    return decorator
```

## Best Practices Summary

1. **Batch Processing**: Use ZIP files for large datasets, respect 50MB per file limit
2. **Error Handling**: Implement retry logic with exponential backoff (API provides 429 with Retry-After)
3. **Caching**: Implement client-side caching (backend has limited built-in caching)
4. **Connection Management**: Use connection pooling and session reuse
5. **Async Processing**: Use async HTTP clients for concurrent processing
6. **Monitoring**: Use the built-in structured logging and request tracking
7. **Rate Limiting**: Respect 600 requests/minute limit with 100 burst allowance
8. **Resource Management**: Clean up temporary files and connections
9. **Validation**: Validate file types (`.fits`, `.dat`, `.txt`, `.lnw`, `.csv`) before upload
10. **Graceful Degradation**: Implement fallback strategies for critical operations
