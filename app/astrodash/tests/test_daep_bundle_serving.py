"""Tests for serving the DAEP twins bundle with byte ranges and gzip.

The explorer fetches one spectrum at a time from ``data_spectra.bin`` with an
HTTP Range request and refuses any response that is not a 206, so these tests
pin the range behavior through the real middleware stack.
"""

import gzip
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, SimpleTestCase

from astrodash.middleware import TWINS_BUNDLE_URL_PREFIX

SPECTRA = bytes(range(256)) * 4  # 1024 bytes with distinct values
META = b'{"N": 1}' * 100


class TwinsBundleServingTests(SimpleTestCase):
    """The bundle directory is served under its prefix by WhiteNoise."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bundle_dir = Path(tmp.name) / "daep"
        self.bundle_dir.mkdir()
        (self.bundle_dir / "data_spectra.bin").write_bytes(SPECTRA)
        (self.bundle_dir / "data_meta.json").write_bytes(META)
        (self.bundle_dir / "data_meta.json.gz").write_bytes(gzip.compress(META))
        self.client = self._client_for(self.bundle_dir)

    def _client_for(self, bundle_dir):
        settings = SimpleNamespace(daep_twins_bundle_dir=str(bundle_dir))
        patcher = patch("astrodash.middleware.get_settings", return_value=settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        # A fresh Client builds a fresh middleware chain, which reads the setting.
        return Client()

    def _url(self, name):
        return TWINS_BUNDLE_URL_PREFIX + name

    def test_closed_range_returns_206_with_exact_bytes(self):
        response = self.client.get(
            self._url("data_spectra.bin"), HTTP_RANGE="bytes=0-35"
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Range"], f"bytes 0-35/{len(SPECTRA)}")
        self.assertEqual(b"".join(response.streaming_content), SPECTRA[:36])

    def test_open_ended_range_returns_the_remainder(self):
        response = self.client.get(
            self._url("data_spectra.bin"), HTTP_RANGE="bytes=36-"
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b"".join(response.streaming_content), SPECTRA[36:])

    def test_range_past_the_end_returns_416(self):
        response = self.client.get(
            self._url("data_spectra.bin"), HTTP_RANGE=f"bytes={len(SPECTRA) + 10}-"
        )
        self.assertEqual(response.status_code, 416)

    def test_metadata_is_served_gzipped_to_clients_that_accept_it(self):
        response = self.client.get(
            self._url("data_meta.json"), HTTP_ACCEPT_ENCODING="gzip"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Encoding"], "gzip")
        self.assertEqual(gzip.decompress(b"".join(response.streaming_content)), META)

    def test_missing_bundle_directory_leaves_the_prefix_404(self):
        client = self._client_for(self.bundle_dir.parent / "absent")
        response = client.get(self._url("data_spectra.bin"))
        self.assertEqual(response.status_code, 404)

    def test_server_only_latents_are_not_served(self):
        # Even if someone drops the latents into the public directory's sibling,
        # nothing outside the bundle directory is reachable under the prefix.
        (self.bundle_dir.parent / "reference_latents.npy").write_bytes(b"x")
        response = self.client.get(self._url("reference_latents.npy"))
        self.assertEqual(response.status_code, 404)
        response = self.client.get(self._url("../reference_latents.npy"))
        self.assertEqual(response.status_code, 404)
