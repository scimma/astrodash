---
title: "feat: Deep-link WISeREP to the classified object"
type: feat
date: 2026-10-30
execution: code
---

# feat: Deep-link WISeREP to the classified object

## Summary

When a spectrum is chosen by searching WISeREP by name, the classify page says
"Source: WISeREP", but the link goes to the top level, `https://www.wiserep.org/`.
The "Redshift from WISeREP for <designation>" note under the redshift field
links to the same place. Both links should open that object's WISeREP page.

WISeREP addresses object pages by numeric object ID: `/object/1631` is SN 2011fe
(verified 2026-10-30). The designation forms do not work: `/object/2011fe` and
`/object?name=2011fe` both return 404. The search export AstroDash already
downloads (`/search/spectra?format=tsv`) has an `Obj. ID` column (SN 2011fe ->
1631), but the parser does not read it today.

The change reads `Obj. ID` at parse time. It carries the ID with the spectrum
through fields that are already JSON, so no schema migration is needed. When the
result line is built, the ID becomes `<wiserep_base_url>/object/<id>`.

## Decisions to honor

- **Link text and position do not change.** Only the target of the existing
  "WISeREP" link in "Source: WISeREP" changes, along with the target of the
  WISeREP link in the redshift note.
- **Fall back to the top level.** If the object ID is missing or not purely
  digits, both links point to `<wiserep_base_url>/`, as they do today. This covers:
  - spectra retained before this change;
  - candidate sets stored before this change;
  - session-held provenance from before the deploy;
  - an export without the column.
- **Build the link from settings.** Use `settings.wiserep_base_url` with any
  trailing `/` removed. The ID is untrusted upstream data, so it is used in the
  path only if it is all digits.
- **The web page only.** The REST API response is unchanged, and `views.py` is
  not touched.
- **Every model.** The deep link must appear whichever classifier the by-name
  lookup serves, including:
  - credential-gated models;
  - models that own their preprocessing and read the spectrum at full band.

  Every model takes the same path: the candidate goes through the WISeREP
  spectrum repository's `get_for_candidate`, and the result line through
  `_spectrum_provenance`. Put the ID in that shared path, never in a per-model
  branch. Any branch that adds a model or changes that path must re-check the
  link after rebasing onto this change.
- **Branch.** Work goes on a new branch from `main`.

## Implementation units

### U1. Read the object ID from the export

- **Files:**
  - `app/astrodash/infrastructure/wiserep/selection.py`
  - `app/astrodash/tests/test_wiserep_selection.py`
- **Changes:**
  - Add an `object_id` column to `_Columns`, with aliases `"Obj. ID"` and
    `"Object ID"`.
  - Add `object_id: str` to `SpectrumCandidate` and document it in the
    docstring.
  - Fill it in `_candidate`.
- **Tests:**
  - A row with `Obj. ID` 1631 yields a candidate with `object_id == "1631"`.
  - An export without the column yields `""`.
  - Eligibility, ranking and the ambiguity refusal are unchanged.

### U2. Keep the ID through stored candidate sets and the spectrum

- **Files:**
  - `app/astrodash/domain/services/spectrum_service.py` (`candidate_to_record`,
    `candidate_from_record`)
  - `app/astrodash/infrastructure/storage/wiserep_spectrum_repository.py`
    (the `Spectrum.meta` built in `get_for_candidate`)
  - `app/astrodash/tests/test_spectrum_by_name.py`
  - `app/astrodash/tests/test_wiserep_repository.py`
- **Changes:**
  - The record mapping writes and reads `"object_id"`, defaulting to `""` when
    the key is absent.
  - The fetched spectrum's `meta` gains `"object_id"`.
  - Retained `SpectrumRecord.meta` and `SpectrumCandidateSetRecord.candidates`
    are JSON fields, so they need no migration.
- **Tests:**
  - The record round-trip keeps the ID.
  - A legacy record without the key yields `""`.
  - The fetched spectrum's `meta["object_id"]` matches the candidate.
  - A spectrum served from retention keeps the ID it was saved with.
  - An aged candidate set without the key still classifies, and its result
    falls back to the top-level link.

### U3. Build the link and render it in both places

- **Files:**
  - `app/astrodash/ui_views.py` (`_spectrum_provenance`)
  - `app/astrodash/templates/astrodash/classify.html`
  - `app/astrodash/tests/test_classify_by_name.py`
- **Changes:**
  - `_spectrum_provenance` adds `wiserep_url`, which is:
    - `{base}/object/{id}` when the spectrum's `meta["object_id"]` is all
      digits;
    - `{base}/` otherwise.

    The held session provenance stores the dict whole, so it carries the URL
    across the overlay and epoch re-renders.
  - Both hard-coded `href="https://www.wiserep.org/"` occurrences in the
    template become
    `{{ spectrum_provenance.wiserep_url|default:"https://www.wiserep.org/" }}`.
    The `default` covers provenance held in a session from before the deploy.
- **Tests:**
  - With object ID `1631`, both the Source line and the redshift note render
    `https://www.wiserep.org/object/1631`.
  - A missing ID renders the top-level link.
  - A non-numeric ID such as `"../x"` or `"1631?a=b"` renders the top-level link.
  - Held session provenance without `wiserep_url` renders the top-level link.
  - A non-default `wiserep_base_url` is respected.
  - The deep link renders for each listed model and for a gated model that owns
    its preprocessing, served through the by-name path. The redshift-note
    assertion applies only where that model shows the note.
  - The REST by-name response is byte-for-byte unchanged.

## Sequencing

U1, then U2, then U3. Each unit's tests pass before the next starts. Run the
full suite at the end and format with `black`.
