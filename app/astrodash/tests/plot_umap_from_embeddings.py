#!/usr/bin/env python3
"""
Script to load dash_embeddings.npz, run UMAP, and save a scatter plot (colored by type).

If dash_twins_payload.json in explorer/ contains umap_x, umap_y from the original
dash_twinsfromspace.html (same row order as the embeddings), the script searches
UMAP hyperparameters (metric, n_neighbors, min_dist, random_state) to minimize
Procrustes MSE vs that reference, so the plot matches the HTML layout.

Run from repo root or from app/:
  python -m astrodash.tests.plot_umap_from_embeddings
  pytest app/astrodash/tests/plot_umap_from_embeddings.py -v
"""
import sys
import warnings
from pathlib import Path

import numpy as np

# Same as extract_payload.py
UMAP_RANDOM_STATE = 42
UMAP_N_NEIGHBORS = 15
UMAP_MIN_DIST = 0.1
UMAP_METRIC = "cosine"


def load_embeddings_from_npz(npz_path: Path) -> np.ndarray:
    """Load (N, 1024) embedding matrix from .npz. Tries common keys."""
    data = np.load(npz_path, allow_pickle=True)
    keys = list(data.keys())
    for key in ("embeddings", "embedding", "X", "arr_0"):
        if key in keys:
            arr = data[key]
            if hasattr(arr, "shape") and len(arr.shape) == 2 and arr.shape[1] == 1024:
                return np.asarray(arr, dtype=np.float32)
    for key in keys:
        arr = data[key]
        if hasattr(arr, "shape") and len(arr.shape) == 2 and arr.shape[1] == 1024:
            return np.asarray(arr, dtype=np.float32)
    raise ValueError(
        f"No array with shape (N, 1024) found in {npz_path}. Keys: {keys}. "
        "Expected key like 'embeddings' or array with second dim 1024."
    )


def load_types_for_embeddings(npz_path: Path, explorer_dir: Path, n: int):
    """Load type labels (length n) from npz or payload JSON. Returns list of str or None."""
    data = np.load(npz_path, allow_pickle=True)
    for key in ("sn_types", "types", "labels", "type"):
        if key in data.keys():
            arr = data[key]
            if hasattr(arr, "__len__") and len(arr) == n:
                return [str(x) for x in arr]
    payload_path = explorer_dir / "dash_twins_payload.json"
    if payload_path.is_file():
        try:
            import json
            with open(payload_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            types = payload.get("types")
            if types is not None and len(types) == n:
                return [str(t) for t in types]
        except Exception:
            pass
    return None


def load_reference_umap_2d(explorer_dir: Path, n: int) -> np.ndarray | None:
    """Load reference umap_x, umap_y from payload JSON (same order as embeddings). Returns (n, 2) or None."""
    payload_path = explorer_dir / "dash_twins_payload.json"
    if not payload_path.is_file():
        return None
    try:
        import json
        with open(payload_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        ux = payload.get("umap_x")
        uy = payload.get("umap_y")
        if ux is None or uy is None or len(ux) != n or len(uy) != n:
            return None
        return np.column_stack([np.asarray(ux, dtype=np.float64), np.asarray(uy, dtype=np.float64)])
    except Exception:
        return None


def procrustes_mse(our_2d: np.ndarray, ref_2d: np.ndarray) -> float:
    """Align our_2d to ref_2d (center, scale, rotate/reflect) and return mean squared error."""
    A = np.asarray(ref_2d, dtype=np.float64)
    B = np.asarray(our_2d, dtype=np.float64)
    A = A - A.mean(axis=0)
    B = B - B.mean(axis=0)
    na = np.linalg.norm(A)
    nb = np.linalg.norm(B)
    if na < 1e-12 or nb < 1e-12:
        return float("inf")
    B = B * (na / nb)
    H = B.T @ A
    U, S, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    aligned = B @ R
    return float(np.mean((A - aligned) ** 2))


def find_umap_params_matching_reference(
    embeddings: np.ndarray,
    ref_2d: np.ndarray,
    n: int,
) -> tuple:
    """Try UMAP hyperparameter combinations; return (best_params, best_2d, best_mse)."""
    import umap
    best_mse = float("inf")
    best_params = None
    best_2d = None
    # Suppress UMAP's "n_jobs overridden by random_state" warning during grid search
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="n_jobs", category=UserWarning)
        for metric in ("cosine", "euclidean"):
            for n_neighbors in (10, 15, 20, 30):
                for min_dist in (0.0, 0.1, 0.25, 0.5):
                    for random_state in (0, 42):
                        try:
                            reducer = umap.UMAP(
                                n_components=2,
                                random_state=random_state,
                                n_neighbors=min(n_neighbors, n - 1) if n > 1 else 1,
                                min_dist=min_dist,
                                metric=metric,
                            )
                            umap_2d = reducer.fit_transform(embeddings)
                            for order in ([0, 1], [1, 0]):
                                u = umap_2d[:, order].astype(np.float64)
                                mse = procrustes_mse(u, ref_2d)
                                if mse < best_mse:
                                    best_mse = mse
                                    best_params = {
                                        "metric": metric,
                                        "n_neighbors": n_neighbors,
                                        "min_dist": min_dist,
                                        "random_state": random_state,
                                        "swap_axes": order == [1, 0],
                                    }
                                    best_2d = u.copy()
                        except Exception:
                            continue
    return best_params, best_2d, best_mse


def run_umap_fixed_params(
    embeddings: np.ndarray,
    metric: str = "cosine",
    n_neighbors: int = 15,
    min_dist: float = 0.1,
    random_state: int = 42,
    swap_axes: bool = False,
) -> np.ndarray:
    """Fit UMAP with fixed params; return (n, 2) coords. swap_axes=True applies 90 CCW (swap x,y)."""
    import umap
    n = embeddings.shape[0]
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="n_jobs", category=UserWarning)
        reducer = umap.UMAP(
            n_components=2,
            random_state=random_state,
            n_neighbors=min(n_neighbors, n - 1) if n > 1 else 1,
            min_dist=min_dist,
            metric=metric,
        )
        umap_2d = reducer.fit_transform(embeddings)
    if swap_axes:
        umap_2d = umap_2d[:, [1, 0]]
    return umap_2d.astype(np.float64)


def run_umap_and_save_image(
    npz_path: Path,
    out_path: Path,
    explorer_dir: Path | None = None,
    use_reference_search: bool = True,
    fixed_params_no_flip: bool = False,
) -> np.ndarray:
    """Load embeddings, fit UMAP (optionally search params to match reference), save scatter plot. Returns UMAP 2D.

    If fixed_params_no_flip is True, uses metric=cosine, n_neighbors=15, min_dist=0.1, random_state=42,
    swap_axes=False and ignores reference search.
    """
    embeddings = load_embeddings_from_npz(npz_path)
    n = embeddings.shape[0]
    assert embeddings.shape[1] == 1024, f"Expected (N, 1024), got {embeddings.shape}"

    explorer_dir = Path(explorer_dir) if explorer_dir is not None else npz_path.parent
    ref_2d = load_reference_umap_2d(explorer_dir, n) if use_reference_search and not fixed_params_no_flip else None

    import umap

    if fixed_params_no_flip:
        print("Using fixed UMAP params (cosine, n_neighbors=15, min_dist=0.1, random_state=42, swap_axes=False).", flush=True)
        umap_2d = run_umap_fixed_params(
            embeddings,
            metric=UMAP_METRIC,
            n_neighbors=UMAP_N_NEIGHBORS,
            min_dist=UMAP_MIN_DIST,
            random_state=UMAP_RANDOM_STATE,
            swap_axes=False,
        )
    elif ref_2d is not None:
        print("Reference UMAP 2D found; searching hyperparameters to match dash_twinsfromspace.html...", flush=True)
        best_params, umap_2d, mse = find_umap_params_matching_reference(embeddings, ref_2d, n)
        print(f"Best params: {best_params} (Procrustes MSE={mse:.6g})", flush=True)
    else:
        print("No reference UMAP in explorer/dash_twins_payload.json; using default UMAP params.", flush=True)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="n_jobs", category=UserWarning)
            reducer = umap.UMAP(
                n_components=2,
                random_state=UMAP_RANDOM_STATE,
                n_neighbors=min(UMAP_N_NEIGHBORS, n - 1) if n > 1 else 1,
                min_dist=UMAP_MIN_DIST,
            )
            umap_2d = reducer.fit_transform(embeddings)
            umap_2d = umap_2d[:, [1, 0]]  # swap for display

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        raise RuntimeError("matplotlib is required to save the image. Install with: pip install matplotlib")

    explorer_dir = Path(explorer_dir) if explorer_dir is not None else npz_path.parent
    types = load_types_for_embeddings(npz_path, explorer_dir, n)

    fig, ax = plt.subplots(figsize=(12, 9))
    if types is not None:
        uniq_types = sorted(set(types))
        cmap = plt.get_cmap("tab20")
        if len(uniq_types) > 20:
            cmap = plt.get_cmap("tab20b")
        if len(uniq_types) > 40:
            cmap = plt.get_cmap("tab20c")
        type_to_idx = {t: i for i, t in enumerate(uniq_types)}
        colors = [cmap(type_to_idx[t] % 20) for t in types]
        for ty in uniq_types:
            mask = [t == ty for t in types]
            ax.scatter(
                umap_2d[mask, 0], umap_2d[mask, 1],
                s=6, alpha=0.7, label=ty, edgecolors="none",
            )
        ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize=8, ncol=1)
        title = f"UMAP of dash_embeddings.npz by type (n={n}, {len(uniq_types)} types)"
    else:
        ax.scatter(umap_2d[:, 0], umap_2d[:, 1], s=4, alpha=0.6, c="steelblue", edgecolors="none")
        title = f"UMAP of dash_embeddings.npz (n={n}, same params as extract_payload)"
    if fixed_params_no_flip:
        title += " (cosine, no axis flip)"
    ax.set_title(title)
    ax.set_xlabel("UMAP 2")
    ax.set_ylabel("UMAP 1")
    ax.set_aspect("equal", adjustable="datalim")
    fig.tight_layout(rect=[0, 0, 0.85, 1])
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out_path}", flush=True)
    return umap_2d


def main() -> int:
    # Paths relative to this file
    tests_dir = Path(__file__).resolve().parent
    explorer_dir = tests_dir.parent / "explorer"
    npz_path = explorer_dir / "dash_embeddings.npz"
    out_path = tests_dir / "umap_from_dash_embeddings.png"

    if not npz_path.is_file():
        print(f"Error: {npz_path} not found.", file=sys.stderr, flush=True)
        return 1
    # Plot with fixed params (cosine, 15, 0.1, 42) and no axis flip
    out_no_flip = tests_dir / "umap_from_dash_embeddings_cosine_no_flip.png"
    run_umap_and_save_image(npz_path, out_no_flip, explorer_dir=explorer_dir, fixed_params_no_flip=True)
    # Optional: also run reference-search plot
    run_umap_and_save_image(npz_path, out_path, explorer_dir=explorer_dir)
    return 0


def test_umap_from_embeddings_npz_saves_image():
    """Load dash_embeddings.npz, run UMAP with same params as extract_payload, save plot."""
    tests_dir = Path(__file__).resolve().parent
    explorer_dir = tests_dir.parent / "explorer"
    npz_path = explorer_dir / "dash_embeddings.npz"
    out_path = tests_dir / "umap_from_dash_embeddings.png"
    if not npz_path.is_file():
        raise FileNotFoundError(f"Required file not found: {npz_path}")
    umap_2d = run_umap_and_save_image(npz_path, out_path, explorer_dir=explorer_dir)
    assert umap_2d.shape[1] == 2
    assert out_path.is_file()


def test_umap_fixed_params_no_flip_saves_image():
    """Plot UMAP with fixed params (cosine, 15, 0.1, 42) and swap_axes=False."""
    tests_dir = Path(__file__).resolve().parent
    explorer_dir = tests_dir.parent / "explorer"
    npz_path = explorer_dir / "dash_embeddings.npz"
    out_path = tests_dir / "umap_from_dash_embeddings_cosine_no_flip.png"
    if not npz_path.is_file():
        raise FileNotFoundError(f"Required file not found: {npz_path}")
    umap_2d = run_umap_and_save_image(
        npz_path, out_path, explorer_dir=explorer_dir, fixed_params_no_flip=True
    )
    assert umap_2d.shape[1] == 2
    assert out_path.is_file()


if __name__ == "__main__":
    sys.exit(main())
