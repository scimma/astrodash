"""Leaderboard taxonomy, metrics, page assembly, and view."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from astrodash.domain.models.spectrum import Spectrum
from astrodash.infrastructure.ml.leaderboard.dataset import (
    ChallengeSpectrum,
    drop_duplicate_spectra,
    load_challenge,
)
from astrodash.infrastructure.ml.leaderboard.evaluate import (
    _probability_vector,
    score_definition,
)
from astrodash.infrastructure.ml.leaderboard.metrics import score_predictions
from astrodash.infrastructure.ml.leaderboard.page import build_leaderboard_context
from astrodash.infrastructure.ml.leaderboard.store import (
    available_months,
    load_snapshot,
    write_scores,
)
from astrodash.infrastructure.ml.leaderboard.taxonomy import canonicalize
from astrodash.infrastructure.ml.model_registry import (
    REDSHIFT_INPUT_NONE,
    REDSHIFT_INPUT_REQUIRED,
    listed_definitions,
)


class TaxonomyTests(SimpleTestCase):
    def test_wiserep_and_model_aliases_map_to_five_classes(self):
        cases = {
            "SN Ia": "SN Ia",
            "Ia-91T": "SN Ia",
            "SN Iax": "SN Ia",
            "Ib": "SN Ib/c",
            "SN Ic-BL": "SN Ib/c",
            "Ib/c": "SN Ib/c",
            "SN II": "SN II",
            "IIb": "SN II",
            "SN IIn": "SN IIn",
            "IIn": "SN IIn",
            "SLSN-I": "SLSN-I",
            "SLSNe-I": "SLSN-I",
            "SLSN-II": None,
            "Unknown": None,
        }
        for raw, expected in cases.items():
            self.assertEqual(canonicalize(raw), expected, raw)


class MetricsTests(SimpleTestCase):
    def test_perfect_multiclass_scores(self):
        y_true = ["SN Ia", "SN II", "SN Ib/c", "SN IIn", "SLSN-I"]
        y_pred = list(y_true)
        y_proba = [
            [1, 0, 0, 0, 0],
            [0, 0, 1, 0, 0],
            [0, 1, 0, 0, 0],
            [0, 0, 0, 1, 0],
            [0, 0, 0, 0, 1],
        ]
        scores = score_predictions(y_true, y_pred, y_proba)
        self.assertEqual(scores["n_scored"], 5)
        self.assertEqual(scores["accuracy"], 100.0)
        self.assertEqual(scores["precision"], 1.0)
        self.assertEqual(scores["recall"], 1.0)
        self.assertEqual(scores["roc"], 1.0)


class ScoreDefinitionTests(SimpleTestCase):
    def test_redshift_required_skips_missing_z(self):
        definition = SimpleNamespace(
            id="transformer",
            redshift_input=REDSHIFT_INPUT_REQUIRED,
        )
        rows = [
            SimpleNamespace(
                redshift=None,
                canonical_type="SN Ia",
                spectrum=SimpleNamespace(),
            ),
            SimpleNamespace(
                redshift=0.01,
                canonical_type="SN Ia",
                spectrum=SimpleNamespace(),
            ),
        ]
        classifier = SimpleNamespace(
            classify_sync=lambda spectrum: {
                "best_match": {"type": "Ia"},
                "class_probabilities": {
                    "Ia": 0.9,
                    "Ib/c": 0.1,
                    "II": 0.0,
                    "IIn": 0.0,
                    "SLSNe-I": 0.0,
                },
            }
        )
        result = score_definition(definition, rows, classifier)
        self.assertEqual(result["n_scored"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["accuracy"], 100.0)

    def test_noz_scores_spectra_without_redshift(self):
        definition = SimpleNamespace(
            id="1dCNN_noz",
            redshift_input=REDSHIFT_INPUT_NONE,
        )
        rows = [
            SimpleNamespace(
                redshift=None,
                canonical_type="SN II",
                spectrum=SimpleNamespace(),
            ),
        ]
        classifier = SimpleNamespace(
            classify_sync=lambda spectrum: {
                "best_match": {"type": "SN II"},
                "class_probabilities": {
                    "SN Ia": 0.0,
                    "SN Ib/c": 0.0,
                    "SN II": 1.0,
                    "SN IIn": 0.0,
                    "SLSN-I": 0.0,
                },
            }
        )
        result = score_definition(definition, rows, classifier)
        self.assertEqual(result["n_scored"], 1)
        self.assertEqual(result["skipped"], 0)

    def test_classify_exception_skips_spectrum(self):
        definition = SimpleNamespace(
            id="dash",
            redshift_input=REDSHIFT_INPUT_NONE,
        )
        rows = [
            SimpleNamespace(
                redshift=0.01,
                filename="out_of_range.txt",
                canonical_type="SN Ia",
                spectrum=SimpleNamespace(),
            ),
            SimpleNamespace(
                redshift=0.01,
                filename="ok.txt",
                canonical_type="SN Ia",
                spectrum=SimpleNamespace(),
            ),
        ]

        def classify_sync(spectrum):
            if not getattr(classify_sync, "seen", False):
                classify_sync.seen = True
                raise ValueError("Spectrum out of wavelength range")
            return {
                "best_match": {"type": "Ia"},
                "class_probabilities": {
                    "Ia": 1.0,
                    "Ib/c": 0.0,
                    "II": 0.0,
                    "IIn": 0.0,
                    "SLSNe-I": 0.0,
                },
            }

        result = score_definition(
            definition, rows, SimpleNamespace(classify_sync=classify_sync)
        )
        self.assertEqual(result["n_scored"], 1)
        self.assertEqual(result["skipped"], 1)


def _row(iau: str, filename: str, wave, flux) -> ChallengeSpectrum:
    return ChallengeSpectrum(
        iau=iau,
        filename=filename,
        wiserep_type="SN Ia",
        canonical_type="SN Ia",
        redshift=0.01,
        spectrum=Spectrum(x=list(wave), y=list(flux), file_name=filename),
    )


class ProbabilityVectorTests(SimpleTestCase):
    """One unusable spectrum must not cost a model its ROC for the month.

    ``roc_auc_score`` rejects the whole array if any element is NaN, so a
    single bad probability vector silently turned a month's ROC into null.
    The transformer produced exactly one such vector on the June 2026
    challenge (2026obc_91563.txt) out of 223, which is why it alone showed no
    ROC while every other model did.
    """

    def _result(self, probs):
        return {
            "best_match": {"type": "SN Ia"},
            "class_probabilities": probs,
        }

    def test_finite_distribution_is_kept(self):
        vector = _probability_vector(
            self._result({"SN Ia": 0.7, "SN II": 0.2, "SN IIn": 0.1})
        )
        self.assertIsNotNone(vector)
        self.assertAlmostEqual(sum(vector), 1.0)

    def test_nan_probability_is_dropped(self):
        self.assertIsNone(
            _probability_vector(self._result({"SN Ia": float("nan"), "SN II": 0.5}))
        )

    def test_infinite_probability_is_dropped(self):
        self.assertIsNone(
            _probability_vector(self._result({"SN Ia": float("inf"), "SN II": 0.5}))
        )

    def test_all_zero_distribution_is_dropped(self):
        """A zero vector carries no ranking information."""
        self.assertIsNone(_probability_vector(self._result({"SN Ia": 0.0})))

    def test_unmapped_labels_are_dropped(self):
        """Nothing canonicalizes, so the summed vector is all zeros."""
        self.assertIsNone(_probability_vector(self._result({"not-a-class": 1.0})))

    def test_missing_probabilities_fall_back_to_the_prediction(self):
        vector = _probability_vector({"best_match": {"type": "SN Ia"}})
        self.assertEqual(sum(vector), 1.0)

    def test_one_bad_vector_does_not_null_the_whole_roc(self):
        """The regression itself: good rows still yield a ROC."""
        good = [
            ([0.9, 0.05, 0.03, 0.01, 0.01], "SN Ia"),
            ([0.05, 0.9, 0.03, 0.01, 0.01], "SN Ib/c"),
            ([0.05, 0.03, 0.9, 0.01, 0.01], "SN II"),
            ([0.05, 0.03, 0.01, 0.9, 0.01], "SN IIn"),
            ([0.05, 0.03, 0.01, 0.01, 0.9], "SLSN-I"),
        ]
        y_proba = [v for v, _ in good]
        y_true = [t for _, t in good]
        scores = score_predictions(y_true, y_true, y_proba)
        self.assertIsNotNone(scores["roc"])


class DatasetDedupTests(SimpleTestCase):
    def test_keeps_one_spectrum_per_iau(self):
        rows = [
            _row("2026gzf", "2026gzf_b.txt", [1.0, 2.0], [0.1, 0.2]),
            _row("2026gzf", "2026gzf_a.txt", [1.0, 3.0], [0.1, 0.4]),
            _row("2026nhm", "2026nhm_1.txt", [1.0, 4.0], [0.2, 0.3]),
        ]
        kept = drop_duplicate_spectra(rows)
        self.assertEqual([row.filename for row in kept], ["2026gzf_a.txt", "2026nhm_1.txt"])

    def test_drops_identical_flux_even_for_different_iau(self):
        wave, flux = [3500.0, 3600.0], [1.0, 2.0]
        rows = [
            _row("2026aaa", "aaa.txt", wave, flux),
            _row("2026bbb", "bbb.txt", wave, flux),
        ]
        kept = drop_duplicate_spectra(rows)
        self.assertEqual([row.iau for row in kept], ["2026aaa"])

    def test_load_challenge_dedupes_metadata_rows(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spectra = root / "spectra"
            spectra.mkdir()
            (spectra / "a.txt").write_text("1 1\n2 2\n")
            (spectra / "b.txt").write_text("1 3\n2 4\n")
            (root / "metadata.csv").write_text(
                "iau,filename,type,redshift\n"
                "2026gzf,a.txt,SN Ia,0.01\n"
                "2026gzf,b.txt,SN Ia,0.01\n"
            )
            loaded = load_challenge(root)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].filename, "a.txt")


class AvailableMonthsRobustnessTests(SimpleTestCase):
    """A stray directory on the shared data mount must not break the page.

    available_months() treated any directory holding a metadata.csv as a month
    and handed the name to month_label(), so an operator's scratch directory
    (e.g. "2026-06.his-upload") raised ValueError and 500'd /leaderboard.
    """

    def _months_with(self, *dirnames):
        with TemporaryDirectory() as tmp:
            for name in dirnames:
                d = Path(tmp) / name
                d.mkdir()
                (d / "metadata.csv").write_text("iau,filename,type,redshift\n")
            with patch(
                "astrodash.infrastructure.ml.leaderboard.store.challenge_data_root",
                return_value=Path(tmp),
            ), patch(
                "astrodash.infrastructure.ml.leaderboard.store.SCORES_DIR",
                Path(tmp) / "no-scores",
            ):
                return available_months()

    def test_month_directories_are_found(self):
        self.assertEqual(self._months_with("2026-06", "2026-07"), ["2026-07", "2026-06"])

    def test_non_month_directories_are_ignored(self):
        self.assertEqual(
            self._months_with("2026-06", "2026-06.his-upload", "scratch", "archive"),
            ["2026-06"],
        )

    def test_page_renders_with_a_stray_directory_present(self):
        with TemporaryDirectory() as tmp:
            for name in ("2026-07", "2026-07.backup"):
                d = Path(tmp) / name
                d.mkdir()
                (d / "metadata.csv").write_text("iau,filename,type,redshift\n")
            with patch(
                "astrodash.infrastructure.ml.leaderboard.store.challenge_data_root",
                return_value=Path(tmp),
            ), patch(
                "astrodash.infrastructure.ml.leaderboard.store.SCORES_DIR",
                Path(tmp) / "no-scores",
            ):
                context = build_leaderboard_context()
        self.assertEqual(context["selected_month"], "2026-07")


class SnapshotProvenanceTests(SimpleTestCase):
    """Standings carry the date their dataset was collected.

    The dataset a month is scored on is reproducible -- re-running the scrape
    returns the same metadata.csv -- but nothing in a score file said which
    collection it came from. The sidecar makes a dataset self-describing and
    the date reaches the page, so a reader can tell two score sets apart.
    """

    def test_load_snapshot_reads_the_sidecar(self):
        with TemporaryDirectory() as tmp:
            month_dir = Path(tmp) / "2026-07"
            month_dir.mkdir()
            (month_dir / "snapshot.json").write_text(
                '{"scraped_at": "2026-09-25T12:00:00+00:00", "spectra": 450}'
            )
            with patch(
                "astrodash.infrastructure.ml.leaderboard.store.challenge_data_root",
                return_value=Path(tmp),
            ):
                snap = load_snapshot("2026-07")
        self.assertEqual(snap["scraped_at"], "2026-09-25T12:00:00+00:00")

    def test_missing_snapshot_is_not_an_error(self):
        with TemporaryDirectory() as tmp:
            with patch(
                "astrodash.infrastructure.ml.leaderboard.store.challenge_data_root",
                return_value=Path(tmp),
            ):
                self.assertIsNone(load_snapshot("2026-07"))

    def test_corrupt_snapshot_is_not_an_error(self):
        with TemporaryDirectory() as tmp:
            month_dir = Path(tmp) / "2026-07"
            month_dir.mkdir()
            (month_dir / "snapshot.json").write_text("{ not json")
            with patch(
                "astrodash.infrastructure.ml.leaderboard.store.challenge_data_root",
                return_value=Path(tmp),
            ):
                self.assertIsNone(load_snapshot("2026-07"))

    def test_page_surfaces_the_scrape_date_from_the_score_file(self):
        with TemporaryDirectory() as tmp:
            scores_dir = Path(tmp)
            payload = {
                "month": "2026-07",
                "models": [],
                "status": "Scored",
                "scraped_at": "2026-09-25T12:00:00+00:00",
            }
            (scores_dir / "2026-07.json").write_text(json.dumps(payload))
            with patch(
                "astrodash.infrastructure.ml.leaderboard.store.SCORES_DIR", scores_dir
            ):
                context = build_leaderboard_context("2026-07")
        self.assertEqual(
            context["challenge"]["scraped_at"], "2026-09-25T12:00:00+00:00"
        )


class LeaderboardPageTests(SimpleTestCase):
    def test_lists_all_listed_models_when_unscored(self):
        with patch(
            "astrodash.infrastructure.ml.leaderboard.page.available_months",
            return_value=[],
        ), patch(
            "astrodash.infrastructure.ml.leaderboard.page.load_scores",
            return_value=None,
        ):
            context = build_leaderboard_context()
        titles = [row["model"] for row in context["rankings"]]
        self.assertEqual(
            titles, [definition.title for definition in listed_definitions()]
        )
        self.assertTrue(context["is_mock"])
        self.assertIsNone(context["rankings"][0]["rank"])

    def test_ranks_by_accuracy_then_roc(self):
        payload = {
            "month": "2026-07",
            "month_label": "July 2026",
            "status": "Finalized",
            "eval_window": "Jul 1 – Jul 31, 2026",
            "spectra_count": 3,
            "models": [
                {"id": "dash", "accuracy": 80.0, "roc": 0.7, "precision": 0.5, "recall": 0.5},
                {"id": "transformer", "accuracy": 80.0, "roc": 0.9, "precision": 0.8, "recall": 0.8},
                {"id": "1dCNN_z", "accuracy": 60.0, "roc": 0.95, "precision": 0.6, "recall": 0.6},
                {"id": "1dCNN_noz", "accuracy": 40.0, "roc": 0.6, "precision": 0.4, "recall": 0.4},
                {"id": "latent_z", "accuracy": 30.0, "roc": 0.5, "precision": 0.3, "recall": 0.3},
                {"id": "latent_noz", "accuracy": 20.0, "roc": 0.4, "precision": 0.2, "recall": 0.2},
            ],
        }
        with patch(
            "astrodash.infrastructure.ml.leaderboard.page.available_months",
            return_value=["2026-07"],
        ), patch(
            "astrodash.infrastructure.ml.leaderboard.page.load_scores",
            return_value=payload,
        ):
            context = build_leaderboard_context("2026-07")
        self.assertFalse(context["is_mock"])
        self.assertEqual(context["rankings"][0]["id"], "transformer")
        self.assertEqual(context["rankings"][1]["id"], "dash")
        self.assertEqual(context["rankings"][0]["rank"], 1)
        self.assertEqual(context["rankings"][0]["model"], "Transformer Model")
        self.assertEqual(len(context["rankings"]), len(listed_definitions()))
        self.assertEqual(context["challenge"]["spectra_count"], 3)

    def test_view_renders_listed_model_titles(self):
        response = self.client.get(reverse("astrodash:leaderboard"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        for definition in listed_definitions():
            self.assertIn(definition.title, body)


class WriteScoresTests(SimpleTestCase):
    def test_write_scores_round_trip(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch(
                "astrodash.infrastructure.ml.leaderboard.store.SCORES_DIR", tmp_path
            ):
                path = write_scores(
                    {
                        "month": "2026-07",
                        "models": [{"id": "dash", "micro_f1": 0.5}],
                    }
                )
                self.assertTrue(path.is_file())
                self.assertEqual(path.name, "2026-07.json")
