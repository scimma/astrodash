# Contributing a New ML Classifier

## Overview

Thanks for your interest in contributing! This page explains how to add a new ML classifier to the codebase.

If you run into anything unclear, please open an issue or a draft PR so we can help refine this guide.

## Contribute a New ML Classifier Model

This section explains how to add a first-class model kind (e.g., like `dash` or `1dCNN_z`). The goal is to make your model selectable in the UI and callable via the API, with consistent inputs/outputs. Adding a model with this method provides more flexibility for customizing preprocessing, orchestrating inference, and adding more functionality (like templates) than uploading a Torchscripted model via the endpoint.

### Assumptions

- You have a trained model checkpoint compatible with PyTorch.
- You know the model's expected inputs (e.g., length-N flux array vs wavelength+flux(+redshift)) and output label space.
- You can define preprocessing to reproduce your training normalization/resampling.

### Backend Changes (Django)

#### 1. Register the model in the model registry

- Directory: `app/astrodash/infrastructure/ml/model_registry/`
- Create `definitions/<your_model>.py` defining a `ModelDefinition`. Copy `definitions/dash.py` or, for a model that takes redshift as a required input, `definitions/oned_cnn_z.py` as a template. A definition declares four separable things:

  1. **Identity and presentation.** `id` (which is the `model_type`), the UI card fields (title, description, color, feature tags, icon, recommended), and `preprocessing`.
  2. **Public surface.** `status` (`active` or `retired`), `listed`, `is_default`, and `requires_credential`. These are independent questions — see "Public surface" below.
  3. **Inputs.** `redshift_input`, one of `REDSHIFT_INPUT_REQUIRED`, `REDSHIFT_INPUT_OPTIONAL`, or `REDSHIFT_INPUT_NONE`. This says whether redshift is an *input*; whether your model *estimates* one is the separate `supports_redshift_estimation` flag.
  4. **Results.** `surfaces`, the ordered list of result surfaces your model offers (see "Result surfaces" below), plus the content flags that gate sections *within* a surface: `supports_redshift_estimation`, `supports_template_overlays`, `supports_rlap`.

  Plus a reference to your classifier class (see step 2).

- Add your definition to the ordered `MODELS` roster in `model_registry/__init__.py`.
- You do **not** edit `model_factory.py`. `ModelFactory.get_classifier` resolves the definition through the registry (`get_definition(model_type).classifier(config)`), so registering the definition is enough for the factory to build your classifier. The forms, the model-selection cards, the result tabs, and the behavioral gates likewise read from the definition. The registry's import-time invariant check enforces that exactly one active definition is the default, that the default is listed and ungated, and that a gated model is never listed.

#### 1a. Public surface: listed, active, gated

Three separate questions, each with its own field:

| Question | Field | Effect |
|---|---|---|
| Is it offered for new use? | `status` | A `retired` model leaves the cards and choice lists but still resolves its label for stored results. |
| Is it advertised? | `listed` | An unlisted model renders no selection card and appears in no choice control. Listing is presentation only, never protection: an unlisted, ungated model stays resolvable by anyone who names it, including through the REST API. |
| Does reaching it need a credential? | `requires_credential` | A gated model is reachable only by redeeming a model-scoped entry link and presenting a shared access code. It is refused outright by the REST API, and a session scoped to it may enter the classification flow only. |

`requires_credential=True` implies `listed=False`, and a deployment whose roster contains a gated model must configure the gate or refuse to start. Making a model public is clearing both flags — nothing else about the definition changes. See [Serving a gated model](../admin/gated-model-access.md) for the operator side.

#### 1b. Result surfaces

Result tabs are a declared list, not a flag per tab. Your definition names the surfaces it offers, in the order they render, and the first is the default tab:

```python
surfaces=(SURFACE_CLASSIFICATION,)                       # Classification only
surfaces=(SURFACE_CLASSIFICATION, SURFACE_DASH_TWINS)    # both, in that order
```

Every model must declare `SURFACE_CLASSIFICATION`. The ids are opaque to the registry; `app/astrodash/surfaces.py` maps each one to its tab title, its pane markup, and the supporting routes it owns. Declaring an id the surface map does not know refuses startup rather than rendering a broken tab.

Adding a *new* surface is one entry in `SURFACES` plus the id constant — no per-model conditional in `classify.html`. A guard test scans that template (and `batch.html`) for `model_type == '<id>'` comparisons and fails if one reappears, and a second guard walks every route the surface map declares and fails if one is missing the access gate.

#### 2. Implement the classifier

- Create: `app/astrodash/infrastructure/ml/classifiers/<your_model>_classifier.py`
- Inherit from `BaseClassifier` and implement:
  - Model loading from `Settings` (path + hyperparams)
  - `.classify(spectrum)` that accepts preprocessed arrays, runs inference on CPU/GPU, and returns a result consistent with existing models
  - Label mapping (transform logits -> class names) if needed, similar to `OnedCnnClassifier`

- If you need a custom architecture, add it under `app/astrodash/infrastructure/ml/classifiers/architectures.py` (or a sibling module) and instantiate it in your classifier.

#### 3. Add preprocessing (if needed)

- File: `app/astrodash/infrastructure/ml/data_processor.py`
- Pattern after `DashSpectrumProcessor` or `OnedCnnSpectrumProcessor`. Ensure the logic mirrors training (interpolation, normalization, shaping).
- Update: `app/astrodash/domain/services/spectrum_processing_service.py`
  - Set your definition's `preprocessing` identifier, then extend `prepare_for_model(self, spectrum, model_type)` with a matching `elif preprocessing == '<your_variant>'` branch returning exactly the tensors your classifier expects. `prepare_for_model` reads the variant from your definition (`get_definition(model_type).preprocessing`), so it keys on that identifier rather than a raw `model_type` literal.

#### 4. Template/redshift support (optional)

- If your model uses templates (for RLAP or redshift estimation), add a handler under `app/astrodash/infrastructure/ml/templates/` and wire it in `app/astrodash/infrastructure/ml/templates/template_factory.py`.
- If not supported, leave the content flags off. The redshift-estimation, template-overlay, and RLAP gates read the definition's `supports_*` flags, so a model with those flags `False` is excluded automatically — there is no per-model `model_type == 'dash'` branch to update. Whether a *tab* appears is the separate `surfaces` list.

#### 5. API validation and routing

- No allowed-list to widen. The REST endpoints in `app/astrodash/views.py` (`process_spectrum` and `batch_process`) resolve `modelType` through the registry via `_resolve_model_type`: any **active** definition is accepted automatically, and an omitted, unknown, or retired `modelType` returns `400`. Registering your definition with an active `status` is all the API needs to route to it. Listing is not consulted, so an unlisted, ungated model is reachable here; a **gated** model is refused, with a message deliberately identical to the unknown-model refusal.
- Extra-input requirements travel on the definition too. Set `redshift_input=REDSHIFT_INPUT_REQUIRED` and both the classify form and the batch view enforce it from that policy; you do not add a bespoke validation branch. `REDSHIFT_INPUT_NONE` goes further: neither the redshift field nor the Known Redshift checkbox renders, and a submission without one validates in both flows.

#### 6. Configuration

- File: `app/astrodash/config/settings.py`
  - Add env-backed fields for your model path and hyperparameters, e.g. `YOURMODEL_MODEL_PATH`, dims, layers, dropout, etc.
  - For label mapping (class index -> label), follow the `OnedCnnClassifier` pattern (a class-mapping file path such as `oned_cnn_z_class_mapping_path`).

#### 7. Tests

- Unit tests:
  - Extend classification service tests to call the service with your `model_type` and assert behavior.
  - If you add a new processor, test its core transformations.

- Integration (optional but helpful):
  - Mirror existing classifier integration tests with your checkpoint to achieve a smoke run.

### Frontend Changes (Django Templates)

The AstroDash frontend uses Django templates with Bootstrap, and the model surface is registry-driven — you do not hand-edit a template to add a card or a tab:

1. The model-selection cards render from the registry's **listed** definitions (title, description, color, feature tags, icon, recommended badge, order), so setting your definition's UI fields adds the card. The form choice lists are generated from the same listing.
2. The result tabs render from your `surfaces` list, in the declared order, with the first active.
3. Redshift controls follow your `redshift_input` policy; RLAP and template-overlay UI follow `supports_rlap` and `supports_template_overlays`. Set the fields rather than adding `'dash'` vs `'1dCNN_z'` branches in the template or form — a guard test scans the templates for exactly that.
4. Ensure the result display matches your outputs (e.g., a model with `supports_rlap=False` produces no RLAP values to show).

### Documentation Updates

- Add your model type to the accepted values in:
  - `docs/api/introduction.md`
  - `docs/api/architecture.md`
  - Endpoint docs that mention `model_type` (e.g., `docs/api/endpoints/process-spectrum.md`, `docs/guides/getting-started.md`)

- If your model doesn't support templates/redshift, note that those features remain DASH-only.

### Checklist

- Backend
  - Registry definition added (`definitions/<id>.py` + `MODELS` entry) and classifier implemented
  - Preprocessor and `prepare_for_model` updated
  - Settings and env variables added
  - Public surface declared: `status`, `listed`, `requires_credential`
  - `redshift_input` policy set, and content flags set (API validation and gates derive from them)

- Frontend
  - Definition UI fields set (card renders from the registry)
  - `surfaces` list declared in the order the tabs should render
  - Result view aligns with outputs

- Docs & Tests
  - Docs list the new `model_type`
  - Unit/integration tests added

- Ops
  - Model artifact present under `/data/pre_trained_models/<your_model>/...`
  - Startup environment exports configured

### Tips

- Keep the backend response shape consistent across models to minimize frontend changes.
- Mirror your training preprocessing exactly; subtle differences in interpolation or normalization can degrade performance.
- Use `torch.device('cuda' if available else 'cpu')` and move tensors/models with `.to(device)` to support both CPU and GPU.

## Add Model-Specific Assets/Templates

This section explains how to add the supporting assets required by models other than DASH — for example, statistical normalization files, input-shape specs, lookup tables, or any auxiliary resources your model needs at inference time.

### Overview

Model assets are used for:

- **Preprocessing alignment**: Normalization stats, wavelength grids, or tokenizer/featurizer vocabularies
- **Output interpretation**: Label metadata
- **Optional lookups**: Any auxiliary tables used by your model during inference

### Asset Requirements

1. **File structure**: Store assets alongside the model or under a clear subdirectory in `/data/pre_trained_models/<your_model>/assets/` (or with user models in `/data/user_models/<model_id>/`).
2. **Configuration file**: Provide a small JSON/YAML that declares:
   - `input_shapes`: list(s) of expected input shapes
   - `preprocessing`: any required normalization parameters or grids
   - `assets`: paths to auxiliary files the model will read at runtime

3. **Versioning**: Include an `asset_version` field and update it on changes.

### Adding Assets

**Step 1: Prepare assets**

1. **Define inputs**: Document the exact inputs your model expects (e.g., wavelength/flux/redshift tensors, shapes).
2. **Normalization**: Export means/stds or other scalars/grids used by training.
3. **Aux files**: Include any lookup tables or tokenizers needed at inference.

**Step 2: Place assets**

1. **Location**: Put assets under `/data/user_models/<model_id>/assets/` for user models, or `/data/pre_trained_models/<your_model>/assets/` for built-ins.
2. **Config**: Add a `model_assets.json` (or `.yaml`) that references these files and declares shapes/mappings.

**Step 3: Integration**

1. **Loader**: Ensure the model loader reads your `model_assets.json` and wires preprocessing accordingly.
2. **Factory/registry**: If introducing a new built-in model type, register it in the model registry (a `definitions/<id>.py` file plus a `MODELS` entry) so the API can route requests properly.
3. **Validation**: On startup or upload, validate that shapes are consistent with the serialized model.

### Validation and Testing

1. **Load test**: Confirm assets are discovered and parsed correctly.
2. **Shape test**: Verify dummy inputs shaped per `input_shapes` execute end-to-end.
3. **Repro test**: Run a known file and compare to expected outputs (tolerances as appropriate).

### Best Practices

- **Single source of truth**: Keep shapes and normalization in one config that code loads.
- **Relative paths**: Use paths relative to the asset config for portability.
- **Schema stability**: Evolve the asset schema with explicit version bumps.
- **Document assumptions**: Note wavelength ranges, required units, or preprocessing expectations.
