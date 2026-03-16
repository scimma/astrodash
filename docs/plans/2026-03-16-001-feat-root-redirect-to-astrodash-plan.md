---
title: "feat: Redirect Root URL to /astrodash"
type: feat
status: active
date: 2026-03-16
origin: docs/brainstorms/2026-03-16-root-redirect-to-astrodash-brainstorm.md
---

# feat: Redirect Root URL to /astrodash

## Acceptance Criteria

- [ ] Accessing `/` redirects to `/astrodash/`
- [ ] Works in both Docker Compose and Kubernetes environments
- [ ] Other URL paths are not affected

## Context

The astrodash web application is served at `/astrodash/` but users navigating to `/` get a 404. Add a Django `RedirectView` in `astrodash_project/urls.py` to redirect `/` to `/astrodash/`.

## MVP

### app/astrodash_project/urls.py

Add `RedirectView` import and a redirect path entry:

```python
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls, name='admin'),
    path("astrodash/api/v1/", include("astrodash.api_urls")),
    path("astrodash/", include("astrodash.urls")),
    path("", include("users.urls")),
    path("oidc/", include("mozilla_django_oidc.urls")),
    path("", RedirectView.as_view(url="/astrodash/", permanent=False)),
]
```

Note: The empty string `""` path for `users.urls` may conflict. Check if `users.urls` has a root pattern — if so, the RedirectView must be placed carefully or the users include must be prefixed.

## Sources

- **Origin brainstorm:** [docs/brainstorms/2026-03-16-root-redirect-to-astrodash-brainstorm.md](docs/brainstorms/2026-03-16-root-redirect-to-astrodash-brainstorm.md)
- **URL config:** `app/astrodash_project/urls.py`
