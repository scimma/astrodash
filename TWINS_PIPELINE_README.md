# AstroDASH twins pipeline

A small, **S3-independent** management layer for the existing AstroDASH DAEP twins viewer. It does not replace or retrain DAEP, re-encode the spectra, change the cosine-neighbor search, apply PCA, or change uploaded-spectrum Milky Way extinction handling.

**Place the `twins_pipeline/` package and `tests/` directory at your repository root.** Existing application paths remain unchanged:

- `app/astrodash/data/phase_literature/literature_phases.json`: reviewed six-field catalog, committed to Git
- `/astrodash/twins/reviewed-literature.json`: existing Django endpoint that serves it
- `app/astrodash/daep_upload_preprocess.py`: existing upload preprocessing, unchanged
- `app/astrodash/daep_inference.py`: existing DAEP encoder/inference, unchanged
- `app/astrodash/daep_upload_metadata.py`: existing metadata integration, unchanged
- `app/astrodash/static/astrodash/explorer/spectral_twins_app.js`: existing UI, unchanged
- S3: existing bundles and model assets, unchanged

## Validate and edit metadata

From repository root:

```bash
python -m twins_pipeline validate
python -m twins_pipeline export-csv --output /tmp/twins_literature_review.csv
```

The CSV contains `observation_key,value_json`; `value_json` is the *entire existing six-element array*. Edit only the values you intend to change, preserving the other positions and exact observation keys. The tool does **not** infer peak dates, recompute phases, or restore older four-field metadata.

After editing:

```bash
python -m twins_pipeline update --csv /tmp/twins_literature_review.csv --output /tmp/literature_phases.updated.json
python -m json.tool /tmp/literature_phases.updated.json >/dev/null
diff -u app/astrodash/data/phase_literature/literature_phases.json /tmp/literature_phases.updated.json | less
```

When the diff is correct, copy the candidate into the existing Git-tracked catalog, commit through a PR, and deploy the application. **Do not add the reviewed literature file to the S3 manifest.** If a production deployment doesn't bundle repository data, configure Django's reviewed-literature endpoint to read the Git-tracked file shipped with the app (not the S3 model-data volume). This package alone cannot guarantee that deployment-specific wiring.

## Tests

```bash
python -m unittest discover -s tests -p 'test_catalog.py' -v
python -m twins_pipeline check-live
python -m twins_pipeline.smoke --skip-imports  # if dependencies live only inside Docker
```

For import testing, use your backend's Python environment and omit `--skip-imports`:

```bash
python -m twins_pipeline.smoke
```

To check existing AstroDASH tests, use your configured Django test runner *inside the application's environment*, including `app/astrodash/tests/test_spectral_twins_page.py` and its existing preprocessing/inference tests. Tests here cannot reproduce a full model inference without your local bundle and dependencies.

## Deployment rules

1. The Git-tracked reviewed catalog owns human-audited phases, peak dates, peak bands, and provenance. It must take precedence over old S3 bundle metadata in the viewer.
2. S3 integrity checks may continue for model files; do **not** globally disable them.
3. Do not apply MW extinction corrections or request MW extinction inputs for uploaded spectra.
4. Keep all 1024 DAEP latent dimensions for cosine-nearest-neighbor retrieval and UMAP, with **no PCA**.
5. Treat edits as reviewable PRs, not production writes or automatic Github polling. Cache busting and serving the deployed catalog remain the responsibility of the existing endpoint/viewer.

## Limitations

This is a self-contained, testable addition, **not an applied patch to your machine or a committed GitHub change**. Integration and full end-to-end tests require the working repository; only console logs were supplied in the conversation. The example CSV update format intentionally preserves the positional six-field schema without making guesses about positions 1-5.
