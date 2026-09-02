"""Production 1D CNN classifiers for website_final (not the original DASH 2D CNN).

Classify from observed ``spectrum.x`` / ``spectrum.y`` (and redshift for the
z-variant) via ``OnedCnnSpectrumProcessor``. Do not accept a prebuilt 1025-vector
on the spectrum object.
"""

from __future__ import annotations

import os
from typing import Any, Literal, Optional

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from astrodash.config.logging import get_logger
from astrodash.config.settings import Settings, get_settings
from astrodash.infrastructure.ml.classifiers.base import BaseClassifier
from astrodash.infrastructure.ml.classifiers.utils import (
    classification_from_logits,
    inference_device,
    load_idx_to_label,
    preprocess_redshift,
    spectrum_redshift,
    torch_load,
)

logger = get_logger(__name__)

OnedCnnVariant = Literal["z", "noz"]


class _DashCNN1D(nn.Module):
    """3 Conv1d blocks + 2 FC layers. Input (B, 1025); last sample is z or 0.0."""

    def __init__(self, input_length: int = 1025, num_classes: int = 5):
        super().__init__()
        self.conv1 = nn.Sequential(
            nn.Conv1d(1, 32, kernel_size=7, padding=3),
            nn.BatchNorm1d(32),
            nn.ReLU(inplace=True),
            nn.MaxPool1d(kernel_size=4, stride=4),
        )
        self.conv2 = nn.Sequential(
            nn.Conv1d(32, 64, kernel_size=5, padding=2),
            nn.BatchNorm1d(64),
            nn.ReLU(inplace=True),
            nn.MaxPool1d(kernel_size=4, stride=4),
        )
        self.conv3 = nn.Sequential(
            nn.Conv1d(64, 128, kernel_size=3, padding=1),
            nn.BatchNorm1d(128),
            nn.ReLU(inplace=True),
            nn.MaxPool1d(kernel_size=4, stride=4),
        )
        conv_out_length = input_length // (4 * 4 * 4)
        self.flat_size = 128 * conv_out_length
        self.fc1 = nn.Linear(self.flat_size, 256)
        self.dropout = nn.Dropout(0.5)
        self.fc2 = nn.Linear(256, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x.unsqueeze(1)
        x = self.conv1(x)
        x = self.conv2(x)
        x = self.conv3(x)
        x = x.flatten(1)
        x = F.relu(self.fc1(x))
        x = self.dropout(x)
        x = self.fc2(x)
        return x


class OnedCnnClassifier(BaseClassifier):
    """Loads a bare DashCNN1D state_dict from Settings (z or no-z path)."""

    def __init__(
        self,
        variant: OnedCnnVariant,
        config: Settings = None,
    ):
        super().__init__(config)
        self.config = config or get_settings()
        self.variant = variant
        self.device = inference_device()
        self.model = None
        if variant == "z":
            self.model_path = self.config.oned_cnn_z_model_path
            mapping_path = self.config.oned_cnn_z_class_mapping_path
        else:
            self.model_path = self.config.oned_cnn_noz_model_path
            mapping_path = self.config.oned_cnn_noz_class_mapping_path
        self.idx_to_label = load_idx_to_label(mapping_path)
        self._processor = None
        self._load_model()

    def _load_model(self) -> None:
        if not self.model_path or not os.path.exists(self.model_path):
            logger.error("1D CNN model file not found at %s", self.model_path)
            self.model = None
            return
        try:
            n_cls = len(self.config.website_final_label_mapping)
            model = _DashCNN1D(
                input_length=self.config.oned_cnn_input_length(),
                num_classes=n_cls,
            ).to(self.device)
            state = torch_load(
                self.model_path, map_location=self.device, weights_only=True
            )
            model.load_state_dict(state, strict=True)
            model.eval()
            self.model = model
            logger.debug(
                "Loaded 1D CNN (%s) from %s", self.variant, self.model_path
            )
        except Exception as e:
            logger.error("Failed to load 1D CNN model: %s", e)
            self.model = None

    def _processor_instance(self):
        if self._processor is None:
            from astrodash.infrastructure.ml.data_processor import OnedCnnSpectrumProcessor

            self._processor = OnedCnnSpectrumProcessor()
        return self._processor

    def _vector_from_spectrum(self, spectrum: Any) -> Optional[np.ndarray]:
        processed = self._preprocess_from_xy(spectrum)
        if processed is not None:
            return processed
        logger.error("No processable spectrum.x/y for 1D CNN classify")
        return None

    def _preprocess_from_xy(self, spectrum: Any) -> Optional[np.ndarray]:
        x = np.asarray(getattr(spectrum, "x", []), dtype=np.float64).reshape(-1)
        y = np.asarray(getattr(spectrum, "y", []), dtype=np.float64).reshape(-1)
        if x.size < 2 or x.size != y.size:
            return None
        include_redshift = self.variant == "z"
        redshift = preprocess_redshift(spectrum, required=include_redshift)
        if redshift is None:
            logger.error("Redshift is required for the 1D CNN z-variant")
            return None
        try:
            return self._processor_instance().process(
                x, y, redshift, include_redshift=include_redshift
            )
        except Exception as e:
            logger.error("1D CNN preprocessing failed: %s", e)
            return None

    def classify_sync(self, spectrum: Any) -> dict:
        if self.model is None:
            logger.error("1D CNN model is not loaded. Returning empty result.")
            return {}
        try:
            vec = self._vector_from_spectrum(spectrum)
            if vec is None:
                return {}
            redshift = spectrum_redshift(spectrum)
            x = torch.tensor(vec, dtype=torch.float32).unsqueeze(0).to(self.device)
            with torch.no_grad():
                logits = self.model(x)
            return classification_from_logits(logits, self.idx_to_label, redshift)
        except Exception as e:
            logger.error("Error during 1D CNN classification: %s", e)
            return {}

    async def classify(self, spectrum: Any) -> dict:
        import asyncio

        return await asyncio.to_thread(self.classify_sync, spectrum)


class OnedCnnZClassifier(OnedCnnClassifier):
    def __init__(self, config: Settings = None):
        super().__init__("z", config=config)


class OnedCnnNozClassifier(OnedCnnClassifier):
    def __init__(self, config: Settings = None):
        super().__init__("noz", config=config)
