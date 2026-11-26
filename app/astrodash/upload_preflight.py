"""Proposed AstroDash spectral-twins upload preflight (REVIEW ONLY).

No model inference.  Does not mutate reference bundles or application state.
All results include machine-readable failure reasons for every file.
"""
from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass, asdict
from pathlib import Path, PurePosixPath
from typing import Any
import zipfile

import numpy as np

SUPPORTED = {'.txt', '.dat', '.flm', '.csv', '.fits', '.fit', '.fts'}
MAX_FILE_BYTES = 40 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 250


@dataclass
class PreflightResult:
    filename: str
    status: str
    reason: str
    n_samples: int = 0
    wavelength_min: float | None = None
    wavelength_max: float | None = None
    n_finite: int = 0
    details: str = ''

    def json(self) -> dict[str, Any]:
        return asdict(self)


def _parse_ascii(raw: bytes, ext: str) -> tuple[np.ndarray, np.ndarray]:
    txt = raw.decode('utf-8-sig', errors='replace')
    if ext == '.csv':
        # Detect column names and common alternative headers. A two-column
        # numeric CSV without header is also supported.
        lines = [l for l in txt.splitlines() if l.strip() and not l.lstrip().startswith('#')]
        if not lines:
            raise ValueError('Empty CSV')
        tokens = next(csv.reader([lines[0]]))
        has_header = not all(_is_number(v) for v in tokens[:2])
        if has_header:
            reader = csv.DictReader(io.StringIO('\n'.join(lines)))
            names = {str(k).casefold().strip(): k for k in (reader.fieldnames or [])}
            wave_key = next((names[k] for k in ('wavelength', 'wave', 'lambda', 'lam', 'angstrom', 'wavelength_angstrom') if k in names), None)
            flux_key = next((names[k] for k in ('flux', 'flx', 'f_lambda', 'flam', 'flux_density') if k in names), None)
            if not wave_key or not flux_key:
                raise ValueError('CSV header needs wavelength/wave/lambda and flux columns')
            pairs = [(r.get(wave_key), r.get(flux_key)) for r in reader]
            x = np.array([[float(a), float(b)] for a, b in pairs], dtype=float)
        else:
            x = np.genfromtxt(io.StringIO('\n'.join(lines)), delimiter=',', dtype=float, invalid_raise=False)
    else:
        x = np.genfromtxt(io.StringIO(txt), comments='#', dtype=float, invalid_raise=False)
    x = np.atleast_2d(x)
    if x.shape[1] < 2:
        raise ValueError('Expected at least two columns: wavelength and flux')
    return x[:, 0], x[:, 1]


def _is_number(v: str) -> bool:
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


def _parse_fits(raw: bytes) -> tuple[np.ndarray, np.ndarray]:
    try:
        from astropy.io import fits
    except ImportError as exc:
        raise RuntimeError('FITS_READER_UNAVAILABLE: install astropy') from exc
    with fits.open(io.BytesIO(raw), memmap=False) as hdul:
        for hdu in hdul:
            data = hdu.data
            if data is None:
                continue
            # FITS binary tables with two named columns
            if getattr(data, 'names', None):
                names = {s.lower(): s for s in data.names}
                wk = next((names[s] for s in ('wavelength', 'wave', 'lambda', 'lam') if s in names), None)
                fk = next((names[s] for s in ('flux', 'flam', 'f_lambda') if s in names), None)
                if wk and fk:
                    return np.asarray(data[wk]).ravel(), np.asarray(data[fk]).ravel()
            arr = np.asarray(data)
            # 1D WCS from CRVAL1/CDELT1/CRPIX1 or CD1_1.
            if arr.ndim == 1 and np.issubdtype(arr.dtype, np.number):
                hdr = hdu.header
                if 'CRVAL1' in hdr and ('CDELT1' in hdr or 'CD1_1' in hdr):
                    cdelt = float(hdr.get('CDELT1', hdr.get('CD1_1')))
                    crpix = float(hdr.get('CRPIX1', 1.0))
                    pix = np.arange(arr.size, dtype=float) + 1
                    wav = float(hdr['CRVAL1']) + (pix - crpix) * cdelt
                    if str(hdr.get('CTYPE1', '')).upper().startswith('LOG'):
                        raise ValueError('LOG_WCS_UNSUPPORTED: wavelength transform requires explicit handling')
                    return wav, arr.astype(float)
        raise ValueError('FITS_WAVELENGTH_UNKNOWN: no supported table columns or linear spectral WCS')


def inspect_spectrum(filename: str, raw: bytes) -> PreflightResult:
    ext = Path(filename).suffix.lower()
    result = PreflightResult(filename=filename, status='rejected', reason='')
    if ext not in SUPPORTED:
        result.reason = 'UNSUPPORTED_EXTENSION'
        return result
    if len(raw) > MAX_FILE_BYTES:
        result.reason = 'FILE_TOO_LARGE'
        return result
    if not raw:
        result.reason = 'EMPTY_FILE'
        return result
    try:
        wave, flux = _parse_fits(raw) if ext in {'.fits','.fit','.fts'} else _parse_ascii(raw, ext)
        wave, flux = np.asarray(wave, float).ravel(), np.asarray(flux, float).ravel()
        result.n_samples = len(wave)
        if wave.shape != flux.shape or len(wave) < 20:
            raise ValueError('INSUFFICIENT_SAMPLES_OR_SHAPE_MISMATCH')
        valid = np.isfinite(wave) & np.isfinite(flux)
        result.n_finite = int(valid.sum())
        if result.n_finite < 20:
            raise ValueError('INSUFFICIENT_FINITE_WAVELENGTH_FLUX_PAIRS')
        wav = wave[valid]
        result.wavelength_min, result.wavelength_max = float(wav.min()), float(wav.max())
        if np.any(wav <= 0):
            raise ValueError('NONPOSITIVE_WAVELENGTH')
        if len(np.unique(wav)) < 20:
            raise ValueError('TOO_FEW_UNIQUE_WAVELENGTHS')
        if not np.any(np.abs(flux[valid]) > 0):
            raise ValueError('ZERO_FLUX')
        # Do not infer units from values. Actual normalization, WCS/frame, dust
        # correction, gap/telluric masks etc. happen in later processing.
        result.status, result.reason = 'parsed', 'READY_FOR_MODEL_SPECIFIC_PREPROCESSING'
        result.details = 'Wavelength units, rest/observed frame, and dust correction unverified'
    except Exception as exc:
        result.reason = str(exc)[:400]
    return result


def inspect_upload(filename: str, raw: bytes) -> list[PreflightResult]:
    """Read single upload or ZIP; reject traversal, nested zips, encrypted and oversized members."""
    if Path(filename).suffix.lower() != '.zip':
        return [inspect_spectrum(filename, raw)]
    results: list[PreflightResult] = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            members = [m for m in z.infolist() if not m.is_dir()]
            if len(members) > MAX_MEMBERS:
                return [PreflightResult(filename, 'rejected', 'TOO_MANY_ZIP_MEMBERS')]
            if sum(m.file_size for m in members) > MAX_TOTAL_BYTES:
                return [PreflightResult(filename, 'rejected', 'ZIP_UNCOMPRESSED_SIZE_LIMIT')]
            for info in members:
                name = info.filename
                path = PurePosixPath(name)
                if (path.is_absolute() or '..' in path.parts or '\\' in name or info.flag_bits & 1):
                    results.append(PreflightResult(name, 'rejected', 'UNSAFE_OR_ENCRYPTED_ZIP_ENTRY'))
                    continue
                if path.name.startswith('.') or any(p.startswith('__MACOSX') for p in path.parts):
                    continue
                if path.suffix.lower() == '.zip':
                    results.append(PreflightResult(name, 'rejected', 'NESTED_ZIP_NOT_SUPPORTED'))
                    continue
                if path.suffix.lower() not in SUPPORTED:
                    results.append(PreflightResult(name, 'rejected', 'UNSUPPORTED_EXTENSION'))
                    continue
                if info.file_size > MAX_FILE_BYTES:
                    results.append(PreflightResult(name, 'rejected', 'FILE_TOO_LARGE'))
                    continue
                try:
                    results.append(inspect_spectrum(name, z.read(info)))
                except Exception as exc:
                    results.append(PreflightResult(name, 'rejected', f'ZIP_READ_FAILED: {exc}'))
    except (zipfile.BadZipFile, OSError) as exc:
        return [PreflightResult(filename, 'rejected', f'INVALID_ZIP: {exc}')]
    return results or [PreflightResult(filename, 'rejected', 'ZIP_HAS_NO_FILES')]


def inspect_many(files: list[tuple[str, bytes]]) -> list[dict[str, Any]]:
    """Multiple independent uploads; a bad member never rejects good neighbors."""
    return [record.json() for name, payload in files for record in inspect_upload(name, payload)]
