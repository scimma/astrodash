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


class SpectralTwinsPageTests(TestCase):
    """The explorer page is public and adapts to the installed bundle."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bundle_dir = Path(tmp.name)
        patcher = patch(
            "astrodash.ui_views.get_config",
            return_value=SimpleNamespace(daep_twins_bundle_dir=str(self.bundle_dir)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.url = reverse("astrodash:spectral_twins")

    def _install_descriptor(self, **overrides):
        descriptor = {
            "schema": daep_bundle.BUNDLE_SCHEMA,
            "bundle_id": "abc123",
            "N": 3,
            "K": 2,
            "W": 4,
            "files": {"data_meta.json": 100, "data_spectra.bin": 108},
        }
        descriptor.update(overrides)
        (self.bundle_dir / "bundle.json").write_text(json.dumps(descriptor))

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
        (self.bundle_dir / "bundle.json").write_text(
            json.dumps({"schema": daep_bundle.BUNDLE_SCHEMA})
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "format")
