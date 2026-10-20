"""Tests for preparing a DAEP spectral twins bundle for publication.

The fixture is a three-spectrum bundle whose latents are chosen by hand so the
mean-over-tokens cosine distances are known without running the code under
test: rows 0 and 1 are 0.2 apart, rows 1 and 2 are 0.6 apart, and rows 0 and 2
are 1.0 apart.
"""

import gzip
import io
import hashlib
import json
import tempfile
from pathlib import Path

import numpy as np
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from astrodash.infrastructure.explorer import daep_bundle

N, K, W = 3, 2, 4

# Two tokens of two dimensions each. Row 0's first token is deliberately not
# unit length, so the preparation must normalize per token.
LATENTS = np.array(
    [
        [[3.0, 0.0], [1.0, 0.0]],
        [[1.0, 0.0], [0.6, 0.8]],
        [[0.0, 1.0], [0.0, 1.0]],
    ],
    dtype=np.float32,
)
NBRS = [[0, 1], [1, 0], [2, 1]]
DISTS = [[0.0, 0.2], [0.0, 0.2], [0.0, 0.6]]


def _meta(**overrides):
    meta = {
        "N": N,
        "K": K,
        "W": W,
        "k_show_default": 2,
        "exclude_same_sn_default": True,
        "Y": [[0.0, 0.0], [1.0, 0.5], [2.0, 1.0]],
        "hover": ["a", "b", "c"],
        "sn": ["SN 1", "SN 1", "SN 2"],
        "spec_id": ["10", "11", "12"],
        "obj_type": ["SN Ia", "SN Ia", "SN II"],
        "type_col_used": "raw_type",
        "raw_type": ["SN Ia", "SN Ia", "SN II"],
        "canonical_type": ["SNIa", "SNIa", "SNII"],
        "broad_type": ["SNIa", "SNIa", "SNII"],
        "source_dataset": ["WISeREP"] * 3,
        "instrument": ["Other"] * 3,
        "phase": [None] * 3,
        "jd": [1.0, 2.0, 3.0],
        "jd_str": ["a", "b", "c"],
        "z": [0.01, 0.01, 0.02],
        "obs_date": ["d1", "d2", "d3"],
        "wave": [3202.5, 3212.5, 3222.5, 3232.5],
        "nbrs": NBRS,
        "dists": DISTS,
    }
    meta.update(overrides)
    return meta


class PrepareTwinsBundleTests(SimpleTestCase):
    """End-to-end behavior of the ``prepare_twins_bundle`` command."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src = Path(self._tmp.name) / "src"
        self.out = Path(self._tmp.name) / "out"
        self.src.mkdir()

    def _write_source(self, meta=None, spectra_bytes=None, latents=LATENTS, order=None):
        meta = _meta() if meta is None else meta
        (self.src / "data_meta.json").write_text(json.dumps(meta))
        if spectra_bytes is None:
            spectra_bytes = N * daep_bundle.BIN_BYTES_PER_WAVE * W
        (self.src / "data_spectra.bin").write_bytes(b"\x01" * spectra_bytes)
        order = np.arange(N) if order is None else np.asarray(order)
        # Latent row r belongs to metadata row order[r].
        z = np.empty_like(latents)
        z[np.arange(N)] = latents[order]
        np.savez(
            self.src / "latent_raw_z_best.npz",
            z=z,
            ds_idx=np.arange(N),
            orig_meta_idx=order,
        )

    def _run(self):
        call_command(
            "prepare_twins_bundle",
            str(self.src),
            "--out",
            str(self.out),
            stdout=io.StringIO(),
        )

    def test_descriptor_records_geometry_sizes_and_bundle_id(self):
        self._write_source()
        self._run()
        descriptor = json.loads((self.out / "daep" / "bundle.json").read_text())
        meta_bytes = (self.src / "data_meta.json").read_bytes()
        self.assertEqual(descriptor["schema"], daep_bundle.BUNDLE_SCHEMA)
        self.assertEqual((descriptor["N"], descriptor["K"], descriptor["W"]), (N, K, W))
        self.assertEqual(
            descriptor["bundle_id"], hashlib.sha256(meta_bytes).hexdigest()
        )
        self.assertEqual(descriptor["files"]["data_meta.json"], len(meta_bytes))
        self.assertEqual(descriptor["files"]["data_spectra.bin"], N * 9 * W)

    def test_public_files_are_copied_unchanged_with_a_gzip_sibling(self):
        self._write_source()
        self._run()
        public = self.out / "daep"
        meta_bytes = (self.src / "data_meta.json").read_bytes()
        self.assertEqual((public / "data_meta.json").read_bytes(), meta_bytes)
        self.assertEqual(
            gzip.decompress((public / "data_meta.json.gz").read_bytes()), meta_bytes
        )
        self.assertEqual(
            (public / "data_spectra.bin").read_bytes(),
            (self.src / "data_spectra.bin").read_bytes(),
        )

    def test_spectra_file_one_byte_short_is_rejected(self):
        self._write_source(spectra_bytes=N * 9 * W - 1)
        with self.assertRaisesRegex(CommandError, f"{N * 9 * W}.*{N * 9 * W - 1}"):
            self._run()

    def test_metadata_missing_nbrs_is_rejected(self):
        meta = _meta()
        del meta["nbrs"]
        self._write_source(meta=meta)
        with self.assertRaisesRegex(CommandError, "nbrs"):
            self._run()

    def test_permuted_latents_are_reordered_to_metadata_rows(self):
        self._write_source(order=[2, 0, 1])
        self._run()
        latents = np.load(self.out / "daep_server" / "reference_latents.npy")
        self.assertEqual(latents.shape, (N, 4))
        # Metadata row 2 is the [0,1],[0,1] latent.
        np.testing.assert_allclose(latents[2], [0.0, 1.0, 0.0, 1.0], atol=1e-6)

    def test_distances_that_disagree_with_the_latents_abort(self):
        self._write_source(meta=_meta(dists=[[0.0, 0.2], [0.0, 0.2], [0.0, 0.9]]))
        with self.assertRaisesRegex(CommandError, "row 2"):
            self._run()

    def test_latent_rows_are_unit_norm_per_token(self):
        self._write_source()
        self._run()
        latents = np.load(self.out / "daep_server" / "reference_latents.npy")
        norms = np.linalg.norm(latents.reshape(N, 2, 2), axis=2)
        np.testing.assert_allclose(norms, np.ones((N, 2)), atol=1e-6)

    def test_index_rows_match_metadata_and_descriptor(self):
        self._write_source()
        self._run()
        descriptor = json.loads((self.out / "daep" / "bundle.json").read_text())
        index = np.load(self.out / "daep_server" / "reference_index.npz")
        np.testing.assert_allclose(index["Y"], _meta()["Y"])
        self.assertEqual(index["spec_id"].tolist(), ["10", "11", "12"])
        self.assertEqual(index["sn"].tolist(), ["SN 1", "SN 1", "SN 2"])
        self.assertEqual(str(index["bundle_id"]), descriptor["bundle_id"])

    def test_no_server_only_file_lands_in_the_public_directory(self):
        self._write_source()
        self._run()
        public = sorted(p.name for p in (self.out / "daep").iterdir())
        self.assertEqual(
            public,
            ["bundle.json", "data_meta.json", "data_meta.json.gz", "data_spectra.bin"],
        )

    def test_encoder_hash_is_recorded_when_an_encoder_is_given(self):
        self._write_source()
        encoder = self.src / "encoder.pt"
        encoder.write_bytes(b"weights")
        call_command(
            "prepare_twins_bundle",
            str(self.src),
            "--out",
            str(self.out),
            "--encoder",
            str(encoder),
            stdout=io.StringIO(),
        )
        descriptor = json.loads((self.out / "daep" / "bundle.json").read_text())
        self.assertEqual(
            descriptor["encoder_sha256"], hashlib.sha256(b"weights").hexdigest()
        )

    def test_encoder_hash_is_null_without_an_encoder(self):
        self._write_source()
        self._run()
        descriptor = json.loads((self.out / "daep" / "bundle.json").read_text())
        self.assertIsNone(descriptor["encoder_sha256"])

    def test_latent_index_that_is_not_a_permutation_is_rejected(self):
        self._write_source()
        np.savez(
            self.src / "latent_raw_z_best.npz",
            z=LATENTS,
            ds_idx=np.arange(N),
            orig_meta_idx=np.array([0, 0, 1]),
        )
        with self.assertRaisesRegex(CommandError, "one-to-one"):
            self._run()

    def test_missing_latents_file_is_rejected(self):
        self._write_source()
        (self.src / "latent_raw_z_best.npz").unlink()
        with self.assertRaisesRegex(CommandError, "latent_raw_z_best.npz"):
            self._run()

    def test_latents_file_without_an_index_array_is_rejected(self):
        self._write_source()
        np.savez(self.src / "latent_raw_z_best.npz", z=LATENTS)
        with self.assertRaisesRegex(CommandError, "orig_meta_idx"):
            self._run()

    def test_metadata_list_of_the_wrong_length_is_rejected(self):
        self._write_source(meta=_meta(hover=["a", "b"]))
        with self.assertRaisesRegex(CommandError, "hover.*2 entries"):
            self._run()


class ReadDescriptorTests(SimpleTestCase):
    """The descriptor reader the explorer and twins service share."""

    def test_unknown_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            path.write_text(json.dumps({"schema": daep_bundle.BUNDLE_SCHEMA + 1}))
            with self.assertRaisesRegex(daep_bundle.BundleError, "schema"):
                daep_bundle.read_descriptor(path)

    def _write(self, tmp, value):
        path = Path(tmp) / "bundle.json"
        path.write_text(json.dumps(value))
        return path

    def test_descriptor_missing_required_fields_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {"schema": daep_bundle.BUNDLE_SCHEMA})
            with self.assertRaisesRegex(daep_bundle.BundleError, "bundle_id"):
                daep_bundle.read_descriptor(path)

    def test_descriptor_that_is_not_an_object_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, [daep_bundle.BUNDLE_SCHEMA])
            with self.assertRaisesRegex(daep_bundle.BundleError, "JSON object"):
                daep_bundle.read_descriptor(path)
