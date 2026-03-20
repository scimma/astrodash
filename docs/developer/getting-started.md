# Developer Getting Started

## Quick Start

```bash
# Start the full development environment
run/astrodashctl full_dev up

# View logs
run/astrodashctl full_dev logs

# Stop the environment
run/astrodashctl full_dev down
```

The application will be available at `http://localhost:4000/astrodash/`.

### Running Tests

```bash
run/astrodash.test.sh slim_dev
```

### Development Profiles

| Profile | Command | Description |
|---------|---------|-------------|
| `full_dev` | `run/astrodashctl full_dev up` | All services including Celery workers |
| `slim_dev` | `run/astrodashctl slim_dev up` | Web app + database only (no Celery) |
