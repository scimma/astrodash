"""
Domain service for DASH Twins similarity search.

Loads precomputed embeddings and fitted UMAP/PCA from the explorer artifacts
(dash_twins_embeddings.npy, dash_twins_umap.pkl, dash_twins_pca.pkl) and provides
cosine-similarity search and 2D projection for a query embedding (e.g. from a
user-uploaded spectrum).
"""
import pickle
from pathlib import Path

import numpy as np

from astrodash.config.logging import get_logger

logger = get_logger(__name__)


class TwinsSearchService:
    """
    Service for finding nearest-neighbor "twins" in the DASH embedding space
    and projecting a query embedding into the same 2D (UMAP/PCA) as the payload.
    """

    def __init__(self, explorer_dir: Path):
        """
        Load artifacts from explorer_dir. Raises FileNotFoundError if any required file is missing.

        Args:
            explorer_dir: Directory containing dash_twins_embeddings.npy,
                dash_twins_umap.pkl, dash_twins_pca.pkl.
        """
        explorer_dir = Path(explorer_dir)
        emb_path = explorer_dir / "dash_twins_embeddings.npy"
        umap_path = explorer_dir / "dash_twins_umap.pkl"
        pca_path = explorer_dir / "dash_twins_pca.pkl"
        for p, name in [(emb_path, "embeddings"), (umap_path, "UMAP"), (pca_path, "PCA")]:
            if not p.is_file():
                raise FileNotFoundError(
                    f"Twins artifact not found: {p}. Run extract_payload.py --build-artifacts."
                )

        self._embeddings = np.load(emb_path).astype(np.float32)
        with open(umap_path, "rb") as f:
            self._umap = pickle.load(f)
        with open(pca_path, "rb") as f:
            self._pca = pickle.load(f)

        n, dim = self._embeddings.shape
        if dim != 1024:
            raise ValueError(f"Expected embedding dimension 1024, got {dim}")
        self._n = n
        self._dim = dim

        # Precompute L2-normalized embeddings for cosine similarity (cos_sim = dot of normalized)
        norms = np.linalg.norm(self._embeddings, axis=1, keepdims=True)
        norms = np.clip(norms, 1e-10, None)
        self._normed = (self._embeddings / norms).astype(np.float32)
        logger.info("TwinsSearchService loaded: N=%d, dim=%d", n, dim)

    @property
    def n_spectra(self) -> int:
        """Number of spectra in the precomputed embedding matrix."""
        return self._n

    def find_twins(
        self,
        query_embedding: np.ndarray,
        k: int = 10,
    ) -> dict:
        """
        Find the k most similar spectra to the query embedding via cosine similarity,
        and project the query into the same 2D UMAP/PCA space as the payload.

        Args:
            query_embedding: 1D array of shape (1024,) or (1, 1024).
            k: Number of nearest neighbors to return.

        Returns:
            Dict with:
                - query_umap: [x, y] in UMAP space
                - query_pca: [x, y] in PCA space
                - twin_indices: list of k row indices (into the payload)
                - twin_similarities: list of k cosine similarities (1 = identical)
        """
        q = np.asarray(query_embedding, dtype=np.float32).ravel()
        if q.shape[0] != self._dim:
            raise ValueError(f"Query embedding must have length {self._dim}, got {q.shape[0]}")

        # Normalize query
        q_norm = np.linalg.norm(q)
        q_norm = np.clip(q_norm, 1e-10, None)
        q_unit = (q / q_norm).astype(np.float32)

        # Cosine similarities (already normalized DB)
        sims = self._normed @ q_unit
        top_k = np.argsort(sims)[::-1][:k]
        twin_indices = top_k.tolist()
        twin_similarities = sims[top_k].tolist()

        # Project query into 2D
        q_2d = q.reshape(1, -1)
        query_umap = self._umap.transform(q_2d)[0].tolist()
        query_pca = self._pca.transform(q_2d)[0].tolist()

        return {
            "query_umap": [float(query_umap[0]), float(query_umap[1])],
            "query_pca": [float(query_pca[0]), float(query_pca[1])],
            "twin_indices": twin_indices,
            "twin_similarities": twin_similarities,
        }
