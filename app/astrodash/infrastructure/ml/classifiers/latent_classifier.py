"""Latent DAEP encoder + flatten MLP classifiers (latent_z / latent_noz).

Pairing is hardcoded: Dered encoder Settings paths only with latent_z;
Nodered only with latent_noz.

Classify from observed ``spectrum.x`` / ``spectrum.y`` (and redshift for
latent_z). ``LatentEncoderSpectrumProcessor`` resamples onto the 1320-bin
3200–9800 Å grid and builds the DAEP ignore mask from coverage
(``mask = ~np.isfinite(flux_on_grid)``). Callers never pass a mask.
Phase is always zeros (current SpectraLayers still appends a phase token).
Redshift is not an encoder input; z vs no-z is which checkpoint/preprocess.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Literal, Optional

import numpy as np
import torch
from torch import nn

from astrodash.config.logging import get_logger
from astrodash.config.settings import Settings, get_settings
from astrodash.infrastructure.ml.classifiers.base import BaseClassifier
from astrodash.infrastructure.ml.classifiers.daep.build import build_daep
from astrodash.infrastructure.ml.classifiers.utils import (
    classification_from_logits,
    inference_device,
    load_idx_to_label,
    preprocess_redshift,
    spectrum_redshift,
    torch_load,
)

logger = get_logger(__name__)

LatentVariant = Literal["z", "noz"]


class _LatentFlattenMlp(nn.Module):
    """train_latent.LatentClassifier (kind latent_flatten_mlp)."""

    def __init__(
        self,
        embed_dim: int,
        n_cls: int,
        head_hidden: int,
        head_dropout: float,
    ):
        super().__init__()
        self.head = nn.Sequential(
            nn.Linear(embed_dim, head_hidden),
            nn.GELU(),
            nn.Dropout(head_dropout),
            nn.Linear(head_hidden, n_cls),
        )

    def forward(self, batch: Dict[str, torch.Tensor]) -> torch.Tensor:
        z_batch = batch["z"]
        return self.head(z_batch.reshape(z_batch.shape[0], -1))


class LatentSpectrumClassifier(BaseClassifier):
    def __init__(self, variant: LatentVariant, config: Settings = None):
        super().__init__(config)
        self.config = config or get_settings()
        self.variant = variant
        self.device = inference_device()
        self.encoder = None
        self.model = None
        if variant == "z":
            # Dered encoder is paired only with latent_z.
            self.encoder_path = self.config.latent_z_encoder_path
            self.classifier_path = self.config.latent_z_classifier_path
            mapping_path = None
        else:
            # Nodered encoder is paired only with latent_noz.
            self.encoder_path = self.config.latent_noz_encoder_path
            self.classifier_path = self.config.latent_noz_classifier_path
            mapping_path = None
        self.idx_to_label = load_idx_to_label(mapping_path)
        self._processor = None
        self._load_models()

    def _load_models(self) -> None:
        if not self.encoder_path or not os.path.exists(self.encoder_path):
            logger.error("Latent encoder not found at %s", self.encoder_path)
            return
        if not self.classifier_path or not os.path.exists(self.classifier_path):
            logger.error("Latent classifier not found at %s", self.classifier_path)
            return
        try:
            ckpt = torch_load(
                self.encoder_path, map_location=self.device, weights_only=False
            )
            cfg = ckpt.get("cfg") or self.config.latent_encoder_ctor_kwargs()
            encoder = build_daep(cfg).to(self.device)
            encoder.load_state_dict(ckpt["state_dict"], strict=True)
            encoder.eval()
            self.encoder = encoder

            payload = torch_load(
                self.classifier_path, map_location=self.device, weights_only=False
            )
            mlp_cfg = payload.get("cfg") or {}
            latent_shape = mlp_cfg.get("latent_shape")
            expected_flat = self.config.latent_encoder_latent_flat()
            if latent_shape and len(latent_shape) >= 2:
                embed_dim = int(np.prod(latent_shape[1:]))
            else:
                embed_dim = expected_flat
            if embed_dim != expected_flat:
                logger.warning(
                    "MLP embed_dim %s != expected %s", embed_dim, expected_flat
                )
            n_cls = len(self.config.website_final_label_mapping)
            head = _LatentFlattenMlp(
                embed_dim=embed_dim,
                n_cls=n_cls,
                head_hidden=int(mlp_cfg.get("ff_dim", self.config.latent_mlp_head_hidden)),
                head_dropout=float(
                    mlp_cfg.get("dropout", self.config.latent_mlp_head_dropout)
                ),
            ).to(self.device)
            head.load_state_dict(payload["model_state_dict"], strict=True)
            head.eval()
            self.model = head

            class_names = payload.get("class_names")
            class_to_idx = payload.get("class_to_idx")
            if class_to_idx:
                self.idx_to_label = {int(v): k for k, v in class_to_idx.items()}
            elif class_names:
                self.idx_to_label = {i: name for i, name in enumerate(class_names)}

            logger.debug(
                "Loaded latent_%s encoder=%s classifier=%s",
                self.variant,
                self.encoder_path,
                self.classifier_path,
            )
        except Exception as e:
            logger.error("Failed to load latent encoder/MLP: %s", e)
            self.encoder = None
            self.model = None

    def _processor_instance(self):
        if self._processor is None:
            from astrodash.infrastructure.ml.data_processor import (
                LatentEncoderSpectrumProcessor,
            )

            self._processor = LatentEncoderSpectrumProcessor()
        return self._processor

    def _preprocess_from_xy(self, spectrum: Any) -> Optional[Dict[str, Any]]:
        x = np.asarray(getattr(spectrum, "x", []), dtype=np.float64).reshape(-1)
        y = np.asarray(getattr(spectrum, "y", []), dtype=np.float64).reshape(-1)
        if x.size < 2 or x.size != y.size:
            return None
        deredshift = self.variant == "z"
        redshift = preprocess_redshift(spectrum, required=deredshift)
        if redshift is None:
            logger.error("Redshift is required for the latent z-variant")
            return None
        try:
            return self._processor_instance().process(
                x, y, redshift, deredshift=deredshift
            )
        except Exception as e:
            logger.error("Latent encoder preprocessing failed: %s", e)
            return None

    def _batch_from_spectrum(self, spectrum: Any) -> Optional[Dict[str, torch.Tensor]]:
        processed = self._preprocess_from_xy(spectrum)
        if processed is None:
            logger.error("No processable spectrum.x/y for latent classify")
            return None
        flux = np.asarray(processed["flux"], dtype=np.float32).reshape(-1)
        wavelength = np.asarray(processed["wavelength"], dtype=np.float32).reshape(-1)
        mask = np.asarray(processed["mask"]).reshape(-1).astype(bool)
        if (
            flux.size != self.config.latent_encoder_n_wave
            or wavelength.size != self.config.latent_encoder_n_wave
            or mask.size != self.config.latent_encoder_n_wave
        ):
            logger.error(
                "Latent classify expects length-%s flux/wavelength/mask (got %s, %s, %s)",
                self.config.latent_encoder_n_wave,
                flux.size,
                wavelength.size,
                mask.size,
            )
            return None
        return {
            "flux": torch.tensor(flux, dtype=torch.float32).unsqueeze(0).to(self.device),
            "wavelength": torch.tensor(wavelength, dtype=torch.float32)
            .unsqueeze(0)
            .to(self.device),
            "mask": torch.tensor(mask, dtype=torch.bool).unsqueeze(0).to(self.device),
            "phase": torch.zeros(1, dtype=torch.float32, device=self.device),
        }

    def classify_sync(self, spectrum: Any) -> dict:
        if self.encoder is None or self.model is None:
            logger.error("Latent model is not loaded. Returning empty result.")
            return {}
        try:
            batch = self._batch_from_spectrum(spectrum)
            if batch is None:
                return {}
            redshift = spectrum_redshift(spectrum)
            with torch.no_grad():
                latent = self.encoder.encode(batch)
                logits = self.model({"z": latent})
            return classification_from_logits(logits, self.idx_to_label, redshift)
        except Exception as e:
            logger.error("Error during latent classification: %s", e)
            return {}

    async def classify(self, spectrum: Any) -> dict:
        import asyncio

        return await asyncio.to_thread(self.classify_sync, spectrum)


class LatentZClassifier(LatentSpectrumClassifier):
    def __init__(self, config: Settings = None):
        super().__init__("z", config=config)


class LatentNozClassifier(LatentSpectrumClassifier):
    def __init__(self, config: Settings = None):
        super().__init__("noz", config=config)
