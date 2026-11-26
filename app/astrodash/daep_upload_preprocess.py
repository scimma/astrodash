"""Single-spectrum wrapper around the exact methods in Preprocess_Universal2.

Wavelength and flux-density unit conversion precedes the original preprocessing.
Metadata detection happens separately and uncertainty is reported per file.
"""
from __future__ import annotations
import numpy as np

GRID = np.arange(3202.5, 9800., 5., dtype=np.float64)

WAVELENGTH_TO_ANGSTROM = {
    'angstrom': 1.,
    'nm': 10.,
    'micron': 1.e4,
    'm': 1.e10,
}
# Multipliers for F_lambda (erg s^-1 cm^-2 A^-1).
FLAMBDA_FACTORS = {
    'flam_cgs_a': 1.,
    'flam_cgs_nm': 0.1,
    'flam_cgs_um': 1.e-4,
    'flam_w_m2_nm': 100.,
    'flam_w_m2_um': 0.1,
    'flam_1e17_cgs_a': 1.e-17,
}
# Multipliers for F_nu (erg s^-1 cm^-2 Hz^-1).
FNU_FACTORS = {
    'jy': 1.e-23,
    'mjy': 1.e-26,
    'ujy': 1.e-29,
    'fnu_cgs_hz': 1.,
}
C_ANGSTROM_PER_SEC = 2.99792458e18


def convert_input_units(wavelength, flux, *, wavelength_unit, flux_unit):
    """Convert declared units to Angstrom and F_lambda before spectral processing.

    'relative_flam' is valid only when values already trace F_lambda vs wavelength.
    Frequency-density (F_nu) input cannot be treated as wavelength-density input.
    """
    if wavelength_unit is None or str(wavelength_unit).strip() == '':
        # No declared unit: infer typical optical scale from the numeric wavelengths.
        # This is a heuristic, not proof of the physical unit.
        raw_x = np.asarray(wavelength, dtype=np.float64).reshape(-1)
        good_x = raw_x[np.isfinite(raw_x) & (raw_x > 0)]
        if good_x.size < 20:
            raise ValueError('Cannot infer wavelength scale: too few positive finite samples')
        typical = float(np.median(good_x))
        if typical >= 2000.:
            wavelength_unit = 'angstrom'
        elif typical >= 100.:
            wavelength_unit = 'nm'
        elif typical >= 0.1:
            wavelength_unit = 'micron'
        else:
            raise ValueError('Wavelength scale is ambiguous; supply wavelengths in Angstrom')
    if wavelength_unit not in WAVELENGTH_TO_ANGSTROM:
        raise ValueError('Unsupported declared wavelength unit; provide wavelengths in Angstrom')
    if flux_unit is None or str(flux_unit).strip() == '':
        flux_unit = 'relative_flam'
    if flux_unit not in (set(FLAMBDA_FACTORS) | set(FNU_FACTORS) | {'relative_flam'}):
        raise ValueError('Unsupported declared flux-density unit; provide relative F_lambda')
    x = np.asarray(wavelength, dtype=np.float64).reshape(-1) * WAVELENGTH_TO_ANGSTROM[wavelength_unit]
    y = np.asarray(flux, dtype=np.float64).reshape(-1)
    if x.shape != y.shape:
        raise ValueError('Wavelength and flux arrays must have the same length')
    if flux_unit in FLAMBDA_FACTORS:
        y = y * FLAMBDA_FACTORS[flux_unit]
    elif flux_unit in FNU_FACTORS:
        if not np.all(np.isfinite(x) & (x > 0)):
            raise ValueError('F_nu conversion requires positive finite wavelengths')
        y = y * FNU_FACTORS[flux_unit] * C_ANGSTROM_PER_SEC / x**2
    # Relative F_lambda is deliberately scale-free; it will be normalized later.
    return x, y


def prepare(wavelength, flux, *, redshift, deredshift,
            input_frame='observer', wavelength_unit=None,
            flux_unit=None, wavelength_medium='air', gap_angstrom=35., mask_telluric=True):
    from astrodash.daep_upload_core import (
        clean_spectrum, trim_zero_flux_edges, trim_to_grid_window,
        vacuum_to_air, sampling_diagnostics,
        resample_to_grid, mask_large_input_gaps, telluric_mask_for_grid,
        parse_ranges, normalize_90th_percentile_abs,
        finalize_normalized_flux,
    )
    if wavelength_medium not in ('air', 'vacuum'):
        raise ValueError('Wavelength medium must be air or vacuum')
    if input_frame not in ('observer','rest'):
        raise ValueError('Input wavelength frame must be explicit')
    z = float(redshift) if redshift is not None else 0.0
    if not np.isfinite(z) or z <= -1 or z > 3:
        raise ValueError('Invalid input redshift')
    if deredshift and redshift is None:
        raise ValueError('Rest-frame processing requires known redshift for telluric masking')
    x, y = convert_input_units(wavelength, flux, wavelength_unit=wavelength_unit, flux_unit=flux_unit)
    if x.size != y.size or x.size < 20:
        raise ValueError('Spectrum must contain at least 20 wavelength/flux pairs')
    # Input wavelengths remain in the frame selected by the user.
    # No redshift, Galactic extinction, or air/vacuum conversion here.
    x, y = clean_spectrum(x, y)
    x, y = trim_zero_flux_edges(x, y)
    x, y = trim_to_grid_window(x, y, z=z, deredshift=bool(deredshift), grid=GRID, margin=50.)
    x, y = clean_spectrum(x, y)
    if x.size < 20:
        raise ValueError('Insufficient samples after model-specific wavelength trimming')
    xr = x/(1+z) if deredshift else x
    # Restrict the uploaded spectrum to >= 3300 Angstrom in the model frame.
    # The cut is applied before resampling, so the discarded blue edge cannot
    # contribute to interpolation or the 90th-percentile flux normalization.
    keep = np.isfinite(xr) & (xr >= 3300.0)
    x, y, xr = x[keep], y[keep], xr[keep]
    if xr.size < 20:
        raise ValueError('Too few measured samples at or above 3300 Angstrom')
    n_good, med_step, _ = sampling_diagnostics(xr)
    if n_good < 5 or (np.isfinite(med_step) and med_step > gap_angstrom):
        raise ValueError('Insufficient spectral sampling')
    f = resample_to_grid(xr, y, GRID)
    f[GRID < 3300.0] = np.nan
    f, _, _ = mask_large_input_gaps(f, GRID, xr, gap_angstrom)
    if mask_telluric:
        bad = telluric_mask_for_grid(GRID, z=z, deredshift=bool(deredshift),
                                     ranges=parse_ranges('6850:6900,7590:7650'))
        f[bad] = np.nan
    fn, scale, ok = normalize_90th_percentile_abs(f)
    if not ok:
        raise ValueError('Cannot normalize using 90th-percentile absolute flux')
    cutflow = {'skip_norm_fail': 0, 'skip_high_outlier_fraction': 0,
               'spectra_with_outlier_bins_masked': 0, 'outlier_bins_masked_total': 0}
    values, valid, n_valid, *_ = finalize_normalized_flux(
        fn, scale, 'upload', cutflow, [], np.nan, 1.0)
    if values is not None:
        # Guarantee the encoder and spectral overlay see no valid blue-side bins.
        valid = np.asarray(valid, dtype=bool).copy()
        values = np.asarray(values).copy()
        valid[GRID < 3300.0] = False
        values[GRID < 3300.0] = 0.0
        n_valid = int(valid.sum())
    if values is None or n_valid < 20:
        raise ValueError('Spectrum failed final mask/coverage checks')
    return GRID.astype(np.float32), values, valid
