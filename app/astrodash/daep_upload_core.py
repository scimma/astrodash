"""Upload preprocessing kernels taken from Preprocess_Universal2.

This module contains the same non-MW functions without importing the original
batch pipeline or unrelated dust-map dependencies.
"""
from __future__ import annotations
from typing import List, Tuple
import numpy as np
DEFAULT_MIN_FINITE_NORM = 50


def parse_ranges(s: str) -> List[Tuple[float, float]]:
    ranges = []
    if s is None or str(s).strip() == "":
        return ranges
    for part in str(s).split(","):
        part = part.strip()
        if not part:
            continue
        a, b = part.split(":")
        lo = float(a)
        hi = float(b)
        if hi <= lo:
            raise ValueError(f"Bad range: {part}")
        ranges.append((lo, hi))
    return ranges

def telluric_mask_for_grid(grid: np.ndarray, z: float, deredshift: bool, ranges: List[Tuple[float, float]]) -> np.ndarray:
    if not ranges:
        return np.zeros_like(grid, dtype=bool)
    obs_wave_for_grid = grid * (1.0 + z) if deredshift else grid
    bad = np.zeros_like(grid, dtype=bool)
    for lo, hi in ranges:
        bad |= (obs_wave_for_grid >= lo) & (obs_wave_for_grid <= hi)
    return bad

def vacuum_to_air(lam_vac: np.ndarray) -> np.ndarray:
    lam_air = np.asarray(lam_vac, dtype=np.float64).copy()
    good = np.isfinite(lam_air) & (lam_air > 2000.0)
    if np.any(good):
        s2 = (1e4 / lam_air[good]) ** 2
        n = 1.0 + 0.0000834254 + 0.02406147 / (130.0 - s2) + 0.00015998 / (38.9 - s2)
        lam_air[good] = lam_air[good] / n
    return lam_air

def clean_spectrum(lam: np.ndarray, flx: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    good = np.isfinite(lam) & np.isfinite(flx)
    lam = np.asarray(lam[good], dtype=np.float64)
    flx = np.asarray(flx[good], dtype=np.float64)

    if lam.size < 2:
        return lam, flx

    order = np.argsort(lam)
    lam = lam[order]
    flx = flx[order]

    uniq = np.concatenate([[True], lam[1:] > lam[:-1]])
    return lam[uniq], flx[uniq]

def trim_zero_flux_edges(lam: np.ndarray, flx: np.ndarray, frac: float = 1e-4, min_keep: int = 20) -> Tuple[np.ndarray, np.ndarray]:
    lam = np.asarray(lam, dtype=np.float64)
    flx = np.asarray(flx, dtype=np.float64)

    good = np.isfinite(lam) & np.isfinite(flx)
    lam = lam[good]
    flx = flx[good]

    if flx.size < min_keep:
        return lam, flx

    amp = np.nanpercentile(np.abs(flx), 95.0)
    if not np.isfinite(amp) or amp <= 0:
        return lam, flx

    real = np.abs(flx) > float(frac) * amp
    if int(real.sum()) < min_keep:
        return lam, flx

    i0 = int(np.argmax(real))
    i1 = int(len(real) - np.argmax(real[::-1]))

    return lam[i0:i1], flx[i0:i1]

def trim_to_grid_window(lam_obs: np.ndarray, flx: np.ndarray, z: float, deredshift: bool, grid: np.ndarray, margin: float = 50.0) -> Tuple[np.ndarray, np.ndarray]:
    lam_frame = lam_obs / (1.0 + z) if deredshift else lam_obs
    lo = float(grid.min()) - float(margin)
    hi = float(grid.max()) + float(margin)

    keep = (
        np.isfinite(lam_obs)
        & np.isfinite(flx)
        & np.isfinite(lam_frame)
        & (lam_frame >= lo)
        & (lam_frame <= hi)
    )

    return lam_obs[keep], flx[keep]

def resample_to_grid(lam: np.ndarray, flx: np.ndarray, grid: np.ndarray) -> np.ndarray:
    from astropy import units as u
    from specutils import Spectrum
    from specutils.manipulation import FluxConservingResampler
    spec = Spectrum(spectral_axis=lam * u.AA, flux=flx * u.dimensionless_unscaled)
    out = FluxConservingResampler()(spec, grid * u.AA)
    f = out.flux.value
    if np.ma.isMaskedArray(f):
        f = np.asarray(f.filled(np.nan), dtype=np.float64)
    else:
        f = np.asarray(f, dtype=np.float64)
    f[grid < lam.min()] = np.nan
    f[grid > lam.max()] = np.nan
    return f

def sampling_diagnostics(lam: np.ndarray) -> Tuple[int, float, float]:
    lam = np.asarray(lam, dtype=np.float64)
    dlam = np.diff(lam)
    good = np.isfinite(dlam) & (dlam > 0)
    if int(good.sum()) == 0:
        return 0, np.nan, np.nan
    return int(good.sum()), float(np.nanmedian(dlam[good])), float(np.nanmax(dlam[good]))

def mask_large_input_gaps(
    f_resamp: np.ndarray,
    grid: np.ndarray,
    lam_input: np.ndarray,
    max_gap_angstrom: float,
) -> Tuple[np.ndarray, int, float]:
    """Mask grid bins that fall inside large gaps in the original spectrum.

    FluxConservingResampler treats the input spectrum as a continuous sequence
    between the first and last wavelength. If the original spectrum has large
    chip gaps, segment gaps, or pathological sparse sampling, the resampler can
    interpolate across regions where no data actually exist. This function
    restores those gaps by setting affected resampled bins to NaN before
    normalization and storage.
    """
    f_out = np.array(f_resamp, dtype=np.float64, copy=True)

    if max_gap_angstrom is None or not np.isfinite(max_gap_angstrom) or max_gap_angstrom <= 0:
        return f_out, 0, np.nan

    lam_input = np.asarray(lam_input, dtype=np.float64)
    lam_input = lam_input[np.isfinite(lam_input)]

    if lam_input.size < 2:
        return f_out, 0, np.nan

    lam_input = np.unique(np.sort(lam_input))
    gaps = np.diff(lam_input)
    large = np.where(gaps > float(max_gap_angstrom))[0]

    masked = np.zeros_like(grid, dtype=bool)

    for j in large:
        lo = lam_input[j]
        hi = lam_input[j + 1]
        masked |= (grid > lo) & (grid < hi)

    n_masked = int(np.isfinite(f_out[masked]).sum())
    f_out[masked] = np.nan

    max_gap = float(np.nanmax(gaps)) if gaps.size else np.nan
    return f_out, n_masked, max_gap

def normalize_90th_percentile_abs(flux: np.ndarray, min_finite: int = DEFAULT_MIN_FINITE_NORM, eps: float = 1e-20) -> Tuple[np.ndarray, float, bool]:
    finite = np.isfinite(flux)
    if int(finite.sum()) < min_finite:
        return np.full_like(flux, np.nan), np.nan, False
    scale = float(np.nanpercentile(np.abs(flux[finite]), 90.0))
    if (not np.isfinite(scale)) or scale < eps:
        return np.full_like(flux, np.nan), np.nan, False
    return flux / scale, scale, True

def finalize_normalized_flux(f_norm, scale, spec_id, cutflow, report_rows, outlier_mask_abs, max_outlier_mask_frac):
    """
    Prepare final saved flux and mask.

   outlier_mask_abs is the normalized-flux threshold beyond which bins are MASKED.

    Behavior when a finite positive threshold is supplied:
    - no bins exceed the threshold:
        keep the spectrum unchanged;
    - at least one bin exceeds the threshold, but the affected fraction is
      <= max_outlier_mask_frac:
        set only those bins to NaN so they become mask=False;
    - the affected fraction is > max_outlier_mask_frac:
        reject the whole spectrum as broadly pathological.

    Important:
    - np.nan in f_norm means no usable measurement exists at that wavelength.
    - valid=True means this wavelength bin is a real usable measurement.
    - valid=False means missing, padded, telluric, gap-masked, or outlier-masked.
    - flux.npy is kept finite for DAEP stability; invalid bins are written as 0.0,
      while mask.npy remains the source of truth.

    Returns
    -------
    f_store, valid, n_valid, n_outlier_bins_masked, outlier_fraction
    """
    f_store_work = np.array(f_norm, dtype=np.float64, copy=True)

    finite_before = np.isfinite(f_store_work)
    n_finite_before = int(finite_before.sum())

    if n_finite_before == 0:
        cutflow["skip_norm_fail"] += 1
        report_rows.append({
            "spec_id": spec_id,
            "status": "skip_no_finite_after_norm",
            "norm_scale": scale,
        })
        return None, None, None, None, None

    n_outlier_bins_masked = 0
    outlier_fraction = 0.0

    if outlier_mask_abs is not None and np.isfinite(outlier_mask_abs) and outlier_mask_abs > 0:
        over_threshold = finite_before & (np.abs(f_store_work) > float(outlier_mask_abs))
        n_outlier_bins_masked = int(over_threshold.sum())
        outlier_fraction = (
            float(n_outlier_bins_masked) / float(n_finite_before)
            if n_finite_before > 0
            else 0.0
        )

        if outlier_fraction > float(max_outlier_mask_frac):
            cutflow["skip_high_outlier_fraction"] = (
                cutflow.get("skip_high_outlier_fraction", 0) + 1
            )
            report_rows.append({
                "spec_id": spec_id,
                "status": "skip_high_outlier_fraction",
                "norm_scale": scale,
                "outlier_mask_abs": float(outlier_mask_abs),
                "n_finite_before_outlier_mask": n_finite_before,
                "n_outlier_bins": n_outlier_bins_masked,
                "outlier_fraction": outlier_fraction,
                "max_outlier_fraction": float(max_outlier_mask_frac),
            })
            return None, None, None, None, None

        if n_outlier_bins_masked > 0:


            f_store_work[over_threshold] = np.nan
            cutflow["spectra_with_outlier_bins_masked"] = (
                cutflow.get("spectra_with_outlier_bins_masked", 0) + 1
            )
            cutflow["outlier_bins_masked_total"] = (
                cutflow.get("outlier_bins_masked_total", 0)
                + n_outlier_bins_masked
            )

    valid = np.isfinite(f_store_work)
    n_valid = int(valid.sum())

    f_store = np.nan_to_num(
        f_store_work,
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    ).astype(np.float32)

    if np.isinf(f_store).any() or np.isnan(f_store).any():
        cutflow["skip_norm_fail"] += 1
        report_rows.append({
            "spec_id": spec_id,
            "status": "skip_nonfinite_after_store",
            "norm_scale": scale,
        })
        return None, None, None, None, None

    return (
        f_store,
        valid.astype(bool),
        n_valid,
        n_outlier_bins_masked,
        outlier_fraction,
    )
