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

## We Need Your Help

This section walks you through how to become an Astrodash developer. We want
your help. No, really. There may be a little voice inside your head that is
telling you that you're not ready to be an open source contributor; that your
skills aren't nearly good enough to contribute. What could you possibly offer
a project like this one?

We assure you — the little voice in your head is wrong. If you can write code
or documentation, you can contribute code to open source. Contributing to open
source projects is a fantastic way to advance one's coding and open source
workflow skills. Writing perfect code isn't the measure of a good developer
(that would disqualify all of us!); it's trying to create something, making
mistakes, and learning from those mistakes. That's how we all improve, and we
are happy to help others learn.

Being an open source contributor doesn't just mean writing code, either. You
can help out by writing documentation, tests, or even giving feedback about
the project (and yes — that includes giving feedback about the contribution
process). Some of these contributions may be the most valuable to the project
as a whole, because you're coming to the project with fresh eyes, so you can
see the errors and assumptions that seasoned contributors have glossed over.

> This text was originally written by Adrienne Lowe for a PyCon talk, and was
> adapted by Astrodash based on its use in the README file for the MetPy
> project and [Astropy](https://www.astropy.org) project.

## Code of Conduct

See [CODE_OF_CONDUCT.md](../../.github/CODE_OF_CONDUCT.md) for the full
Contributor Covenant Code of Conduct.
