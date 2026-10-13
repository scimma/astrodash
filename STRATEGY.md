---
name: AstroDash
last_updated: 2026-05-14
---

# AstroDash Strategy

## Target problem

Astronomers, astrophysicists, and research scientists need to classify
transients from spectra, but the best ML classifiers — DASH, Transformer,
and emerging community models — require local install, GPU setup, or
coding skills most of those researchers don't have. The classifiers exist;
they're just out of reach of the people who'd benefit from using them.

## Our approach

Prioritize the researcher who wants to drop in a single spectrum and get a
classification over the power user who'd happily install a tool. Zero-install
web access is non-negotiable, and AstroDash takes responsibility for keeping
the model library current so users don't have to track which classifier is
state-of-the-art.

## Who it's for

**Primary:** Research scientists, astronomers, and astrophysicists who need
to classify a transient from a spectrum without ML expertise, without
software expertise, and without installing, deploying, or staying current on
the latest ML models.

## Key metrics

- **Weekly active astronomers** — distinct astronomers using the tool in a
  rolling 7-day window
- **Single-spectrum completion rate** — fraction of upload attempts that
  produce a classification the user accepts
- **By-name completion rate** - fraction of by-name lookup attempts that
  produce a classification the user accepts. Tracked separately from the
  single-spectrum completion rate, whose denominator stays upload attempts, so
  the two series remain comparable and show whether starting from a
  designation converts better than starting from a file
- **Time from upload to result** — median seconds from upload submission to
  a returned classification for a single spectrum
- **Returning astronomers** — fraction of weekly actives who were also active
  in a prior week
- **Citations in published papers** — quarterly count of peer-reviewed papers
  citing AstroDash for a classification

## Tracks

### Core classification experience

The drop-in-a-spectrum web flow and the surfaces built around it — batch
upload, spectral twins explorer, redshift estimation. The surface area the
casual user actually touches.

_Why it serves the approach:_ owns the "no install, no expertise"
commitment. If this flow breaks or gains friction, the approach fails at
its core promise.

### Model library & curation

Adding, retiring, and maintaining the classifiers AstroDash ships with —
DASH CNN, Transformer, and community-contributed TorchScript models —
including evaluation and accuracy testing before integration.

_Why it serves the approach:_ owns the commitment that users don't have
to track which ML model is best. AstroDash absorbs that work on their
behalf.

### Ecosystem integration

The API surface, identity and access management, and integrations with
external transient-research tools (Blast and others) so AstroDash can be
called from existing pipelines and workflows.

_Why it serves the approach:_ extends "zero-install, no-expertise"
outward — users get AstroDash classifications from inside the other tools
they already use, without leaving their workflow to install anything.
