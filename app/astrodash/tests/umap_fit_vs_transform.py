#!/usr/bin/env python3
"""
Compare UMAP fit_transform (full dataset) vs transform-only (leave-one-out) for each spectrum.

Loads all spectra embeddings from the explorer directory (dash_twins_embeddings.npy or
dash_embeddings.npz), then for each spectrum (or a subset):
  - Left panel: UMAP fitted on the rest, query projected via transform (simulates production).
  - Right panel: UMAP fit_transform on full dataset (query included in fit).

Saves side-by-side plots per spectrum and computes:
  - Twins overlap: Jaccard between k-NN in embedding space vs k-NN in each 2D space.
  - 2D displacement: after Procrustes aligning leave-one-out to full, distance between
    query position from transform vs from fit_transform.

Output: tests/umap_fit_vs_transform/*.png and summary.json.

Run from repo root or from app/:
  python -m astrodash.tests.umap_fit_vs_transform
  python -m astrodash.tests.umap_fit_vs_transform --max-spectra 20 --k 10
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import numpy as np


# Same as extract_payload.py / plot_umap_from_embeddings.py
UMAP_RANDOM_STATE = 42
UMAP_N_NEIGHBORS = 15
UMAP_MIN_DIST = 0.1
UMAP_METRIC = "cosine"
DEFAULT_K_NEIGHBORS = 10
DEFAULT_MAX_SPECTRA = 30


def load_embeddings(explorer_dir: Path) -> np.ndarray:
    """Load (N, 1024) embedding matrix from .npy or .npz in explorer_dir."""
    explorer_dir = Path(explorer_dir)
    npy_path = explorer_dir / "dash_twins_embeddings.npy"
    npz_path = explorer_dir / "dash_embeddings.npz"
    if npy_path.is_file():
        emb = np.load(npy_path).astype(np.float32)
        if emb.ndim != 2 or emb.shape[1] != 1024:
            raise ValueError(f"Expected (N, 1024), got {emb.shape}")
        return emb
    if npz_path.is_file():
        data = np.load(npz_path, allow_pickle=True)
        for key in ("embeddings", "embedding", "X", "arr_0"):
            if key in data.keys():
                arr = data[key]
                if hasattr(arr, "shape") and len(arr.shape) == 2 and arr.shape[1] == 1024:
                    return np.asarray(arr, dtype=np.float32)
        for key in data.keys():
            arr = data[key]
            if hasattr(arr, "shape") and len(arr.shape) == 2 and arr.shape[1] == 1024:
                return np.asarray(arr, dtype=np.float32)
        raise ValueError(f"No (N, 1024) array in {npz_path}. Keys: {list(data.keys())}")
    raise FileNotFoundError(
        f"No embeddings found in {explorer_dir}. "
        "Expect dash_twins_embeddings.npy or dash_embeddings.npz."
    )


def load_types(explorer_dir: Path, n: int) -> list[str] | None:
    """Load type labels from payload JSON if present."""
    payload_path = Path(explorer_dir) / "dash_twins_payload.json"
    if not payload_path.is_file():
        return None
    try:
        with open(payload_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        types = payload.get("types")
        if types is not None and len(types) == n:
            return [str(t) for t in types]
    except Exception:
        pass
    return None


def cosine_knn(embeddings: np.ndarray, query_idx: int, k: int) -> np.ndarray:
    """Indices of k nearest neighbors to query_idx by cosine similarity (excluding self)."""
    n = embeddings.shape[0]
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.clip(norms, 1e-10, None)
    X = embeddings / norms
    sim = X @ X[query_idx]
    sim[query_idx] = -np.inf
    return np.argsort(sim)[::-1][:k]


def knn_2d(points_2d: np.ndarray, query_idx: int | None, query_point: np.ndarray | None, k: int) -> np.ndarray:
    """
    If query_idx is not None: k-NN from points_2d excluding query_idx (query not in points_2d).
    If query_point is not None: points_2d is (N,) and we need k-NN from query_point to points_2d (full N).
    Here we use: points_2d (N,2), query_point (2,) = position of query; return indices of k nearest rows to query_point.
    """
    if query_point is not None:
        # query_point (2,), points_2d (N,2) -> distances to query, return k nearest indices
        d = np.linalg.norm(points_2d - query_point, axis=1)
        return np.argsort(d)[:k]
    # query_idx in points_2d; exclude self
    d = np.linalg.norm(points_2d - points_2d[query_idx], axis=1)
    d[query_idx] = np.inf
    return np.argsort(d)[:k]


def jaccard(a: np.ndarray, b: np.ndarray) -> float:
    """Jaccard similarity |intersection| / |union| for 1D index arrays."""
    sa, sb = set(a.ravel().tolist()), set(b.ravel().tolist())
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 1.0


def procrustes_transform_single(point: np.ndarray, from_pts: np.ndarray, to_pts: np.ndarray) -> np.ndarray:
    """Transform a single point using the same Procrustes transform that aligns from_pts to to_pts."""
    from_c = from_pts - from_pts.mean(axis=0)
    to_c = to_pts - to_pts.mean(axis=0)
    nf = np.linalg.norm(from_c)
    nt = np.linalg.norm(to_c)
    if nf < 1e-12 or nt < 1e-12:
        return point
    from_s = from_c * (nt / nf)
    H = from_s.T @ to_c
    U, S, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    p = np.asarray(point, dtype=np.float64) - from_pts.mean(axis=0)
    p = p * (nt / nf)
    return (p @ R) + to_pts.mean(axis=0)


def run_umap_fit_transform(embeddings: np.ndarray) -> np.ndarray:
    """Fit UMAP on embeddings and return (N, 2) coords."""
    import umap
    n = embeddings.shape[0]
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="n_jobs", category=UserWarning)
        reducer = umap.UMAP(
            n_components=2,
            random_state=UMAP_RANDOM_STATE,
            n_neighbors=min(UMAP_N_NEIGHBORS, n - 1) if n > 1 else 1,
            min_dist=UMAP_MIN_DIST,
            metric=UMAP_METRIC,
        )
        return reducer.fit_transform(embeddings).astype(np.float64)


def run_umap_leave_one_out(embeddings: np.ndarray, query_idx: int):
    """
    Fit UMAP on all except query_idx; return reducer, 2D for rest, 2D for query (transform).
    """
    import umap
    n = embeddings.shape[0]
    mask = np.ones(n, dtype=bool)
    mask[query_idx] = False
    rest = embeddings[mask]
    n_rest = rest.shape[0]
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="n_jobs", category=UserWarning)
        reducer = umap.UMAP(
            n_components=2,
            random_state=UMAP_RANDOM_STATE,
            n_neighbors=min(UMAP_N_NEIGHBORS, n_rest - 1) if n_rest > 1 else 1,
            min_dist=UMAP_MIN_DIST,
            metric=UMAP_METRIC,
        )
        umap_rest = reducer.fit_transform(rest)
    query_2d = reducer.transform(embeddings[query_idx : query_idx + 1])[0]
    # Return full 2D array: rows 0..query_idx-1 are rest[:query_idx], row query_idx is query_2d, rows query_idx+1.. are rest[query_idx:]
    umap_full = np.zeros((n, 2), dtype=np.float64)
    umap_full[:query_idx] = umap_rest[:query_idx]
    umap_full[query_idx] = query_2d
    umap_full[query_idx + 1 :] = umap_rest[query_idx:]
    return umap_full, query_2d, reducer


def plot_side_by_side(
    out_path: Path,
    umap_transform: np.ndarray,
    umap_fit: np.ndarray,
    query_idx: int,
    types: list[str] | None,
    spectrum_label: str,
) -> None:
    """Save one figure: left = transform (leave-one-out), right = fit_transform (full); highlight query."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        raise RuntimeError("matplotlib is required. Install with: pip install matplotlib")

    n = umap_transform.shape[0]
    fig, (ax_left, ax_right) = plt.subplots(1, 2, figsize=(14, 6))

    def scatter_highlight(ax, coords, title, query_idx):
        other = np.ones(n, dtype=bool)
        other[query_idx] = False
        if types is not None:
            uniq = sorted(set(types))
            cmap = plt.get_cmap("tab20" if len(uniq) <= 20 else "tab20b")
            for t in uniq:
                mask = np.array([types[i] == t for i in range(n)]) & other
                if np.any(mask):
                    ax.scatter(coords[mask, 0], coords[mask, 1], s=8, alpha=0.6, label=t, edgecolors="none")
        else:
            ax.scatter(coords[other, 0], coords[other, 1], s=8, alpha=0.6, c="steelblue", edgecolors="none")
        ax.scatter(
            coords[query_idx, 0], coords[query_idx, 1],
            s=120, c="red", marker="*", edgecolors="black", linewidths=0.5, zorder=5, label="query",
        )
        ax.set_title(title)
        ax.set_xlabel("UMAP 1")
        ax.set_ylabel("UMAP 2")
        ax.set_aspect("equal", adjustable="datalim")

    scatter_highlight(ax_left, umap_transform, "Leave-one-out: fit(rest), transform(query)", query_idx)
    scatter_highlight(ax_right, umap_fit, "Full: fit_transform(all)", query_idx)
    fig.suptitle(f"Spectrum index {query_idx} ({spectrum_label})", fontsize=12)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare UMAP fit_transform vs transform per spectrum.")
    parser.add_argument(
        "--explorer-dir",
        type=str,
        default=None,
        help="Explorer dir with dash_twins_embeddings.npy or dash_embeddings.npz (default: astrodash/explorer).",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help="Output directory for plots and summary (default: tests/umap_fit_vs_transform).",
    )
    parser.add_argument(
        "--max-spectra",
        type=int,
        default=DEFAULT_MAX_SPECTRA,
        help=f"Max number of spectra to process (default: {DEFAULT_MAX_SPECTRA}). Use 0 for all.",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=DEFAULT_K_NEIGHBORS,
        help=f"k for twins / k-NN metrics (default: {DEFAULT_K_NEIGHBORS}).",
    )
    args = parser.parse_args()

    tests_dir = Path(__file__).resolve().parent
    explorer_dir = Path(args.explorer_dir).resolve() if args.explorer_dir else (tests_dir.parent / "explorer")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else (tests_dir / "umap_fit_vs_transform")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading embeddings from {explorer_dir} ...", flush=True)
    try:
        embeddings = load_embeddings(explorer_dir)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    n, dim = embeddings.shape
    assert dim == 1024, f"Expected 1024-dim embeddings, got {dim}"
    print(f"Loaded {n} spectra (shape {embeddings.shape}).", flush=True)

    types = load_types(explorer_dir, n)
    if types is not None:
        print(f"Loaded {len(set(types))} type labels.", flush=True)

    max_spectra = args.max_spectra if args.max_spectra > 0 else n
    indices = np.arange(min(max_spectra, n))
    k = min(args.k, n - 1) if n > 1 else 0

    # Full fit once (reused for right panel and for 2D k-NN)
    print("Fitting full UMAP (fit_transform) once ...", flush=True)
    umap_full = run_umap_fit_transform(embeddings)

    results = []
    for idx in indices:
        i = int(idx)
        print(f"  Spectrum {i + 1}/{len(indices)} ...", flush=True)
        umap_lo, query_2d_transform, _ = run_umap_leave_one_out(embeddings, i)

        # Plot
        spectrum_label = types[i] if types else f"idx_{i}"
        plot_side_by_side(
            out_dir / f"spectrum_{i:05d}.png",
            umap_lo,
            umap_full,
            i,
            types,
            spectrum_label,
        )

        # Twins: k-NN in embedding space
        nn_emb = cosine_knn(embeddings, i, k)
        # k-NN in full 2D (point i in umap_full)
        nn_full = knn_2d(umap_full, i, None, k)
        # k-NN in leave-one-out 2D: query is at query_2d_transform; "rest" positions are umap_lo[~i]
        rest_mask = np.ones(n, dtype=bool)
        rest_mask[i] = False
        nn_transform = knn_2d(umap_lo[rest_mask], None, query_2d_transform, k)
        # nn_transform gives indices into rest (0..n-2). Map back to original indices.
        rest_indices = np.where(rest_mask)[0]
        nn_transform_global = rest_indices[nn_transform]

        jaccard_emb_full = jaccard(nn_emb, nn_full)
        jaccard_emb_transform = jaccard(nn_emb, nn_transform_global)
        jaccard_full_transform = jaccard(nn_full, nn_transform_global)

        # 2D displacement: Procrustes align leave-one-out (N-1 points) to full (N-1 points), then transform query position
        rest_lo = umap_lo[rest_mask]
        rest_full = umap_full[rest_mask]
        query_aligned = procrustes_transform_single(query_2d_transform, rest_lo, rest_full)
        query_fit = umap_full[i]
        displacement = float(np.linalg.norm(query_aligned - query_fit))

        results.append({
            "spectrum_idx": i,
            "type": spectrum_label,
            "jaccard_embedding_vs_fit": round(jaccard_emb_full, 4),
            "jaccard_embedding_vs_transform": round(jaccard_emb_transform, 4),
            "jaccard_fit_vs_transform": round(jaccard_full_transform, 4),
            "procrustes_displacement_2d": round(displacement, 6),
        })

    # Summary
    jaccard_emb_fit = [r["jaccard_embedding_vs_fit"] for r in results]
    jaccard_emb_trans = [r["jaccard_embedding_vs_transform"] for r in results]
    jaccard_fit_trans = [r["jaccard_fit_vs_transform"] for r in results]
    displacements = [r["procrustes_displacement_2d"] for r in results]

    summary = {
        "n_spectra_processed": len(results),
        "n_total": n,
        "k": k,
        "mean_jaccard_embedding_vs_fit_transform": round(float(np.mean(jaccard_emb_fit)), 4),
        "mean_jaccard_embedding_vs_transform_only": round(float(np.mean(jaccard_emb_trans)), 4),
        "mean_jaccard_fit_vs_transform_twins": round(float(np.mean(jaccard_fit_trans)), 4),
        "mean_procrustes_displacement_2d": round(float(np.mean(displacements)), 6),
        "max_procrustes_displacement_2d": round(float(np.max(displacements)), 6),
        "per_spectrum": results,
    }
    summary_path = out_dir / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"Wrote {summary_path}", flush=True)

    print("\n--- Summary ---", flush=True)
    print(f"  Mean Jaccard (embedding k-NN vs fit_transform 2D k-NN): {summary['mean_jaccard_embedding_vs_fit_transform']}", flush=True)
    print(f"  Mean Jaccard (embedding k-NN vs transform-only 2D k-NN): {summary['mean_jaccard_embedding_vs_transform_only']}", flush=True)
    print(f"  Mean Jaccard (fit 2D twins vs transform 2D twins): {summary['mean_jaccard_fit_vs_transform_twins']}", flush=True)
    print(f"  Mean 2D displacement (Procrustes-aligned): {summary['mean_procrustes_displacement_2d']}", flush=True)
    print(f"  Max 2D displacement: {summary['max_procrustes_displacement_2d']}", flush=True)
    print(f"  Plots saved in {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
