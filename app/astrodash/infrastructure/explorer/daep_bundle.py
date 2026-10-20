"""The DAEP spectral twins reference bundle: layout, validation, and metric.

Henna Abunemeh's latent explorer is driven by two files her generator writes:
``data_meta.json`` (per-spectrum metadata, 2-D UMAP coordinates, and the
precomputed top-K twins) and ``data_spectra.bin`` (per spectrum, ``W`` float32
flux values, ``W`` float32 DAEP reconstruction values, then ``W`` uint8 mask
values). Neither carries a format version, so ``prepare_twins_bundle`` checks
them structurally and writes a descriptor that the explorer page and the twins
service read before trusting the data.

The bundle is split across two directories under ``{data_dir}/explorer/``:
``daep/`` holds what the browser fetches and is served publicly, and
``daep_server/`` holds the reference latents, which only the server reads.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

# An integer, not a dotted string, so the version-literal guard leaves it be.
BUNDLE_SCHEMA = 1

# Per wavelength bin: float32 flux + float32 reconstruction + uint8 mask.
BIN_BYTES_PER_WAVE = 4 + 4 + 1

PUBLIC_SUBDIR = "daep"
SERVER_SUBDIR = "daep_server"

DESCRIPTOR_FILENAME = "bundle.json"
META_FILENAME = "data_meta.json"
META_GZ_FILENAME = "data_meta.json.gz"
SPECTRA_FILENAME = "data_spectra.bin"
LATENTS_FILENAME = "reference_latents.npy"
INDEX_FILENAME = "reference_index.npz"

# Scalars the explorer reads from the metadata.
REQUIRED_SCALAR_KEYS = (
    "N",
    "K",
    "W",
    "k_show_default",
    "exclude_same_sn_default",
    "type_col_used",
)

# Per-spectrum lists the explorer reads; each must have N entries.
REQUIRED_ROW_KEYS = (
    "Y",
    "hover",
    "sn",
    "spec_id",
    "obj_type",
    "raw_type",
    "canonical_type",
    "broad_type",
    "source_dataset",
    "instrument",
    "phase",
    "jd",
    "jd_str",
    "z",
    "obs_date",
    "nbrs",
    "dists",
)


class BundleError(ValueError):
    """Raised when a bundle or its descriptor is not in the expected format."""


def validate_meta(meta: dict[str, Any], spectra_size: int) -> None:
    """Check the metadata's shape and its agreement with the spectra file.

    Args:
        meta: The parsed ``data_meta.json``.
        spectra_size: Size of ``data_spectra.bin`` in bytes.

    Raises:
        BundleError: If a key is missing, a per-spectrum list has the wrong
            length, the wavelength grid length differs from ``W``, or the
            spectra file size differs from ``N * 9 * W``.
    """
    for key in REQUIRED_SCALAR_KEYS + REQUIRED_ROW_KEYS + ("wave",):
        if key not in meta:
            raise BundleError(f"{META_FILENAME} is missing the {key!r} key")
    n, w = int(meta["N"]), int(meta["W"])
    for key in REQUIRED_ROW_KEYS:
        if len(meta[key]) != n:
            raise BundleError(
                f"{META_FILENAME} {key!r} has {len(meta[key])} entries, expected N={n}"
            )
    if len(meta["wave"]) != w:
        raise BundleError(
            f"{META_FILENAME} 'wave' has {len(meta['wave'])} entries, expected W={w}"
        )
    expected = n * BIN_BYTES_PER_WAVE * w
    if spectra_size != expected:
        raise BundleError(
            f"{SPECTRA_FILENAME} should be {expected} bytes (N*9*W) but is {spectra_size}"
        )


def normalize_tokens(z: np.ndarray) -> np.ndarray:
    """Unit-normalize each latent token and flatten each spectrum's tokens.

    Args:
        z: Latents of shape ``(n, tokens, dim)``.

    Returns:
        A float32 array of shape ``(n, tokens * dim)``.
    """
    z = np.asarray(z, dtype=np.float32)
    norms = np.maximum(np.linalg.norm(z, axis=-1, keepdims=True), 1e-12)
    return (z / norms).reshape(z.shape[0], -1)


def latent_distances(
    reference: np.ndarray, query: np.ndarray, n_tokens: int
) -> np.ndarray:
    """Mean-over-tokens cosine distance from one query to every reference row.

    This is the metric Henna's generator used for the precomputed twins: the
    mean of the per-token cosine similarities, subtracted from one.

    Args:
        reference: Token-normalized, flattened reference latents ``(n, tokens*dim)``.
        query: One token-normalized, flattened latent ``(tokens*dim,)``.
        n_tokens: Number of latent tokens per spectrum.

    Returns:
        Distances of shape ``(n,)``.
    """
    return 1.0 - (reference @ query) / float(n_tokens)


def align_latents(z: np.ndarray, orig_meta_idx: np.ndarray, n: int) -> np.ndarray:
    """Reorder latents so row ``i`` belongs to metadata row ``i``.

    Args:
        z: Latents as saved by Henna's training run.
        orig_meta_idx: For each latent row, the metadata row it belongs to.
        n: Number of metadata rows.

    Returns:
        The latents in metadata row order.

    Raises:
        BundleError: If the index is not a permutation of the metadata rows.
    """
    idx = np.asarray(orig_meta_idx)
    if len(z) != n or len(idx) != n or not np.array_equal(np.sort(idx), np.arange(n)):
        raise BundleError(f"latents do not map one-to-one onto the {n} metadata rows")
    aligned = np.empty_like(z)
    aligned[idx] = z
    return aligned


def check_precomputed_twins(
    reference: np.ndarray,
    meta: dict[str, Any],
    n_tokens: int,
    rows: np.ndarray,
    atol: float = 1e-4,
) -> None:
    """Confirm the latents reproduce the metadata's twin distances for some rows.

    Args:
        reference: Token-normalized, flattened, aligned reference latents.
        meta: The parsed ``data_meta.json``.
        n_tokens: Number of latent tokens per spectrum.
        rows: Metadata rows to check.
        atol: Allowed absolute difference in distance.

    Raises:
        BundleError: If a row's recomputed distances to its listed twins differ
            from the stored ones.
    """
    for i in rows:
        i = int(i)
        nbrs = np.asarray(meta["nbrs"][i])
        stored = np.asarray(meta["dists"][i], dtype=np.float32)
        recomputed = latent_distances(reference[nbrs], reference[i], n_tokens)
        if not np.allclose(recomputed, stored, atol=atol):
            raise BundleError(
                f"latents disagree with the stored twin distances for row {i}; "
                "the latents and metadata may come from different runs"
            )


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 of a file.

    Args:
        path: The file to hash.

    Returns:
        The hex digest.
    """
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def read_descriptor(path: Path) -> dict[str, Any]:
    """Read a bundle descriptor and check that its schema is understood.

    Args:
        path: Path to ``bundle.json``.

    Returns:
        The descriptor.

    Raises:
        BundleError: If the schema is not ``BUNDLE_SCHEMA`` or a field the
            explorer relies on is missing or of the wrong type.
    """
    descriptor = json.loads(Path(path).read_text())
    if not isinstance(descriptor, dict):
        raise BundleError(f"{DESCRIPTOR_FILENAME} is not a JSON object")
    if descriptor.get("schema") != BUNDLE_SCHEMA:
        raise BundleError(
            f"unsupported bundle schema {descriptor.get('schema')!r}; expected {BUNDLE_SCHEMA}"
        )
    files = descriptor.get("files")
    if (
        not isinstance(descriptor.get("bundle_id"), str)
        or not all(isinstance(descriptor.get(k), int) for k in ("N", "K", "W"))
        or not isinstance(files, dict)
        or not isinstance(files.get(META_FILENAME), int)
    ):
        raise BundleError(
            f"{DESCRIPTOR_FILENAME} is missing bundle_id, N, K, W, or the metadata size"
        )
    return descriptor
