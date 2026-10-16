---
title: Retire the Transformer Model - Plan
type: feat
date: 2026-10-16
topic: retire-transformer-model
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# Retire the Transformer Model - Plan

## Goal Capsule

**Objective:** Visitors and API clients can no longer choose the older Transformer model for new classifications. It is gone from the model-selection cards, the Classification form's Model dropdown and the leaderboard. DASH is the model AstroDash presents by default.

**Means:** Flip the Transformer's registry status to retired and move the default flag to DASH (KTD1). Every surface already derives from the registry.

**Authority:** The Product Contract requirements (R-IDs) win on behavior. KTDs win on mechanism. Units override neither.

**Stop conditions:** Stop and report if the registry fails `validate_registry` at import after the flip. Also stop if any production code path outside the API and the session-access guard turns out to reject retired models while rendering a stored result.

**Execution profile:** Small production change (two definition files plus one comment), larger test and documentation churn. One branch, one PR to `scimma/astrodash` from the `bot` remote. The maintainer merges.

---

## Product Contract

### Summary

The Transformer is marked retired in the model registry and DASH becomes the default. The cards, the Model dropdown and the leaderboard all read the registry's listed models, so the Transformer disappears from all three. The REST API refuses `modelType=transformer`. The Transformer's code and weights stay so past results still resolve their label.

### Problem Frame

The Transformer is an older model the project no longer wants to offer, yet it is the default and the first card on `/astrodash/select-model/?action=classify`. New visitors are steered toward it and it keeps a place in the monthly leaderboard rankings.

### Key Decisions

- **Retire rather than only unlist.** (session-settled: user-directed -- chosen over setting `listed=False`: unlisting would keep the model runnable by anyone naming it, including API clients, and the user wants it gone for new use.) Governs R4.
- **DASH becomes the default model.** (session-settled: user-approved -- proposed because DASH already carries the RECOMMENDED badge; the user confirmed.) Governs R5.
- **Past leaderboard months also drop the Transformer row.** The user accepted this when it was raised in the brainstorm. Score files on disk are not edited. Governs R3.

### Requirements

**Selection surfaces**

- R1. The model-selection page shows no Transformer card for any action (classify or batch).
- R2. The Classification form's and the model-selection form's Model choices do not include the Transformer.

**Leaderboard**

- R3. The leaderboard shows no Transformer row for any month, and the monthly evaluation no longer scores it.

**API**

- R4. The single-spectrum and batch API endpoints answer `modelType=transformer` with HTTP 400 "Model type transformer is not available."

**Default and continuity**

- R5. DASH is the registry's single active default.
- R6. A stored result produced by the Transformer still renders with its Transformer label.
- R7. A visitor whose session still holds the Transformer as the selected model is sent back to the model picker with the existing "no longer selectable" message instead of classifying with it.

**Documentation**

- R8. User-facing docs no longer present the Transformer as an available model, the default, an example `modelType`, or a template to copy.

### Scope Boundaries

- The Transformer classifier code, its weights on the data mount, its template handler and its `transformer` preprocessing variant stay. R6 depends on them.
- Score files under the leaderboard data directory are not rewritten. Old months keep their Transformer rows on disk. The page just doesn't show them.
- Historical artifacts under `docs/plans/`, `docs/brainstorms/` and `docs/solutions/` stay as written.
- Considered and not built: a guard that refuses to re-run `evaluate` on an already-finalized month. Re-running would rewrite that month's score file without the Transformer row, but re-running a finalized month is not part of the normal workflow. The Risks entry below records this. A request to keep historical leaderboards reproducible would change the call.

#### Deferred to Follow-Up Work

- Removing the Transformer's code and weights entirely. Wait at least one session lifetime after the PROD deploy (`SESSION_COOKIE_AGE` is unset, so Django's two-week default applies), so no live session still holds `transformer` (KTD2).
- Fixing the `run/astrodash.test.sh` wrapper, which is broken independently of this work.

---

## Planning Contract

### Key Technical Decisions

- KTD1. **Two definition flips are the whole production mechanism.** In `definitions/transformer.py`, set `status=STATUS_RETIRED` and `is_default=False`. In `definitions/dash.py`, set `is_default=True`. `listed_definitions()` already filters to active and listed models. It feeds the cards (`model_selection.html`), both forms (`forms.py`), `leaderboard/page.py` and `leaderboard/evaluate.py`. `views.py` already returns the R4 refusal for an inactive definition, and `core/model_access.py` already performs R7's redirect. No new code path is needed. Implements R1 to R5 and R7.
- KTD2. **Keep `TRANSFORMER` in `MODELS`.** `get_definition()` resolves retired models only because they stay in the tuple. It is what keeps R6 working, through `surfaces.py`, `ui_views.py` result rendering, and the preprocessing and redshift services. It also keeps R7 working: `revalidate_session_model` in `core/model_access.py` treats a selection that `get_definition()` cannot resolve as a user-uploaded model and lets it through, so a missing definition would skip the redirect and fail later in `ModelFactory`. Update the ordering comment in `model_registry/__init__.py`, which says "Transformer first, then DASH". Keep the Transformer's position in the tuple. Moving it would change nothing visible and churn the ordering tests twice.
- KTD3. **Test rosters that relied on the Transformer as the stand-in default move the default explicitly.** Several patched `MODELS` rosters in tests gate, retire or unlist DASH and rely on the Transformer staying the active default. After KTD1 those rosters have no active default, or they gate the default. Both states are invalid, and patching `MODELS` skips `validate_registry`, so the tests could still pass while exercising impossible states. Each such roster must make a remaining active, listed, ungated model the default. `gate_fixtures.gated_roster()` defaults to gating `dash`, so either change its default target or have it promote another model when it gates the default.
- KTD4. **`1dCNN_z` replaces the Transformer as the test suite's stand-in model.** The tests use the Transformer in two roles. It is the "redshift required" model, and it is the "classification only" model: no twins surface, no template overlays, no RLap. `1dCNN_z` fills both roles, since it declares `REDSHIFT_INPUT_REQUIRED`, `surfaces=(SURFACE_CLASSIFICATION,)` and all three capability flags False. It is the earlier of the two redshift-required listed models in registry order, so it is the stable choice.

### Assumptions

- `ui_views.py:1078` substitutes `'transformer'` into `params['modelType']` for user-uploaded models. On the single-spectrum path nothing reads that value as a gate, so it is left alone. The batch path at `ui_views.py:1535` already uses `'dash'`.
- No database table stores past classifications by model id. R6 concerns results rendered from the current session or request, and they resolve through `get_definition()`.

### Risks

| Risk | Mitigation |
|---|---|
| Re-running `leaderboard/evaluate.py` for a finalized past month rewrites that month's committed score JSON (`app/astrodash/data/leaderboard/`) without the Transformer row. The change shows up as a git diff and can be reverted. | U4 adds a one-line warning to the "Relationship to the leaderboard" section of `app/wiserep_scrape/README.md`. No code guard (see Scope Boundaries). |
| External API clients that hard-coded `modelType=transformer`, possibly copied from `docs/api/endpoints/batch-process.md`, start getting 400s. | Accepted under the retire decision. U4 replaces the doc example. Note the change in the PR description so the maintainer can announce it. |
| Past-month ranks below the Transformer renumber; it ranked 4th in May and August 2026. | Accepted under the past-months decision. No action. |

### Sequencing

U1 first, because it is the production change and it breaks the tests that U2 and U3 repair. U2 and U3 are independent of each other. U4 (docs) is independent and can go anywhere.

---

## Implementation Units

### U1. Retire the Transformer and promote DASH in the registry

- **Goal:** The registry reports DASH as the sole active default and the Transformer as retired but still resolvable.
- **Requirements:** R1 to R7, via KTD1 and KTD2.
- **Dependencies:** none.
- **Files:**
  - `app/astrodash/infrastructure/ml/model_registry/definitions/transformer.py`
  - `app/astrodash/infrastructure/ml/model_registry/definitions/dash.py`
  - `app/astrodash/infrastructure/ml/model_registry/__init__.py` (ordering comment and module docstring wording only)
  - `app/astrodash/tests/test_model_registry.py`
- **Approach:**
  1. Flip the two definitions per KTD1.
  2. Update the `MODELS` comment and the package docstring so they no longer describe the Transformer as active or first-listed.
  3. Rewrite the registry tests that pin the old state. `test_default_is_transformer` becomes a DASH-default test, and the active/listed ordering tests drop `transformer`. `test_transformer_capability_fields` asserts retired, not default. The existing retire-and-promote test (around `test_model_registry.py:114`) becomes a test of the real roster rather than a patched one.
- **Patterns to follow:** the existing retire test in `test_model_registry.py` already exercises this exact state through a patch.
- **Test scenarios:**
  - `default_definition().id` is `"dash"`.
  - `active_definitions()` and `listed_definitions()` ids are `dash, 1dCNN_z, 1dCNN_noz, latent_z, latent_noz` in that order.
  - `get_definition("transformer")` returns a definition with `is_active` False, title "Transformer Model", and classifier `TransformerClassifier`.
  - `validate_registry(MODELS)` passes on the real roster.
- **Verification:** the registry imports cleanly and `test_model_registry` passes against the unpatched roster.

### U2. Repair surface, API and access tests around the retired model

- **Goal:** Tests for the cards, forms, API and session access reflect the retired Transformer and pin R1, R2, R4 and R7 against the real roster.
- **Requirements:** R1, R2, R4, R5, R7. KTD3, KTD4.
- **Dependencies:** U1.
- **Files:**
  - `app/astrodash/tests/test_model_factory.py`
  - `app/astrodash/tests/test_classifier_parity.py`
  - `app/astrodash/tests/test_api_model_type.py`
  - `app/astrodash/tests/test_model_access.py`
  - `app/astrodash/tests/test_classify_by_name.py`
  - `app/astrodash/tests/gate_fixtures.py`
- **Approach:**
  1. Form-choice tests drop the `("transformer", "Transformer Model")` choice and expect `initial == "dash"`.
  2. Card-visibility tests in `test_classifier_parity.py` assert the Transformer card is absent. Tests that used the Transformer in either role KTD4 describes switch to `1dCNN_z`. That covers the redshift-required cases, the classification-only render and twins-refusal cases (for example `test_transformer_renders_only_the_classification_tab`, `test_transformer_no_twins_embedding_and_no_templates`, and the `_render_visible(..., "transformer")` case), and the batch cases that post `transformer`. Keep only the cases that deliberately exercise a retired definition's resolution, such as `prepare_for_model(..., "transformer")`.
  3. In `test_api_model_type.py`, fix the roster helpers that described the Transformer as "the listed, ungated active default" so each roster names another default (KTD3). Replace the `test_valid_transformer_classifies_as_today` case with a real-roster refusal test. Keep a positive active-model test using `1dCNN_z` or `dash`.
  4. In `gate_fixtures.gated_roster()`, apply KTD3. Callers that gate DASH because they need its twins surface must also promote another model to default.
  5. In `test_classify_by_name.py`, change the seven `select_model("transformer")` calls to `1dCNN_z` (KTD4).
  6. In `test_model_access.py`, review the cases that select or mint `transformer` (around lines 173, 501, 609, 894). Where a case meant "an ordinary listed model", switch it to a listed model. Keep or add one case where a stale session holding `transformer` is redirected.
- **Patterns to follow:** existing `patch.object(model_registry, "MODELS", ...)` roster helpers. `MODEL_NO_LONGER_SELECTABLE_MESSAGE` assertions already in `test_model_access.py`.
- **Test scenarios:**
  - GET `/astrodash/select-model/?action=classify`: the page has no `selectModel('transformer')` card and has a DASH card.
  - `ClassifyForm().fields["model"].choices` excludes `transformer`, and its initial value is `dash`.
  - `ModelSelectionForm` choices exclude `transformer`.
  - POST model selection with `model_type=transformer` is rejected as an invalid choice, and the session's selected model is unchanged.
  - Process-spectrum API with `modelType=transformer` on the real roster returns 400 and the body "Model type transformer is not available." The classification service is not called.
  - Batch-process API with `modelType=transformer` returns the same 400, and the batch service is not called.
  - A session holding `selected_model_type="transformer"` that GETs the classify page is redirected to the model picker. The no-longer-selectable message is shown and the selection is cleared.
  - The same stale session POSTing a classify form gets the same redirect, and nothing is classified.
  - A required-redshift upload without redshift is still refused for `1dCNN_z`, standing in for the old Transformer case.
- **Verification:** all six files pass. No test still treats the Transformer as active or default. A grep of `select_model("transformer")` and `get_definition("transformer")` in tests finds only deliberate retired-model cases.

### U3. Repair leaderboard tests and pin the Transformer's absence

- **Goal:** Leaderboard tests prove the Transformer is excluded from rankings for current and past months, and the tie-break test no longer depends on it.
- **Requirements:** R3.
- **Dependencies:** U1.
- **Files:** `app/astrodash/tests/test_leaderboard.py`
- **Approach:**
  1. `test_ranks_by_accuracy_then_roc` ties `dash` with `transformer` at 80.0 accuracy. Rebuild the tie between two listed models so the accuracy-then-ROC ordering stays tested.
  2. The `ScoreDefinitionTests` case that builds a `SimpleNamespace(id="transformer", ...)` is independent of the registry. Rename its id to a neutral or required-redshift listed id for clarity. No behavior change.
- **Patterns to follow:** existing `patch(... page.load_scores ...)` and `patch(... page.available_months ...)` fixtures in the same file.
- **Test scenarios:**
  - A month whose score payload contains a `transformer` row renders rankings without that id. The remaining models keep contiguous ranks starting at 1.
  - The accuracy tie between two listed models is broken by the higher ROC.
  - `evaluate_month` with a stubbed `ModelFactory` never asks for a `transformer` classifier, and its payload's `models` ids exclude `transformer`.
- **Verification:** `test_leaderboard` passes and covers both the page and the evaluation side of R3.

### U4. Update user-facing documentation

- **Goal:** No user-facing doc presents the Transformer as available, default, an example `modelType`, or a template.
- **Requirements:** R8.
- **Dependencies:** none (U1 must land in the same PR so docs and behavior agree).
- **Files:**
  - `README.md`
  - `STRATEGY.md`
  - `docs/api/introduction.md`
  - `docs/api/integration-examples.md`
  - `docs/api/architecture.md`
  - `docs/api/data-formats.md`
  - `docs/api/openapi.json` (the two `modelType` descriptions listing active ids)
  - `docs/api/endpoints/batch-process.md` (the `modelType: transformer` example)
  - `docs/api/endpoints/process-spectrum.md`
  - `docs/admin/updating-data-files.md`
  - `docs/guides/contributing-classifiers.md` (stop recommending `transformer.py` as a template; point at `dash.py` or a current redshift-required definition)
  - `app/wiserep_scrape/README.md` (the "Relationship to the leaderboard" section, for the re-run warning in step 3)
- **Approach:**
  1. Remove the Transformer from lists of available models and from examples, substituting DASH or `1dCNN_z` where an example needs redshift.
  2. Where a doc explains model history or the registry's retire mechanism, say the Transformer is retired rather than deleting the mention. Keep wording that describes the retired-model behavior as a mechanism.
  3. In the "Relationship to the leaderboard" section of `app/wiserep_scrape/README.md`, add one line warning that re-running evaluation for a finalized month rewrites its committed score file with only the currently listed models.
  4. Keep `openapi.json` valid JSON, and edit only the description strings.
- **Test expectation:** none -- documentation only. The existing documentation guard tests (skipped inside the container) are unaffected.
- **Verification:** grep across the listed docs finds "transformer" only where it describes the retirement or the retire mechanism. `openapi.json` still parses.

---

## Verification Contract

| Gate | Command / check | Proves |
|---|---|---|
| Full suite | With the stack up (`run/astrodashctl slim_dev up`): `docker compose --file docker/docker-compose.yml --file docker/docker-compose.dev.yaml --env-file env/.env.default --env-file env/.env.dev --profile slim_dev exec -T app bash -c 'python manage.py test astrodash.tests users.tests'` | U1 to U3. Do not use `run/astrodash.test.sh`, which is broken. |
| Known baseline | One pre-existing failure, `test_model_access.EntryLinkTokenTests.test_committed_signing_key_refuses_to_mint`, plus three documentation-guard skips | Distinguishes regressions from the existing baseline |
| Formatting | `black` on every changed Python file | Project convention |
| Manual smoke | `http://localhost:4000/astrodash/select-model/?action=classify` shows no Transformer card and DASH is the first card. The classify page's Model dropdown lacks the Transformer, and `/astrodash/leaderboard/` shows no Transformer row. R5 has no visible surface, so U1 and U2 prove it with unit tests. | R1, R2, R3 end to end |
| JSON validity | `docs/api/openapi.json` parses | U4 |

---

## Definition of Done

- All of R1 to R8 hold, with the test scenarios in U1 to U3 present and passing.
- The full suite passes except for the known baseline failure.
- Changed Python files are `black`-clean. Files the work did not need to touch are not reformatted.
- No abandoned-attempt code or commented-out registry entries remain in the diff.
- The PR description notes the API behavior change for `modelType=transformer` (R4) so the maintainer can tell API users.
