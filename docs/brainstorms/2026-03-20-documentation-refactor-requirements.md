---
date: 2026-03-20
topic: documentation-refactor
---

# Documentation Refactor

## Problem Frame

The astrodash documentation was inherited from the Blast project and still carries that project's structure and tooling. The docs use Sphinx with RST files, a Docker-based build pipeline, and a mix of formats (RST and markdown) serving different audiences (developers, admins, API consumers) without clear organization. The content may also be stale or incorrect after the Blast excision.

## Requirements

- R1. Remove Sphinx and its build infrastructure (conf.py, Makefile, make.bat, Dockerfile, docs/requirements.txt, _static/, index.rst, docker-compose.docs.yaml). All documentation will be plain markdown files.
- R2. Convert existing RST documentation to markdown, organized into clear categories under docs/:
  - `docs/api/` — API reference (endpoints, data formats, errors, architecture)
  - `docs/guides/` — user-facing guides (getting started, code examples)
  - `docs/developer/` — developer onboarding, contributing, quickstart for local development
  - `docs/admin/` — operational guides (already exists with updating-data-files.md)
  - `docs/brainstorms/` — development brainstorm artifacts (keep as-is)
  - `docs/plans/` — development plan artifacts (keep as-is)
- R3. Review all converted content for correctness against the current codebase. Fix or remove content that references Blast behavior, stale endpoints, or incorrect instructions.
- R4. Expand README.md to be the primary project entry point: project description, architecture overview, links to documentation sections. Do NOT include quickstart instructions in the README.
- R5. Move quickstart/local development instructions from README.md into docs/developer/.
- R6. Fix the two remaining Blast references in code comments (django_repositories.py:15, ui_views.py:73).
- R7. Remove the unused `astro-sedpy` dependency from docs/requirements.txt (file itself will be deleted as part of R1).
- R8. Remove the acknowledgements RST files (docs/acknowledgements/) or convert to markdown if the content is still relevant.

## Success Criteria

- No RST files remain in the repository
- No Sphinx build infrastructure remains
- All documentation is markdown, organized by audience
- README.md serves as a clear project overview with links to detailed docs
- No stale or incorrect content referencing Blast behavior or endpoints
- Application continues to work unchanged (documentation-only refactor)

## Scope Boundaries

- No application code changes beyond fixing the two Blast comments (R6)
- No architectural changes
- No changes to the GitOps repository
- Brainstorm and plan files are kept as-is (not reviewed for correctness)

## Key Decisions

- **Drop Sphinx entirely:** Plain markdown is sufficient. No doc build pipeline needed.
- **Keep brainstorms/plans:** These are useful project history and stay in docs/.
- **README as entry point:** Expanded README with links, but quickstart lives in docs/developer/.
- **Convert rather than drop API docs:** Content is valuable if corrected for accuracy.

## Outstanding Questions

### Deferred to Planning

- [Affects R3][Needs review] Each RST file needs to be read, checked against the current codebase for correctness, and converted to markdown. The planner should identify which files have stale content vs. which can be converted directly.
- [Affects R8][User decision during planning] Are the acknowledgements files (contributors.rst, data_sources.rst, software.rst) still relevant? Review content during planning and decide whether to convert or remove.
- [Affects R4][Needs review] What should the README architecture overview cover? Review the existing architecture_overview.rst for usable content.

## Next Steps

→ `/ce:plan` for structured implementation planning
