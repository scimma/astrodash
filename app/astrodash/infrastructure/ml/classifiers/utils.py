"""Helpers shared by the 1D CNN and latent classifiers."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Mapping, Optional

import numpy as np
import torch

from astrodash.config.settings import get_settings


def inference_device() -> torch.device:
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def torch_load(path: str, *, map_location, weights_only: bool):
    kwargs = {"map_location": map_location}
    try:
        return torch.load(path, weights_only=weights_only, **kwargs)
    except TypeError:
        return torch.load(path, **kwargs)


def load_idx_to_label(class_mapping_path: Optional[str]) -> Dict[int, str]:
    if class_mapping_path and os.path.exists(class_mapping_path):
        with open(class_mapping_path, "r") as f:
            mapping = json.load(f)
        return {int(idx): name for name, idx in mapping.items()}
    return dict(get_settings().website_final_idx_to_label())


def classification_from_logits(
    logits: torch.Tensor,
    idx_to_label: Mapping[int, str],
    redshift: float,
    top_k: int = 3,
) -> dict:
    probs = torch.softmax(logits, dim=-1).detach().cpu().numpy()[0]
    top_indices = np.argsort(probs)[::-1][:top_k]
    matches = []
    for idx in top_indices:
        class_name = idx_to_label.get(int(idx))
        if class_name is None:
            fallback = get_settings().website_final_idx_to_label()
            class_name = fallback.get(int(idx), f"unknown_class_{idx}")
        matches.append(
            {
                "type": class_name,
                "probability": float(probs[idx]),
                "redshift": redshift,
                "rlap": None,
                "reliable": bool(probs[idx] > 0.5),
            }
        )
    best_match = matches[0] if matches else {}
    return {
        "best_matches": matches,
        "best_match": best_match,
        "reliable_matches": best_match.get("reliable", False) if best_match else False,
    }


def spectrum_redshift(spectrum: Any) -> float:
    return float(getattr(spectrum, "redshift", 0.0) or 0.0)


def preprocess_redshift(spectrum: Any, required: bool) -> Optional[float]:
    """Return z for website_final preprocess, or None if a required z is missing."""
    redshift = getattr(spectrum, "redshift", None)
    if required and redshift is None:
        return None
    return float(redshift) if redshift is not None else 0.0
