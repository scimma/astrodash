"""Prepare Henna Abunemeh's DAEP explorer output for publication.

Run this on a fresh delivery before uploading it to the S3 data bucket. It
validates the delivery, then writes the browser-facing directory (the two data
files unchanged, a gzip copy of the metadata, and a descriptor) and the
server-only directory (reference latents and a compact row index for the
twins service). See docs/admin/updating-data-files.md.
"""

import gzip
import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from django.core.management.base import BaseCommand, CommandError

from astrodash.infrastructure.explorer import daep_bundle as bundle

logger = logging.getLogger(__name__)

LATENTS_SOURCE_FILENAME = "latent_raw_z_best.npz"


class Command(BaseCommand):
    """Validate a DAEP explorer delivery and write the publishable bundle."""

    help = (
        "Validate a DAEP latent explorer delivery and write the publishable bundle: "
        f"<out>/{bundle.PUBLIC_SUBDIR}/ for the browser and <out>/{bundle.SERVER_SUBDIR}/ for the server."
    )

    def add_arguments(self, parser: Any) -> None:
        """Declare the command's arguments.

        Args:
            parser: The argument parser supplied by Django.
        """
        parser.add_argument(
            "source",
            help=(
                f"Directory holding {bundle.META_FILENAME}, {bundle.SPECTRA_FILENAME}, "
                f"and {LATENTS_SOURCE_FILENAME}."
            ),
        )
        parser.add_argument(
            "--out", required=True, help="Directory to write the bundle into."
        )
        parser.add_argument(
            "--encoder",
            default=None,
            help="The DAEP encoder checkpoint the latents came from; its hash is recorded in the descriptor.",
        )
        parser.add_argument(
            "--check-rows",
            type=int,
            default=200,
            help="How many rows to check against the stored twin distances.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Validate the delivery and write the bundle.

        Args:
            *args: Unused positional arguments.
            **options: Parsed command options.

        Raises:
            CommandError: If any part of the delivery is missing or inconsistent.
        """
        source = Path(options["source"])
        out = Path(options["out"])
        try:
            self._prepare(source, out, options["encoder"], options["check_rows"])
        except (bundle.BundleError, FileNotFoundError) as exc:
            raise CommandError(str(exc)) from exc

    def _prepare(
        self, source: Path, out: Path, encoder: str | None, check_rows: int
    ) -> None:
        """Validate the delivery in ``source`` and write the bundle under ``out``.

        Args:
            source: Directory holding Henna's delivery.
            out: Directory to write the public and server-only subdirectories into.
            encoder: Path to the encoder checkpoint to fingerprint, if any.
            check_rows: How many rows to check against the stored twin distances.

        Raises:
            BundleError: If the delivery's files disagree with each other.
        """
        meta_path = source / bundle.META_FILENAME
        spectra_path = source / bundle.SPECTRA_FILENAME
        meta_bytes = meta_path.read_bytes()
        meta = json.loads(meta_bytes)
        bundle.validate_meta(meta, spectra_path.stat().st_size)
        n = int(meta["N"])

        with np.load(source / LATENTS_SOURCE_FILENAME) as npz:
            for name in ("z", "orig_meta_idx"):
                if name not in npz.files:
                    raise bundle.BundleError(
                        f"{LATENTS_SOURCE_FILENAME} is missing the {name!r} array"
                    )
            z = bundle.align_latents(npz["z"], npz["orig_meta_idx"], n)
        n_tokens, token_dim = int(z.shape[1]), int(z.shape[2])
        reference = bundle.normalize_tokens(z)
        rows = np.linspace(0, n - 1, num=min(check_rows, n)).astype(int)
        bundle.check_precomputed_twins(reference, meta, n_tokens, rows)

        bundle_id = hashlib.sha256(meta_bytes).hexdigest()
        public = out / bundle.PUBLIC_SUBDIR
        server = out / bundle.SERVER_SUBDIR
        public.mkdir(parents=True, exist_ok=True)
        server.mkdir(parents=True, exist_ok=True)

        shutil.copyfile(meta_path, public / bundle.META_FILENAME)
        shutil.copyfile(spectra_path, public / bundle.SPECTRA_FILENAME)
        # mtime=0 keeps the gzip bytes, and so the S3 etag, stable across runs.
        (public / bundle.META_GZ_FILENAME).write_bytes(
            gzip.compress(meta_bytes, compresslevel=9, mtime=0)
        )

        np.save(server / bundle.LATENTS_FILENAME, reference)
        np.savez(
            server / bundle.INDEX_FILENAME,
            Y=np.asarray(meta["Y"], dtype=np.float32),
            spec_id=np.asarray([str(s) for s in meta["spec_id"]]),
            sn=np.asarray([str(s) for s in meta["sn"]]),
            bundle_id=np.asarray(bundle_id),
        )

        descriptor = {
            "schema": bundle.BUNDLE_SCHEMA,
            "bundle_id": bundle_id,
            "N": n,
            "K": int(meta["K"]),
            "W": int(meta["W"]),
            "n_tokens": n_tokens,
            "token_dim": token_dim,
            "files": {
                bundle.META_FILENAME: len(meta_bytes),
                bundle.SPECTRA_FILENAME: spectra_path.stat().st_size,
            },
            "encoder_sha256": bundle.sha256_file(Path(encoder)) if encoder else None,
        }
        (public / bundle.DESCRIPTOR_FILENAME).write_text(
            json.dumps(descriptor, indent=2) + "\n"
        )
        logger.info(
            "Prepared DAEP twins bundle %s (N=%d) in %s", bundle_id[:12], n, out
        )
        self.stdout.write(f"Prepared bundle {bundle_id[:12]} (N={n}) in {out}")
