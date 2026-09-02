"""Characterization tests locking current classifier behavior (plan U1).

These pin the observable behavior of the two built-in models (DASH,
Transformer) at the form / service / view-gate seam -- deliberately without a
live model forward pass -- so the model-registry refactor (U2-U5) can prove it
preserved behavior. They must pass on the pre-refactor code and stay green
through every later unit.

The classification-dependent gates (twins stash, template overlays) are
exercised by driving the classify view with the spectrum/processing/
classification services mocked, so no weights or network access are needed.
"""

import json
import re
import tempfile
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.signals import request_finished
from django.db import close_old_connections
from django.http import HttpResponse
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
import numpy as np

from astrodash.config.settings import get_settings
from astrodash.forms import ClassifyForm, ModelSelectionForm, BatchForm, parse_redshift_csv
from astrodash.infrastructure.ml import model_registry
from astrodash.infrastructure.ml.data_processor import (
    LatentEncoderSpectrumProcessor,
    OnedCnnSpectrumProcessor,
)
from astrodash.ui_views import _batch_run_metadata, _format_batch_results
from astrodash.core.exceptions import ValidationException
from astrodash.domain.services.batch_processing_service import BatchProcessingService
from astrodash.domain.services.redshift_service import RedshiftService
from astrodash.domain.services.spectrum_processing_service import (
    SpectrumProcessingService,
)


class SelectModelPageParityTests(TestCase):
    """AE1: the select-model page offers every listed built-in card."""

    def _render_visible(self):
        """Render the select-model page and return its body with comments stripped.

        Returns:
            str: The rendered HTML with ``<!-- ... -->`` comments removed. The
            user-model/upload cards live inside comments ("functionality
            preserved, visuals disabled"), so they appear in the raw body but
            are not selectable; parity is about the *visible* cards.
        """
        # Mock the model service so the page's async list_models() call does no
        # real DB/filesystem work: it keeps the test hermetic (independent of
        # any uploaded user models) and avoids leaving a connection open in
        # asgiref's executor thread, which would break the suite's DROP DATABASE
        # teardown.
        model_svc = MagicMock(list_models=AsyncMock(return_value=[]))
        with patch("astrodash.ui_views.get_model_service", return_value=model_svc):
            resp = self.client.get(
                reverse("astrodash:model_selection") + "?action=classify"
            )
        self.assertEqual(resp.status_code, 200)
        return re.sub(r"<!--.*?-->", "", resp.content.decode(), flags=re.DOTALL)

    def test_only_dash_and_transformer_cards_are_selectable(self):
        visible = self._render_visible()
        # A *selectable card* is an element with an onclick="selectModel('...')"
        # attribute. (The bare selectModel('upload') call in the page's own
        # JavaScript is not a card, so match the attribute form specifically.)
        card_ids = re.findall(r'''onclick="selectModel\('([^']+)'\)"''', visible)
        self.assertEqual(
            card_ids,
            [
                "transformer",
                "dash",
                "1dCNN_z",
                "1dCNN_noz",
                "latent_z",
                "latent_noz",
            ],
        )
        self.assertNotIn("user_model", card_ids)
        self.assertNotIn("upload", card_ids)

    def test_cards_render_titles_descriptions_tags_badge_icon_and_order(self):
        visible = self._render_visible()
        # Titles and descriptions, unchanged from the hand-written cards.
        self.assertIn("Transformer Model", visible)
        self.assertIn("Dash Model", visible)
        self.assertIn(
            "Advanced transformer-based model with 5-class classification", visible
        )
        self.assertIn("CNN-based model from the original DASH paper", visible)
        # Feature tags for both models.
        for tag in ("Transformer", "5 Classes", "Fast Inference"):
            self.assertIn(f">{tag}</span>", visible)
        for tag in ("CNN", "Template Matching", "RLap Scores"):
            self.assertIn(f">{tag}</span>", visible)
        # DASH's RECOMMENDED badge and flask icon, and only DASH's.
        self.assertEqual(visible.count("RECOMMENDED"), 1)
        self.assertIn("bi-flask", visible)
        # Order: Transformer card precedes DASH card, as today.
        self.assertLess(
            visible.index("onclick=\"selectModel('transformer')\""),
            visible.index("onclick=\"selectModel('dash')\""),
        )

    def test_no_per_model_data_model_type_css_rule_remains(self):
        visible = self._render_visible()
        # The selected-state color is applied inline from data-color; no static
        # CSS rule keyed by data-model-type should remain.
        self.assertNotIn(".model-card.selected[data-model-type", visible)


class UnlistedModelSurfaceTests(TestCase):
    """AE1: an unlisted model leaves every surface an ordinary visitor reaches.

    DASH is made unlisted (but active and ungated) for the duration of each
    test, following the registry fixture idiom of ``dataclasses.replace`` over
    a patched roster. Transformer is untouched, so it stays the listed, ungated
    active default the registry invariants require, and doubles as the control
    proving only the unlisted model disappeared.
    """

    def _unlisted_roster(self):
        """Return a roster where DASH is active and ungated but not listed.

        Returns:
            tuple: A ``MODELS``-shaped tuple suitable for ``patch.object``.
        """
        transformer = model_registry.get_definition("transformer")
        dash = model_registry.get_definition("dash")
        return (transformer, replace(dash, listed=False))

    def _render_visible(self, action):
        """Render the select-model page for an action, comments stripped.

        Args:
            action: The ``action`` query value, ``"classify"`` or ``"batch"``.

        Returns:
            str: The rendered HTML with ``<!-- ... -->`` comments removed, so
            the assertions see only selectable cards (the user-model/upload
            cards live inside comments).
        """
        model_svc = MagicMock(list_models=AsyncMock(return_value=[]))
        with patch.object(model_registry, "MODELS", self._unlisted_roster()), patch(
            "astrodash.ui_views.get_model_service", return_value=model_svc
        ):
            resp = self.client.get(
                reverse("astrodash:model_selection") + f"?action={action}"
            )
        self.assertEqual(resp.status_code, 200)
        return re.sub(r"<!--.*?-->", "", resp.content.decode(), flags=re.DOTALL)

    def test_unlisted_model_renders_no_card_for_classify_action(self):
        visible = self._render_visible("classify")
        self.assertNotIn("onclick=\"selectModel('dash')\"", visible)
        self.assertIn("onclick=\"selectModel('transformer')\"", visible)

    def test_unlisted_model_renders_no_card_for_batch_action(self):
        visible = self._render_visible("batch")
        self.assertNotIn("onclick=\"selectModel('dash')\"", visible)
        self.assertIn("onclick=\"selectModel('transformer')\"", visible)

    def test_unlisted_model_absent_from_classify_form_choices(self):
        with patch.object(model_registry, "MODELS", self._unlisted_roster()):
            choices = [value for value, _ in ClassifyForm().fields["model"].choices]
        self.assertNotIn("dash", choices)
        self.assertIn("transformer", choices)
        # The user-uploaded entry is unrelated to listing and stays put.
        self.assertIn("user_uploaded", choices)

    def test_unlisted_model_absent_from_selection_form_choices(self):
        with patch.object(model_registry, "MODELS", self._unlisted_roster()):
            choices = [
                value for value, _ in ModelSelectionForm().fields["model_type"].choices
            ]
        self.assertNotIn("dash", choices)
        self.assertIn("transformer", choices)

    def _post_selection(self, model_type):
        """POST the selection form naming a model, with the model service mocked.

        Args:
            model_type: The ``model_type`` value to submit.

        Returns:
            The view's ``HttpResponse``.
        """
        model_svc = MagicMock(list_models=AsyncMock(return_value=[]))
        with patch.object(model_registry, "MODELS", self._unlisted_roster()), patch(
            "astrodash.ui_views.get_model_service", return_value=model_svc
        ):
            return self.client.post(
                reverse("astrodash:model_selection"),
                data={"model_type": model_type, "action_type": "classify"},
            )

    def test_selection_post_naming_unlisted_model_is_refused(self):
        resp = self._post_selection("dash")
        # Refused: the page redisplays instead of redirecting into classify,
        # and nothing is written to the session.
        self.assertEqual(resp.status_code, 200)
        self.assertIn("model_type", resp.context["form"].errors)
        self.assertIsNone(self.client.session.get("selected_model_type"))

    def test_selection_post_naming_listed_model_still_succeeds(self):
        resp = self._post_selection("transformer")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.client.session.get("selected_model_type"), "transformer")


class ClassifyFormRedshiftParityTests(TestCase):
    """AE2 / AE3: Transformer requires a redshift; DASH does not."""

    def _base_data(self, model):
        return {
            "supernova_name": "SN2011fe",
            "model": model,
            "smoothing": 0,
            "min_wave": 3500,
            "max_wave": 10000,
        }

    def test_transformer_requires_redshift(self):
        form = ClassifyForm(data=self._base_data("transformer"))
        self.assertFalse(form.is_valid())
        self.assertIn("redshift", form.errors)
        self.assertTrue(
            any("Redshift is required" in e for e in form.errors["redshift"])
        )

    def test_required_redshift_message_names_no_model(self):
        """R13: the message follows the policy, so it names no model literally."""
        form = ClassifyForm(data=self._base_data("transformer"))
        self.assertFalse(form.is_valid())
        for error in form.errors["redshift"]:
            self.assertNotIn("Transformer", error)
            self.assertNotIn("transformer", error)
            self.assertNotIn("Dash", error)

    def test_dash_does_not_require_redshift(self):
        form = ClassifyForm(data=self._base_data("dash"))
        self.assertTrue(form.is_valid(), form.errors)

    def test_1dcnn_z_and_latent_z_require_redshift(self):
        for model in ("1dCNN_z", "latent_z"):
            with self.subTest(model=model):
                form = ClassifyForm(data=self._base_data(model))
                self.assertFalse(form.is_valid())
                self.assertIn("redshift", form.errors)
                self.assertTrue(
                    any("Redshift is required" in e for e in form.errors["redshift"])
                )

    def test_1dcnn_noz_and_latent_noz_do_not_require_redshift(self):
        for model in ("1dCNN_noz", "latent_noz"):
            with self.subTest(model=model):
                form = ClassifyForm(data=self._base_data(model))
                self.assertTrue(form.is_valid(), form.errors)


class BatchRlapParityTests(TestCase):
    """RLAP is populated only for DASH and only when requested."""

    def _results(self):
        return {
            "a.dat": {
                "classification": {
                    "best_match": {
                        "type": "Ia",
                        "age": "2 to 6",
                        "probability": 0.9,
                        "redshift": 0.01,
                        "rlap": 7.5,
                    }
                }
            }
        }

    def test_rlap_populated_for_dash_when_requested(self):
        out = _format_batch_results(
            self._results(), {"modelType": "dash", "calculateRlap": True}
        )
        self.assertEqual(out["a.dat"]["rlap"], 7.5)

    def test_rlap_absent_for_transformer(self):
        out = _format_batch_results(
            self._results(), {"modelType": "transformer", "calculateRlap": True}
        )
        self.assertEqual(out["a.dat"]["rlap"], "-")

    def test_rlap_absent_when_not_requested(self):
        out = _format_batch_results(
            self._results(), {"modelType": "dash", "calculateRlap": False}
        )
        self.assertEqual(out["a.dat"]["rlap"], "-")


class BatchRunMetadataTests(TestCase):
    """Run-level classification metadata is attached to every batch result row."""

    def _params(self, **extra):
        params = {
            "smoothing": 6,
            "minWave": 4000,
            "maxWave": 8000,
            "knownZ": False,
            "zValue": None,
            "calculateRlap": False,
            "modelType": "dash",
        }
        params.update(extra)
        return params

    def test_missing_input_redshift_is_none(self):
        meta = _batch_run_metadata("dash", self._params(), classified_at="2026-08-27T04:00:00+00:00")
        self.assertIsNone(meta["input_redshift"])
        self.assertEqual(meta["model_type"], "dash")
        self.assertEqual(meta["smoothing"], 6)
        self.assertEqual(meta["min_wave"], 4000)
        self.assertEqual(meta["max_wave"], 8000)
        self.assertEqual(meta["classified_at"], "2026-08-27T04:00:00+00:00")

    def test_submitted_input_redshift_is_preserved(self):
        meta = _batch_run_metadata(
            "transformer", self._params(zValue=0.05), classified_at="t"
        )
        self.assertEqual(meta["input_redshift"], 0.05)
        self.assertEqual(meta["model_type"], "transformer")

    def test_user_uploaded_model_type_is_not_rewritten_to_dash(self):
        # params['modelType'] is a display fallback of 'dash' for user models;
        # recorded metadata must keep the selected type.
        meta = _batch_run_metadata(
            "user_uploaded", self._params(modelType="dash"), classified_at="t"
        )
        self.assertEqual(meta["model_type"], "user_uploaded")

    def test_metadata_is_copied_onto_success_and_error_rows(self):
        results = {
            "ok.dat": {
                "classification": {
                    "best_match": {
                        "type": "Ia",
                        "age": "2 to 6",
                        "probability": 0.9,
                        "redshift": 0.01,
                    }
                }
            },
            "bad.xyz": {"error": "Unsupported file type"},
        }
        meta = _batch_run_metadata(
            "dash", self._params(zValue=0.12), classified_at="2026-08-27T04:00:00+00:00"
        )
        out = _format_batch_results(results, self._params(), metadata=meta)
        for filename in ("ok.dat", "bad.xyz"):
            self.assertEqual(out[filename]["classified_at"], "2026-08-27T04:00:00+00:00")
            self.assertEqual(out[filename]["model_type"], "dash")
            self.assertEqual(out[filename]["smoothing"], 6)
            self.assertEqual(out[filename]["min_wave"], 4000)
            self.assertEqual(out[filename]["max_wave"], 8000)
            self.assertEqual(out[filename]["input_redshift"], 0.12)
        self.assertEqual(out["ok.dat"]["type"], "Ia")
        self.assertEqual(out["bad.xyz"]["error"], "Unsupported file type")

    def test_batch_view_exposes_run_metadata_on_the_page_and_rows(self):
        session = self.client.session
        session["selected_model_type"] = "dash"
        session.save()
        fake_results = {
            "a.dat": {
                "classification": {
                    "best_match": {
                        "type": "Ia",
                        "age": "2 to 6",
                        "probability": 0.9,
                        "redshift": 0.01,
                    }
                }
            }
        }
        upload = SimpleUploadedFile("a.dat", b"3500 1.0\n3600 1.1\n")
        batch_svc = MagicMock(process_batch=AsyncMock(return_value=fake_results))
        with patch(
            "astrodash.ui_views.get_batch_processing_service", return_value=batch_svc
        ):
            resp = self.client.post(
                reverse("astrodash:batch_process_ui"),
                data={
                    "smoothing": 6,
                    "min_wave": 4000,
                    "max_wave": 8000,
                    "redshift": 0.03,
                    "files": upload,
                },
            )
        self.assertEqual(resp.status_code, 200)
        meta = resp.context["run_metadata"]
        self.assertEqual(meta["model_type"], "dash")
        self.assertEqual(meta["smoothing"], 6)
        self.assertEqual(meta["min_wave"], 4000)
        self.assertEqual(meta["max_wave"], 8000)
        self.assertEqual(meta["input_redshift"], 0.03)
        self.assertTrue(meta["classified_at"])
        row = resp.context["results"]["a.dat"]
        self.assertEqual(row["model_type"], "dash")
        self.assertEqual(row["input_redshift"], 0.03)
        html = resp.content.decode()
        self.assertIn("Classification metadata", html)
        self.assertIn("Classified at", html)
        self.assertIn("Input redshift", html)
        self.assertIn("'Classified At'", html)
        self.assertIn("'Input Redshift'", html)


class BatchRedshiftCsvTests(TestCase):
    """Batch redshift is a CSV list, one value per spectrum, lengths must match."""

    def test_parse_redshift_csv_accepts_list_or_single_value(self):
        self.assertEqual(parse_redshift_csv("[1, 0.01, 0.1]"), [1.0, 0.01, 0.1])
        self.assertEqual(parse_redshift_csv("0.01, 0.1"), [0.01, 0.1])
        self.assertEqual(parse_redshift_csv("0.03"), [0.03])
        self.assertEqual(parse_redshift_csv(""), [])
        self.assertEqual(parse_redshift_csv(None), [])

    def test_batch_form_parses_csv_list(self):
        form = BatchForm(
            data={
                "smoothing": 0,
                "min_wave": 3500,
                "max_wave": 10000,
                "redshift": "[0.01, 0.02]",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["redshift"], [0.01, 0.02])

    def test_process_file_list_applies_redshifts_in_order(self):
        calls = []

        async def get_spectrum(file):
            return SimpleNamespace(
                x=[1.0], y=[1.0], file_name=file.name, redshift=None, meta={}
            )

        async def process(spec, params):
            calls.append((spec.file_name, params.get("zValue")))
            spec.redshift = params.get("zValue")
            return spec

        classification_svc = MagicMock()
        classification_svc.model_factory.get_classifier = MagicMock(return_value=object())
        classification_svc.classify_spectrum = AsyncMock(
            return_value=SimpleNamespace(results={"best_match": {}})
        )
        svc = BatchProcessingService(
            MagicMock(get_spectrum_from_file=get_spectrum),
            classification_svc,
            MagicMock(process_spectrum_with_params=process),
        )
        files = [
            SimpleUploadedFile("a.dat", b"1 1"),
            SimpleUploadedFile("b.dat", b"1 1"),
        ]
        out = async_to_sync(svc.process_batch)(
            files, {"zValues": [0.1, 0.2], "smoothing": 0}, "dash"
        )
        self.assertEqual(dict(calls), {"a.dat": 0.1, "b.dat": 0.2})
        self.assertEqual(out["a.dat"]["applied_redshift"], 0.1)
        self.assertEqual(out["b.dat"]["applied_redshift"], 0.2)

    def test_mismatched_redshift_and_spectrum_counts_fail(self):
        classification_svc = MagicMock()
        classification_svc.model_factory.get_classifier = MagicMock(return_value=object())
        svc = BatchProcessingService(
            MagicMock(), classification_svc, MagicMock()
        )
        files = [
            SimpleUploadedFile("a.dat", b"1 1"),
            SimpleUploadedFile("b.dat", b"1 1"),
        ]
        with self.assertRaises(ValidationException) as ctx:
            async_to_sync(svc.process_batch)(
                files, {"zValues": [0.1], "smoothing": 0}, "dash"
            )
        self.assertIn("1 redshift", str(ctx.exception.message))
        self.assertIn("2 spectrum", str(ctx.exception.message))


class RedshiftEstimationGateParityTests(TestCase):
    """Redshift estimation is refused for any non-DASH model."""

    def test_non_dash_is_rejected(self):
        svc = RedshiftService()
        out = async_to_sync(svc.estimate_redshift_from_spectrum)(
            [4000.0, 5000.0, 6000.0],
            [1.0, 1.0, 1.0],
            "Ia",
            "2 to 6",
            model_type="transformer",
        )
        self.assertIsNone(out["estimated_redshift"])
        self.assertIn("only available for DASH", out["message"])

    def test_dash_passes_the_gate(self):
        # DASH supports redshift estimation, so it is not short-circuited by the
        # capability gate: it proceeds past it and here fails downstream on the
        # patched template loader. The returned message is that downstream
        # failure, not the non-DASH gate message -- proving DASH cleared the gate.
        svc = RedshiftService()
        with patch(
            "astrodash.domain.services.redshift_service.prepare_log_wavelength_and_templates",
            side_effect=RuntimeError("reached template loading"),
        ):
            out = async_to_sync(svc.estimate_redshift_from_spectrum)(
                [4000.0, 5000.0, 6000.0],
                [1.0, 1.0, 1.0],
                "Ia",
                "2 to 6",
                model_type="dash",
            )
        self.assertNotIn("only available for DASH", out["message"])
        self.assertIn("reached template loading", out["message"])


class PreprocessingVariantParityTests(TestCase):
    """prepare_for_model selects the processor from the model's preprocessing field."""

    def _service(self):
        svc = SpectrumProcessingService()
        # Replace the real processors with mocks so branch selection is
        # characterized without running any spectral preprocessing.
        svc.dash_processor = MagicMock(
            process=MagicMock(return_value=([1.0, 2.0], 0, 2, 0.11))
        )
        svc.transformer_processor = MagicMock(
            process=MagicMock(return_value=([3.0], [4.0], 0.22))
        )
        svc.oned_cnn_processor = MagicMock(
            process=MagicMock(return_value=[0.1] * 1025)
        )
        svc.latent_processor = MagicMock(
            process=MagicMock(
                return_value={
                    "flux": [0.2] * 1320,
                    "wavelength": [3202.5] * 1320,
                    "mask": [False] * 1320,
                }
            )
        )
        return svc

    def _spectrum(self):
        return SimpleNamespace(x=[4000.0, 5000.0], y=[1.0, 2.0], redshift=0.05)

    def test_dash_uses_dash_processor(self):
        svc = self._service()
        out = svc.prepare_for_model(self._spectrum(), "dash")
        svc.dash_processor.process.assert_called_once()
        svc.transformer_processor.process.assert_not_called()
        svc.oned_cnn_processor.process.assert_not_called()
        svc.latent_processor.process.assert_not_called()
        # DASH result shape carries the processor's min/max indices.
        self.assertIn("min_idx", out)
        self.assertIn("max_idx", out)
        self.assertEqual(out["redshift"], 0.11)

    def test_transformer_uses_transformer_processor(self):
        svc = self._service()
        out = svc.prepare_for_model(self._spectrum(), "transformer")
        svc.transformer_processor.process.assert_called_once()
        svc.dash_processor.process.assert_not_called()
        svc.oned_cnn_processor.process.assert_not_called()
        svc.latent_processor.process.assert_not_called()
        # Transformer result shape has no min/max indices.
        self.assertNotIn("min_idx", out)
        self.assertEqual(out["redshift"], 0.22)

    def test_user_model_passes_through_untouched(self):
        svc = self._service()
        out = svc.prepare_for_model(self._spectrum(), "user_uploaded")
        svc.dash_processor.process.assert_not_called()
        svc.transformer_processor.process.assert_not_called()
        svc.oned_cnn_processor.process.assert_not_called()
        svc.latent_processor.process.assert_not_called()
        # No definition -> pass-through: input redshift is returned unchanged.
        self.assertEqual(out["redshift"], 0.05)

    def test_1dcnn_z_uses_oned_cnn_processor_with_redshift(self):
        svc = self._service()
        out = svc.prepare_for_model(self._spectrum(), "1dCNN_z")
        svc.oned_cnn_processor.process.assert_called_once()
        call = svc.oned_cnn_processor.process.call_args
        self.assertTrue(call.kwargs["include_redshift"])
        self.assertEqual(call.args[2], 0.05)
        self.assertIs(out["model_input"], out["y"])
        svc.latent_processor.process.assert_not_called()
        svc.dash_processor.process.assert_not_called()

    def test_1dcnn_noz_uses_oned_cnn_processor_without_redshift_feature(self):
        svc = self._service()
        out = svc.prepare_for_model(self._spectrum(), "1dCNN_noz")
        svc.oned_cnn_processor.process.assert_called_once()
        call = svc.oned_cnn_processor.process.call_args
        self.assertFalse(call.kwargs["include_redshift"])
        self.assertEqual(out["redshift"], 0.05)
        svc.latent_processor.process.assert_not_called()

    def test_latent_z_uses_latent_processor_with_deredshift(self):
        svc = self._service()
        out = svc.prepare_for_model(self._spectrum(), "latent_z")
        svc.latent_processor.process.assert_called_once()
        call = svc.latent_processor.process.call_args
        self.assertTrue(call.kwargs["deredshift"])
        self.assertEqual(len(out["model_input"]["flux"]), 1320)
        svc.oned_cnn_processor.process.assert_not_called()

    def test_latent_noz_uses_latent_processor_without_deredshift(self):
        svc = self._service()
        out = svc.prepare_for_model(self._spectrum(), "latent_noz")
        svc.latent_processor.process.assert_called_once()
        call = svc.latent_processor.process.call_args
        self.assertFalse(call.kwargs["deredshift"])
        self.assertIn("mask", out["model_input"])
        svc.oned_cnn_processor.process.assert_not_called()


def _dense_spectrum(redshift=0.05):
    wave = np.linspace(3500.0, 10000.0, 400)
    flux = np.linspace(0.5, 1.5, 400)
    return wave, flux, redshift


class OnedCnnSpectrumProcessorTests(SimpleTestCase):
    def setUp(self):
        self.processor = OnedCnnSpectrumProcessor()
        self.wave, self.flux, self.z = _dense_spectrum()
        self.cnn_length = get_settings().oned_cnn_input_length()

    def test_z_variant_length_last_sample_is_z(self):
        out = self.processor.process(
            self.wave, self.flux, self.z, include_redshift=True
        )
        self.assertEqual(out.shape, (self.cnn_length,))
        self.assertAlmostEqual(float(out[-1]), self.z, places=6)

    def test_noz_variant_last_sample_is_zero(self):
        out = self.processor.process(
            self.wave, self.flux, self.z, include_redshift=False
        )
        self.assertEqual(out.shape, (self.cnn_length,))
        self.assertEqual(float(out[-1]), 0.0)


class LatentEncoderSpectrumProcessorTests(SimpleTestCase):
    def setUp(self):
        self.processor = LatentEncoderSpectrumProcessor()
        self.n_wave = get_settings().latent_encoder_n_wave

    def test_output_lengths_match_encoder_grid(self):
        wave = np.linspace(4000.0, 9000.0, 300)
        flux = np.ones(300)
        out = self.processor.process(wave, flux, 0.05, deredshift=False)
        self.assertEqual(out["flux"].shape, (self.n_wave,))
        self.assertEqual(out["wavelength"].shape, (self.n_wave,))
        self.assertEqual(out["mask"].shape, (self.n_wave,))

    def test_deredshift_changes_wavelength_mapping(self):
        wave = np.linspace(5000.0, 6000.0, 120)
        flux = np.ones_like(wave)
        z = 0.2
        dered = self.processor.process(wave, flux, z, deredshift=True)
        observed = self.processor.process(wave, flux, z, deredshift=False)
        self.assertFalse(np.array_equal(dered["mask"], observed["mask"]))
        self.assertFalse(np.array_equal(dered["flux"], observed["flux"]))

    def test_uncovered_grid_edges_are_ignored(self):
        wave = np.linspace(4000.0, 9000.0, 300)
        flux = np.ones(300)
        out = self.processor.process(wave, flux, 0.0, deredshift=False)
        self.assertTrue(np.any(out["mask"]))
        self.assertTrue(out["mask"][0])
        self.assertTrue(out["mask"][-1])
        self.assertFalse(np.all(out["mask"]))


class BatchRedshiftGateParityTests(TestCase):
    """The batch view requires a redshift only for models whose definition does."""

    def _post_batch(self, model_type):
        session = self.client.session
        session["selected_model_type"] = model_type
        session.save()
        return self.client.post(
            reverse("astrodash:batch_process_ui"),
            data={"smoothing": 0, "min_wave": 3500, "max_wave": 10000},
        )

    def test_transformer_batch_requires_redshift(self):
        resp = self._post_batch("transformer")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("redshift", resp.context["form"].errors)
        self.assertTrue(
            any(
                "Redshift is required" in e
                for e in resp.context["form"].errors["redshift"]
            )
        )

    def test_batch_required_redshift_message_names_no_model(self):
        """R13: the batch gate's message follows the policy, naming no model."""
        resp = self._post_batch("transformer")
        for error in resp.context["form"].errors["redshift"]:
            self.assertNotIn("Transformer", error)
            self.assertNotIn("transformer", error)
            self.assertNotIn("Dash", error)

    def test_dash_batch_does_not_require_redshift(self):
        resp = self._post_batch("dash")
        self.assertEqual(resp.status_code, 200)
        # DASH does not require a redshift, so the gate adds no redshift error
        # (the request instead falls through to the "no files uploaded" path).
        self.assertNotIn("redshift", resp.context["form"].errors)


class RedshiftInputPolicyFlowTests(TestCase):
    """R13/R14/AE4: the three-way redshift input policy governs both UI flows.

    A model declaring :data:`REDSHIFT_INPUT_NONE` takes no redshift at all, so
    neither the redshift field nor the Known Redshift checkbox may render, and
    a submission without a redshift must validate -- in the classification flow
    and the batch flow alike, which run through two independent gates (the
    classify form's ``clean`` and the batch view's own check).

    DASH is the model whose policy is varied, following the registry fixture
    idiom of ``dataclasses.replace`` over a patched roster; Transformer is left
    untouched so it keeps satisfying the registry invariants and doubles as the
    unchanged control.
    """

    # Markers for the two controls as crispy renders them.
    REDSHIFT_FIELD_MARKER = 'id="id_redshift"'
    KNOWN_Z_FIELD_MARKER = 'id="id_known_z"'

    def _roster(self, policy):
        """Return a roster where DASH declares the given redshift input policy.

        Args:
            policy: One of the ``REDSHIFT_INPUT_*`` constants.

        Returns:
            tuple: A ``MODELS``-shaped tuple suitable for ``patch.object``.
        """
        transformer = model_registry.get_definition("transformer")
        dash = model_registry.get_definition("dash")
        return (transformer, replace(dash, redshift_input=policy))

    def _base_classify_data(self, model):
        return {
            "supernova_name": "SN2011fe",
            "model": model,
            "smoothing": 0,
            "min_wave": 3500,
            "max_wave": 10000,
        }

    def _render_visible(self, url_name, model_type, roster=None):
        """GET a flow's page with a model selected and return its visible HTML.

        Args:
            url_name: The URL name to reverse (``astrodash:classify`` or
                ``astrodash:batch_process_ui``).
            model_type: The value to place in the session as the selected model.
            roster: Optional ``MODELS``-shaped tuple to patch in for the request.

        Returns:
            str: The rendered HTML with ``<!-- ... -->`` comments removed, so
            assertions see only what actually renders.
        """
        session = self.client.session
        session["selected_model_type"] = model_type
        if model_type == "user_uploaded":
            session["selected_model_id"] = "user-model-1"
        session.save()

        # The classify view resolves a display name for a user-uploaded model;
        # mock the service so no DB/filesystem work happens.
        model_svc = MagicMock(
            get_model=AsyncMock(return_value=SimpleNamespace(name="My uploaded model"))
        )
        roster_patch = (
            patch.object(model_registry, "MODELS", roster)
            if roster is not None
            else nullcontext()
        )
        with roster_patch, patch(
            "astrodash.ui_views.get_model_service", return_value=model_svc
        ):
            resp = self.client.get(reverse(url_name))
        self.assertEqual(resp.status_code, 200)
        return re.sub(r"<!--.*?-->", "", resp.content.decode(), flags=re.DOTALL)

    def _post_batch(self, model_type, roster=None, **extra):
        """POST the batch form for a selected model, with an optional roster."""
        session = self.client.session
        session["selected_model_type"] = model_type
        session.save()
        data = {"smoothing": 0, "min_wave": 3500, "max_wave": 10000}
        data.update(extra)
        roster_patch = (
            patch.object(model_registry, "MODELS", roster)
            if roster is not None
            else nullcontext()
        )
        with roster_patch:
            return self.client.post(reverse("astrodash:batch_process_ui"), data=data)

    # --- rendering: neither control for a model that declines redshift ---

    def test_declining_model_renders_no_redshift_controls_in_classify(self):
        """AE4: no redshift field and no Known Redshift checkbox."""
        html = self._render_visible(
            "astrodash:classify",
            "dash",
            self._roster(model_registry.REDSHIFT_INPUT_NONE),
        )
        self.assertNotIn(self.REDSHIFT_FIELD_MARKER, html)
        self.assertNotIn(self.KNOWN_Z_FIELD_MARKER, html)
        # A control proving the form itself still rendered.
        self.assertIn('id="id_smoothing"', html)

    def test_declining_model_renders_no_redshift_controls_in_batch(self):
        html = self._render_visible(
            "astrodash:batch_process_ui",
            "dash",
            self._roster(model_registry.REDSHIFT_INPUT_NONE),
        )
        self.assertNotIn(self.REDSHIFT_FIELD_MARKER, html)
        self.assertNotIn(self.KNOWN_Z_FIELD_MARKER, html)
        self.assertIn('id="id_smoothing"', html)

    def test_optional_policy_still_renders_both_controls_in_classify(self):
        html = self._render_visible("astrodash:classify", "dash")
        self.assertIn(self.REDSHIFT_FIELD_MARKER, html)
        self.assertIn(self.KNOWN_Z_FIELD_MARKER, html)

    def test_optional_policy_still_renders_both_controls_in_batch(self):
        html = self._render_visible("astrodash:batch_process_ui", "dash")
        self.assertIn(self.REDSHIFT_FIELD_MARKER, html)
        self.assertIn(self.KNOWN_Z_FIELD_MARKER, html)

    def test_required_policy_still_renders_both_controls_in_classify(self):
        html = self._render_visible("astrodash:classify", "transformer")
        self.assertIn(self.REDSHIFT_FIELD_MARKER, html)
        self.assertIn(self.KNOWN_Z_FIELD_MARKER, html)

    def test_1dcnn_z_and_latent_z_render_redshift_controls_in_classify(self):
        for model in ("1dCNN_z", "latent_z"):
            with self.subTest(model=model):
                html = self._render_visible("astrodash:classify", model)
                self.assertIn(self.REDSHIFT_FIELD_MARKER, html)
                self.assertIn(self.KNOWN_Z_FIELD_MARKER, html)

    def test_1dcnn_noz_and_latent_noz_render_no_redshift_controls_in_classify(self):
        for model in ("1dCNN_noz", "latent_noz"):
            with self.subTest(model=model):
                html = self._render_visible("astrodash:classify", model)
                self.assertNotIn(self.REDSHIFT_FIELD_MARKER, html)
                self.assertNotIn(self.KNOWN_Z_FIELD_MARKER, html)
                self.assertIn('id="id_smoothing"', html)

    # --- validation: a submission omitting redshift passes in both flows ---

    def test_declining_model_validates_without_redshift_in_classify(self):
        with patch.object(
            model_registry, "MODELS", self._roster(model_registry.REDSHIFT_INPUT_NONE)
        ):
            form = ClassifyForm(data=self._base_classify_data("dash"))
            self.assertTrue(form.is_valid(), form.errors)

    def test_declining_model_validates_without_redshift_in_batch(self):
        resp = self._post_batch(
            "dash", roster=self._roster(model_registry.REDSHIFT_INPUT_NONE)
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("redshift", resp.context["form"].errors)

    def test_required_policy_rejects_missing_redshift_in_classify(self):
        """The gate follows the policy, not the model: DASH made required fails."""
        with patch.object(
            model_registry,
            "MODELS",
            self._roster(model_registry.REDSHIFT_INPUT_REQUIRED),
        ):
            form = ClassifyForm(data=self._base_classify_data("dash"))
            self.assertFalse(form.is_valid())
            self.assertIn("redshift", form.errors)

    def test_required_policy_rejects_missing_redshift_in_batch(self):
        resp = self._post_batch(
            "dash", roster=self._roster(model_registry.REDSHIFT_INPUT_REQUIRED)
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("redshift", resp.context["form"].errors)

    # --- KTD10: an unresolvable selection keeps today's optional behavior ---

    def test_user_uploaded_selection_still_renders_both_controls(self):
        html = self._render_visible("astrodash:classify", "user_uploaded")
        self.assertIn(self.REDSHIFT_FIELD_MARKER, html)
        self.assertIn(self.KNOWN_Z_FIELD_MARKER, html)

    def test_user_uploaded_selection_still_renders_both_controls_in_batch(self):
        html = self._render_visible("astrodash:batch_process_ui", "user_uploaded")
        self.assertIn(self.REDSHIFT_FIELD_MARKER, html)
        self.assertIn(self.KNOWN_Z_FIELD_MARKER, html)

    def test_user_uploaded_selection_validates_without_redshift(self):
        form = ClassifyForm(data=self._base_classify_data("user_uploaded"))
        self.assertTrue(form.is_valid(), form.errors)

    def test_user_uploaded_batch_validates_without_redshift(self):
        resp = self._post_batch("user_uploaded")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("redshift", resp.context["form"].errors)


class ClassifyViewGateParityTests(TestCase):
    """AE4: twins stash + template-overlay eligibility, gated on the model.

    The three service getters are mocked so the view runs its gate logic on a
    constructed classification result without any model weights. `render` is
    patched out so template rendering can't interfere; the gate outcomes are
    read back from the session, which the view writes before any plotting.
    """

    def _run_classify(self, model_type):
        client = Client()
        session = client.session
        session["selected_model_type"] = model_type
        session.save()

        fake_processed = MagicMock()
        fake_processed.x = [3000.0, 5000.0, 9000.0]
        fake_processed.y = [1.0, 2.0, 1.0]

        fake_classification = MagicMock()
        fake_classification.model_type = model_type
        fake_classification.results = {"best_matches": [], "embedding": [0.0] * 1024}

        spectrum_svc = MagicMock(get_spectrum_data=AsyncMock(return_value=MagicMock()))
        processing_svc = MagicMock(
            process_spectrum_with_params=AsyncMock(return_value=fake_processed)
        )
        classification_svc = MagicMock(
            classify_spectrum=AsyncMock(return_value=fake_classification)
        )

        data = {
            "supernova_name": "SN2011fe",
            "model": model_type,
            "smoothing": 0,
            "min_wave": 3500,
            "max_wave": 10000,
        }
        definition = model_registry.get_definition(model_type)
        if (
            definition is not None
            and definition.redshift_input == model_registry.REDSHIFT_INPUT_REQUIRED
        ):
            data["redshift"] = "0.005"

        with patch(
            "astrodash.ui_views.get_spectrum_service", return_value=spectrum_svc
        ), patch(
            "astrodash.ui_views.get_spectrum_processing_service",
            return_value=processing_svc,
        ), patch(
            "astrodash.ui_views.get_classification_service",
            return_value=classification_svc,
        ), patch(
            "astrodash.ui_views.render", return_value=HttpResponse(b"")
        ):
            client.post(reverse("astrodash:classify"), data=data)
        return client.session

    def test_dash_stashes_twins_embedding_and_enables_templates(self):
        session = self._run_classify("dash")
        self.assertEqual(session.get("classify_dash_embedding"), [0.0] * 1024)
        self.assertTrue(session.get("classify_show_templates_section"))

    def test_transformer_no_twins_embedding_and_no_templates(self):
        session = self._run_classify("transformer")
        self.assertNotIn("classify_dash_embedding", session)
        self.assertFalse(session.get("classify_show_templates_section"))


class ResultSurfaceRenderingTests(TestCase):
    """R17/R20/AE7: the tab strip and panes come from the declared list.

    Per KTD5 the strip renders from the *selected* model, not the classified
    one: today a DASH-selected page shows the DASH Twins tab before any
    classification has run, and that must stay true. Every test here therefore
    drives a session with a selection and no classification artifacts at all.
    """

    CLASSIFICATION_TAB = 'id="classification-tab"'
    CLASSIFICATION_PANE = 'id="classification-pane"'
    TWINS_TAB = 'id="twins-tab"'
    TWINS_PANE = 'id="twins-pane"'

    def _render_visible(self, model_type):
        """GET the classify page with a model selected and nothing classified.

        Args:
            model_type: The value to place in the session as the selected model.

        Returns:
            str: The rendered HTML with ``<!-- ... -->`` comments removed.
        """
        session = self.client.session
        session["selected_model_type"] = model_type
        if model_type == "user_uploaded":
            session["selected_model_id"] = "user-model-1"
        session.save()

        model_svc = MagicMock(
            get_model=AsyncMock(return_value=SimpleNamespace(name="My uploaded model"))
        )
        with patch("astrodash.ui_views.get_model_service", return_value=model_svc):
            resp = self.client.get(reverse("astrodash:classify"))
        self.assertEqual(resp.status_code, 200)
        return re.sub(r"<!--.*?-->", "", resp.content.decode(), flags=re.DOTALL)

    @staticmethod
    def _tag_with_id(html, dom_id):
        """Return the opening tag carrying ``id="<dom_id>"``, or ``None``."""
        match = re.search(r"<[a-z]+[^>]*\bid=\"%s\"[^>]*>" % re.escape(dom_id), html)
        return match.group(0) if match else None

    def test_dash_renders_both_tabs_in_declared_order_before_any_classification(self):
        """AE7: DASH offers Classification then DASH Twins, pre-classification."""
        html = self._render_visible("dash")
        self.assertIn(self.CLASSIFICATION_TAB, html)
        self.assertIn(self.TWINS_TAB, html)
        self.assertIn("DASH Twins", html)
        # Declared order: Classification first, DASH Twins second.
        self.assertLess(html.index(self.CLASSIFICATION_TAB), html.index(self.TWINS_TAB))

    def test_dash_renders_both_panes_wired_to_their_tabs(self):
        html = self._render_visible("dash")
        for tab, pane in (
            (self.CLASSIFICATION_TAB, "classification-pane"),
            (self.TWINS_TAB, "twins-pane"),
        ):
            with self.subTest(tab=tab):
                anchor = self._tag_with_id(html, tab.split('"')[1])
                self.assertIsNotNone(anchor)
                self.assertIn(f'href="#{pane}"', anchor)
                self.assertIn(f'aria-controls="{pane}"', anchor)
                self.assertIsNotNone(self._tag_with_id(html, pane))

    def test_first_declared_surface_is_the_active_tab_and_pane(self):
        """R17: the first declared surface is the default, the rest are not."""
        html = self._render_visible("dash")
        classification_tab = self._tag_with_id(html, "classification-tab")
        twins_tab = self._tag_with_id(html, "twins-tab")
        self.assertIn("active", classification_tab)
        self.assertIn('aria-selected="true"', classification_tab)
        self.assertNotIn("active", twins_tab)
        self.assertIn('aria-selected="false"', twins_tab)

        classification_pane = self._tag_with_id(html, "classification-pane")
        twins_pane = self._tag_with_id(html, "twins-pane")
        self.assertIn("show active", classification_pane)
        self.assertNotIn("show active", twins_pane)

    def test_transformer_renders_only_the_classification_tab(self):
        """AE7: Transformer declares Classification only, so no twins tab."""
        html = self._render_visible("transformer")
        self.assertIn(self.CLASSIFICATION_TAB, html)
        self.assertNotIn(self.TWINS_TAB, html)
        self.assertNotIn(self.TWINS_PANE, html)
        self.assertNotIn("DASH Twins", html)
        self.assertIn("show active", self._tag_with_id(html, "classification-pane"))

    def test_website_final_models_render_only_the_classification_tab(self):
        for model in ("1dCNN_z", "1dCNN_noz", "latent_z", "latent_noz"):
            with self.subTest(model=model):
                html = self._render_visible(model)
                self.assertIn(self.CLASSIFICATION_TAB, html)
                self.assertNotIn(self.TWINS_TAB, html)
                self.assertNotIn(self.TWINS_PANE, html)

    def test_user_uploaded_selection_renders_only_the_classification_tab(self):
        """KTD10: an unresolvable selection falls back to Classification alone."""
        html = self._render_visible("user_uploaded")
        self.assertIn(self.CLASSIFICATION_TAB, html)
        self.assertNotIn(self.TWINS_TAB, html)
        self.assertNotIn(self.TWINS_PANE, html)
        self.assertIn("show active", self._tag_with_id(html, "classification-pane"))

    def test_classification_pane_content_still_renders(self):
        """R21: moving the pane under the loop must not drop its markup."""
        html = self._render_visible("dash")
        self.assertIn('id="id_smoothing"', html)
        self.assertIn("Input Parameters", html)


class ClassifyTemplateLiteralGuardTests(SimpleTestCase):
    """R20/R22: no per-model conditional survives in the classify template."""

    TEMPLATE = (
        Path(__file__).resolve().parent.parent
        / "templates"
        / "astrodash"
        / "classify.html"
    )

    # A conditional comparison against a quoted built-in model id, in either
    # order, mirroring ``test_no_model_type_literals.py``'s guard.
    PATTERN = re.compile(
        r"""(?:==|!=)\s*(?P<q1>['"])(?:dash|transformer|user_uploaded|1dCNN_z|1dCNN_noz|latent_z|latent_noz)(?P=q1)"""
        r"""|(?P<q2>['"])(?:dash|transformer|user_uploaded|1dCNN_z|1dCNN_noz|latent_z|latent_noz)(?P=q2)\s*(?:==|!=)"""
    )

    def test_no_per_model_conditional_in_the_classification_template(self):
        offenders = [
            f"  classify.html:{lineno}: {line.strip()}"
            for lineno, line in enumerate(
                self.TEMPLATE.read_text(encoding="utf-8").splitlines(), start=1
            )
            if self.PATTERN.search(line)
        ]
        self.assertFalse(
            offenders,
            "Found per-model conditionals in the classification template. "
            "Result surfaces render from the selected model's declared "
            "surface list, so no tab, pane, or control may be keyed on a "
            "model id:\n" + "\n".join(offenders),
        )


class TwinsSupportingRouteGuardTests(TestCase):
    """R19/AE6: an undeclared surface's routes are unreachable, not just hidden.

    KTD5 splits the authority by route. ``twins_search`` reads the embedding a
    classification stashed, so it authorizes from the *classified* model. The
    twins page and its data route serve a model-agnostic payload with no
    session dependency, so they authorize from the *selected* model -- which is
    what keeps them reachable before any classification has run, exactly as
    today.
    """

    EMBEDDING = [0.0] * 1024

    def _session(self, selected=None, classified=None, embedding=False):
        """Seed the session with a selection, a classification, or both."""
        session = self.client.session
        if selected is not None:
            session["selected_model_type"] = selected
        if classified is not None:
            session["classify_model_type"] = classified
        if embedding:
            session["classify_dash_embedding"] = list(self.EMBEDDING)
        session.save()

    @staticmethod
    def _payload_config(tmpdir):
        """Write a twins payload under a temp data dir and return a config."""
        explorer = Path(tmpdir) / "explorer"
        explorer.mkdir(parents=True, exist_ok=True)
        (explorer / "dash_twins_payload.json").write_text(json.dumps({"points": []}))
        return SimpleNamespace(data_dir=str(tmpdir))

    @staticmethod
    def _drain(response):
        """Consume a streaming response without closing this test's connection.

        The test client wires ``response.close`` into the streaming iterator,
        and that close fires ``request_finished`` -> ``close_old_connections``,
        which would tear down the connection the test's transaction runs in and
        break every test after it. Django disconnects that receiver around
        ``close()`` for non-streaming responses; do the same here.

        Args:
            response: The streaming response to consume.

        Returns:
            bytes: The response body.
        """
        request_finished.disconnect(close_old_connections)
        try:
            return b"".join(response.streaming_content)
        finally:
            request_finished.connect(close_old_connections)

    # --- twins_search: authorized from the CLASSIFIED model ---

    def test_twins_search_refused_after_a_transformer_classification(self):
        """AE6: refused even though a stale embedding sits in the session."""
        self._session(selected="transformer", classified="transformer", embedding=True)
        svc = MagicMock()
        with patch("astrodash.ui_views.get_twins_search_service", return_value=svc):
            resp = self.client.get(reverse("astrodash:twins_search"))
        self.assertEqual(resp.status_code, 403)
        svc.find_twins.assert_not_called()

    def test_twins_search_served_after_a_dash_classification(self):
        self._session(selected="dash", classified="dash", embedding=True)
        svc = MagicMock(
            find_twins=MagicMock(
                return_value={
                    "query_umap": [0.0, 0.0],
                    "twin_indices": [1],
                    "twin_similarities": [0.9],
                }
            )
        )
        with patch("astrodash.ui_views.get_twins_search_service", return_value=svc):
            resp = self.client.get(reverse("astrodash:twins_search"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["twin_indices"], [1])

    # --- twins page and data: authorized from the SELECTED model ---

    def test_dash_selection_reaches_both_twins_routes_before_any_classification(self):
        """R21: the pre-classification twins pane keeps working exactly as today."""
        self._session(selected="dash")
        page = self.client.get(reverse("astrodash:dash_twins"))
        self.assertEqual(page.status_code, 200)

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "astrodash.ui_views.get_config",
                return_value=self._payload_config(tmpdir),
            ):
                data = self.client.get(reverse("astrodash:dash_twins_data"))
                self.assertEqual(data.status_code, 200)
                self.assertEqual(json.loads(self._drain(data)), {"points": []})

    def test_transformer_selection_is_refused_both_twins_routes(self):
        self._session(selected="transformer")
        self.assertEqual(
            self.client.get(reverse("astrodash:dash_twins")).status_code, 403
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "astrodash.ui_views.get_config",
                return_value=self._payload_config(tmpdir),
            ):
                resp = self.client.get(reverse("astrodash:dash_twins_data"))
        self.assertEqual(resp.status_code, 403)

    def test_twins_page_is_not_gated_on_the_classified_model(self):
        """KTD5: a DASH selection whose last run was Transformer still browses.

        The page carries no classification artifact, so the classified model
        must not decide it -- only the selection does.
        """
        self._session(selected="dash", classified="transformer")
        self.assertEqual(
            self.client.get(reverse("astrodash:dash_twins")).status_code, 200
        )
