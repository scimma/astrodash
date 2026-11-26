"""Frozen DAEP inference for already-preprocessed spectra.

Preprocessing contract: wave is precisely the model's training wavelength grid;
flux is *the model-specific normalized, dereddened/resampled flux*; valid=True
indicates an observed, unmasked wavelength bin. Do not feed unprocessed spectra.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
import json
import numpy as np
import torch


@dataclass(frozen=True)
class RunPaths:
    name: str
    path: Path


class DaepIndex:
    def __init__(self, run: RunPaths, repo: Path, device: str = 'cpu'):
        self.run = run
        self.device = torch.device(device)
        self.lock = Lock()
        import sys
        if str(repo) not in sys.path: sys.path.insert(0, str(repo))
        from TwinsModel_Universal import repo_import_setup
        if (repo / "package").is_dir(): repo_import_setup(str(repo))
        from TwinsModel_Universal import build_daep
        # Reuse the actual phase-off implementation given by the user.
        from astrodash.daep_phase_off import patch_model_no_phase

        ckpt_path = run.path / 'best_ckpt.pt'
        ckpt = torch.load(ckpt_path, map_location=self.device, weights_only=False)
        if not {'cfg', 'state_dict'} <= ckpt.keys():
            raise ValueError(f'{ckpt_path} missing cfg or state_dict')
        cfg = ckpt['cfg']
        self.use_phase = bool(ckpt.get('use_phase', False))
        latent_dim = int(cfg['bottleneck_length']) * int(cfg['bottleneck_dim'])
        if latent_dim != 1024:
            raise ValueError(f'Expected 1024-D checkpoint, got {latent_dim}')
        model = build_daep(cfg)
        if not self.use_phase:
            model = patch_model_no_phase(model)
        model.load_state_dict(ckpt['state_dict'], strict=True)
        self.model = model.to(self.device).eval()

        # npz is read-only; none of its arrays are mutated or appended to.
        with np.load(run.path / 'latent_raw_z_best.npz', allow_pickle=False) as data:
            if 'z' not in data:
                raise ValueError('Reference latents have no z array')
            self.ref_z = np.asarray(data['z'], dtype=np.float32).reshape(-1, 1024)
        norm = np.linalg.norm(self.ref_z, axis=1)
        self.ref_ok = np.isfinite(norm) & (norm > 0)
        self.ref_unit = np.zeros_like(self.ref_z, dtype=np.float32)
        self.ref_unit[self.ref_ok] = self.ref_z[self.ref_ok] / norm[self.ref_ok, None]
        if not np.any(self.ref_ok):
            raise ValueError('No valid reference latent vectors')

    def encode(self, wave: np.ndarray, flux: np.ndarray, valid: np.ndarray, *, phase: float | None = None) -> np.ndarray:
        wave = np.asarray(wave, dtype=np.float32).reshape(-1)
        flux = np.asarray(flux, dtype=np.float32).reshape(-1)
        valid = np.asarray(valid, dtype=bool).reshape(-1)
        if wave.shape != flux.shape or flux.shape != valid.shape:
            raise ValueError('wave, flux and validity mask must have the same length')
        if wave.size != 1320:
            raise ValueError(f'Expected 1320 training bins, received {wave.size}')
        expected = np.arange(3202.5, 9800, 5, dtype=np.float32)
        if not np.allclose(wave, expected, rtol=0, atol=1.e-3):
            raise ValueError('Wavelength grid does not match the 3202.5–9797.5 Å training bin centers')
        if np.count_nonzero(valid) < 10:
            raise ValueError('Fewer than 10 valid bins')
        if not np.isfinite(flux).all():
            raise ValueError('Flux must be finite everywhere, including masked bins')
        if self.use_phase and (phase is None or not np.isfinite(phase)):
            raise ValueError('This checkpoint requires a numeric phase')
        batch = {
            'flux': torch.from_numpy(flux[None]).to(self.device),
            'wavelength': torch.from_numpy(wave[None]).to(self.device),
            # Injector: stored mask True=valid; model expects True=ignored.
            'mask': torch.from_numpy((~valid)[None]).to(self.device),
            'phase': torch.tensor([phase if phase is not None else float('nan')],
                                  dtype=torch.float32, device=self.device),
        }
        with self.lock, torch.inference_mode():
            z = self.model.encoder.encode_raw(batch)
            out = z.detach().float().cpu().numpy().reshape(-1)
        if out.size != 1024 or not np.isfinite(out).all():
            raise ValueError('Encoder returned an invalid 1024-D representation')
        return out

    def twins(self, z: np.ndarray, k: int = 5, exclude_indices: tuple[int, ...] = ()) -> list[dict]:
        query = np.asarray(z, dtype=np.float32).reshape(-1)
        if query.size != 1024 or not np.isfinite(query).all():
            raise ValueError('Query must be a finite 1024-D vector')
        norm = float(np.linalg.norm(query))
        if not norm > 0:
            raise ValueError('Zero-length query embedding')
        scores = self.ref_unit @ (query / norm)
        scores[~self.ref_ok] = -np.inf
        for idx in exclude_indices:
            if 0 <= idx < len(scores):
                scores[idx] = -np.inf
        eligible = np.flatnonzero(np.isfinite(scores))
        k = min(int(k), len(eligible))
        if k <= 0:
            return []
        selected = eligible[np.argpartition(scores[eligible], -k)[-k:]]
        selected = selected[np.argsort(-scores[selected], kind='stable')]
        return [{'reference_index': int(i), 'cosine_similarity': float(scores[i])} for i in selected]


class QueryManager:
    """In-memory, per-process model cache. No uploaded spectra are persisted here."""
    def __init__(self, roots: dict[str, Path], repo: Path, device: str = 'cpu'):
        self.roots = {name: Path(path) for name, path in roots.items()}
        self.repo = Path(repo)
        self.device = device
        self._models: dict[str, DaepIndex] = {}
        self._lock = Lock()

    def get(self, name: str) -> DaepIndex:
        if name not in self.roots:
            raise ValueError(f'Unknown DAEP model: {name}')
        with self._lock:
            if name not in self._models:
                self._models[name] = DaepIndex(RunPaths(name, self.roots[name]), self.repo, self.device)
            return self._models[name]
