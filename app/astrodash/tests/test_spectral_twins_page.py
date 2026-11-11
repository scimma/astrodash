"""Tests for the public Spectral Twins (DAEP) explorer page."""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from astrodash.infrastructure.explorer import daep_bundle
from astrodash.middleware import TWINS_BUNDLE_URL_PREFIX


class TwinsPageTestCase(TestCase):
    """Points the explorer at empty temporary bundle directories."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.bundle_dir = root / "daep"
        self.observer_dir = root / "daep_observer_frame"
        self.multi_dir = root / "daep_multi_object"
        self.literature_dir = root / "daep_literature"
        patcher = patch(
            "astrodash.ui_views.get_config",
            return_value=SimpleNamespace(
                daep_twins_bundle_dir=str(self.bundle_dir),
                daep_twins_observer_frame_dir=str(self.observer_dir),
                daep_twins_multi_object_dir=str(self.multi_dir),
                daep_twins_literature_dir=str(self.literature_dir),
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.url = reverse("astrodash:spectral_twins")

    def _install_descriptor(self, bundle_dir=None, data_files=True, **overrides):
        """Install a bundle descriptor and, by default, the two data files."""
        bundle_dir = bundle_dir or self.bundle_dir
        bundle_dir.mkdir(exist_ok=True)
        descriptor = {
            "schema": daep_bundle.BUNDLE_SCHEMA,
            "bundle_id": "abc123",
            "N": 3,
            "K": 2,
            "W": 4,
            "files": {"data_meta.json": 100, "data_spectra.bin": 108},
        }
        descriptor.update(overrides)
        (bundle_dir / "bundle.json").write_text(json.dumps(descriptor))
        if data_files:
            (bundle_dir / "data_meta.json").write_text("{}")
            (bundle_dir / "data_spectra.bin").write_bytes(b"\0" * 108)


class SpectralTwinsPageTests(TwinsPageTestCase):
    """The explorer page is public and adapts to the installed bundle."""

    def test_anonymous_visitor_gets_the_page(self):
        self._install_descriptor()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Spectral Twins (DAEP)")

    def test_page_points_at_the_served_bundle_with_its_cache_key(self):
        self._install_descriptor()
        response = self.client.get(self.url)
        self.assertContains(response, TWINS_BUNDLE_URL_PREFIX + "data_meta.json")
        self.assertContains(response, TWINS_BUNDLE_URL_PREFIX + "data_spectra.bin")
        self.assertContains(response, "abc123")

    def test_navigation_links_to_the_explorer(self):
        response = self.client.get(reverse("astrodash:landing_page"))
        self.assertContains(response, f'href="{self.url}"')
        self.assertContains(response, "Spectral Twins")

    def test_missing_bundle_renders_the_not_installed_state(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not installed")
        self.assertNotContains(response, TWINS_BUNDLE_URL_PREFIX + "data_meta.json")

    def test_unknown_bundle_schema_renders_the_format_error(self):
        self._install_descriptor(schema=daep_bundle.BUNDLE_SCHEMA + 1)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "format")
        self.assertNotContains(response, TWINS_BUNDLE_URL_PREFIX + "data_meta.json")

    def test_incomplete_descriptor_renders_the_format_error(self):
        self._install_descriptor()
        (self.bundle_dir / "bundle.json").write_text(
            json.dumps({"schema": daep_bundle.BUNDLE_SCHEMA})
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "format")


class SpectralTwinsDatasetTests(TwinsPageTestCase):
    """The page offers each installed dataset and loads the selected one."""

    def _install_all(self):
        self._install_descriptor(self.bundle_dir, bundle_id="rest")
        self._install_descriptor(self.observer_dir, bundle_id="observer")
        self._install_descriptor(self.multi_dir, bundle_id="multi")

    def test_only_the_original_bundle_loads_at_the_bare_prefix(self):
        self._install_descriptor(bundle_id="rest")
        response = self.client.get(self.url)
        self.assertContains(
            response, '"metaUrl": "/astrodash/twins-data/data_meta.json"'
        )
        self.assertContains(response, '"bundleId": "rest"')

    def test_each_dataset_loads_from_its_own_prefix(self):
        self._install_all()
        for dataset, bundle_id in (
            ("observer_frame", "observer"),
            ("multi_object_rest", "multi"),
        ):
            with self.subTest(dataset=dataset):
                response = self.client.get(self.url, {"dataset": dataset})
                self.assertContains(
                    response,
                    f'"metaUrl": "{TWINS_BUNDLE_URL_PREFIX}{dataset}/data_meta.json"',
                )
                self.assertContains(
                    response,
                    f'"spectraUrl": "{TWINS_BUNDLE_URL_PREFIX}{dataset}/data_spectra.bin"',
                )
                self.assertContains(response, f'"bundleId": "{bundle_id}"')

    def test_unknown_dataset_falls_back_to_the_rest_frame(self):
        self._install_all()
        response = self.client.get(self.url, {"dataset": "bogus"})
        self.assertContains(response, '"bundleId": "rest"')
        self.assertContains(response, '<option value="rest_frame" selected')

    def test_dataset_missing_a_data_file_is_offered_disabled(self):
        self._install_all()
        (self.observer_dir / "data_spectra.bin").unlink()
        response = self.client.get(self.url)
        self.assertContains(response, '"bundleId": "rest"')
        self.assertContains(response, "Observer-Frame DAEP (data missing)")
        self.assertNotContains(response, "Multi-Object DAEP (data missing)")

    def test_requesting_an_uninstalled_dataset_renders_the_missing_state(self):
        self._install_descriptor(bundle_id="rest")
        response = self.client.get(self.url, {"dataset": "observer_frame"})
        self.assertContains(response, "not installed")
        self.assertNotContains(response, "data_meta.json")

    def test_dataset_with_a_rejected_descriptor_shows_the_format_warning(self):
        self._install_all()
        self._install_descriptor(
            self.observer_dir, schema=daep_bundle.BUNDLE_SCHEMA + 1
        )
        response = self.client.get(self.url, {"dataset": "observer_frame"})
        self.assertContains(response, "format this page does not recognize")
        self.assertNotContains(response, "observer_frame/data_meta.json")

    def test_catalog_url_is_given_only_when_the_catalog_is_installed(self):
        self._install_descriptor()
        catalog_url = (
            TWINS_BUNDLE_URL_PREFIX
            + daep_bundle.LITERATURE_URL_SUFFIX
            + daep_bundle.LITERATURE_FILENAME
        )
        response = self.client.get(self.url)
        self.assertNotContains(response, catalog_url)
        self.literature_dir.mkdir()
        (self.literature_dir / daep_bundle.LITERATURE_FILENAME).write_text("{}")
        response = self.client.get(self.url)
        self.assertContains(response, f'"literatureUrl": "{catalog_url}"')

    def test_page_html_stays_small_and_leaves_the_catalog_out(self):
        self._install_all()
        for dataset in ("rest_frame", "observer_frame", "multi_object_rest"):
            with self.subTest(dataset=dataset):
                response = self.client.get(self.url, {"dataset": dataset})
                self.assertEqual(response.status_code, 200)
                self.assertLess(len(response.content), 100_000)
                self.assertNotContains(response, "literature-phase-catalog")
