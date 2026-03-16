# Brainstorm: Redirect Root URL to /astrodash

**Date:** 2026-03-16
**Status:** Ready for planning

## What We're Building

Add a redirect so that accessing the root URL `/` automatically redirects to `/astrodash/`, where the web application is served. This should work in both Docker Compose (nginx) and Kubernetes (Traefik) environments.

## Why This Approach

**Chosen: Django URL redirect via `RedirectView` in `urls.py`**

Implementing the redirect in Django makes it work in every deployment environment since Django is the common layer. A single `RedirectView` in the URL config is the simplest and most maintainable solution.

**Rejected alternatives:**
- **Proxy-level redirects (nginx + Traefik):** Two separate configurations to maintain and keep in sync across environments.
- **Django middleware:** Over-engineered for a single URL redirect; middleware runs on every request.

## Key Decisions

- **Implementation layer:** Django URL configuration (not proxy)
- **Redirect type:** Permanent (301) or temporary (302) — to be decided during planning
- **Scope:** Only `/` redirects to `/astrodash/`; no other paths affected

## Resolved Questions

- **Environment coverage:** Must work in both Docker Compose and Kubernetes — hence Django-level implementation.
