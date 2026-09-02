"""
Tests for DASH embedding extraction (Phase 1: twins finder).
Ensures forward_embedding matches the classification path and classify_sync
returns an embedding identical to extract_embedding_sync for the same spectrum.
"""
import numpy as np
import os
import torch
from torch.nn import functional as F

from astrodash.config.settings import get_settings
from astrodash.domain.models.spectrum import Spectrum
from astrodash.infrastructure.ml.classifiers.architectures import AstroDashPyTorchNet
from astrodash.infrastructure.ml.classifiers.dash_classifier import DashClassifier


class TestAstroDashPyTorchNetEmbedding:
    """Verify forward_embedding is the same representation used in forward()."""

    def test_forward_embedding_shape(self):
        """Embedding has shape (batch, 1024)."""
        model = AstroDashPyTorchNet(n_types=10, im_width=32)
        model.eval()
        x = torch.randn(3, 1024)
        with torch.no_grad():
            emb = model.forward_embedding(x)
        assert emb.shape == (3, 1024)

    def test_forward_embedding_reproduces_softmax(self):
        """
        The embedding is the pre-softmax layer: embedding -> dropout -> output -> softmax
        must equal forward(x). In eval mode dropout is identity.
        """
        model = AstroDashPyTorchNet(n_types=10, im_width=32)
        model.eval()
        x = torch.randn(2, 1024)
        with torch.no_grad():
            out_forward = model(x)
            emb = model.forward_embedding(x)
            # Same path as forward(): dropout then output then softmax
            h_drop = model.dropout(emb)
            logits = model.output(h_drop)
            out_manual = F.softmax(logits, dim=1)
        assert torch.allclose(out_forward, out_manual), (
            "forward_embedding must be the same h_fc1 as in forward()"
        )


class TestDashClassifierEmbedding:
    """Verify classify_sync returns embedding and it matches extract_embedding_sync."""

    def _make_spectrum(self, w0=3500.0, w1=10000.0, n_pts=500):
        """Spectrum with wavelength and flux that DashSpectrumProcessor can process."""
        x = np.linspace(w0, w1, n_pts).tolist()
        y = (np.random.RandomState(42).rand(n_pts) + 0.1).tolist()
        return Spectrum(x=x, y=y, redshift=0.0)

    def test_classify_sync_returns_embedding_when_model_loaded(self):
        """When DASH model is loaded, classify_sync result includes 'embedding' of length 1024."""
        settings = get_settings()
        if not os.path.isfile(settings.dash_model_path):
            return  # skip when model file not present
        classifier = DashClassifier(config=settings)
        if classifier.model is None:
            return
        spectrum = self._make_spectrum(
            w0=settings.w0, w1=settings.w1
        )
        result = classifier.classify_sync(spectrum)
        assert "embedding" in result
        emb = result["embedding"]
        assert isinstance(emb, list)
        assert len(emb) == 1024
        assert all(isinstance(v, (int, float)) for v in emb)

    def test_extract_embedding_matches_classify_sync_embedding(self):
        """
        For the same spectrum, result['embedding'] from classify_sync must match
        extract_embedding_sync(spectrum) exactly (same preprocessing and forward path).
        """
        settings = get_settings()
        if not os.path.isfile(settings.dash_model_path):
            return
        classifier = DashClassifier(config=settings)
        if classifier.model is None:
            return
        spectrum = self._make_spectrum(w0=settings.w0, w1=settings.w1)
        result = classifier.classify_sync(spectrum)
        assert "embedding" in result
        from_classify = np.array(result["embedding"], dtype=np.float64)
        from_extract = classifier.extract_embedding_sync(spectrum)
        assert from_classify.shape == (1024,)
        assert from_extract.shape == (1024,)
        np.testing.assert_allclose(
            from_classify,
            from_extract,
            rtol=1e-9,
            atol=1e-9,
            err_msg="classify_sync embedding must equal extract_embedding_sync for same spectrum",
        )
