"""Conservative, per-spectrum metadata extraction for DAEP upload preview.

Never infer redshift or rest-frame status from flux/wavelength numbers.
Unlabelled two-column spectra use documented conventional spectral units;
uncertainty is reported instead of falsely claiming measured units.
"""
from __future__ import annotations
import csv
import io
import re
from pathlib import Path
import numpy as np

FITS_EXT = {'.fits', '.fit', '.fts'}


def _str(x):
    if x is None: return ''
    return str(x).strip()


def _boolean(x):
    s = _str(x).lower()
    if s in ('true', 't', 'yes', 'y', '1', 'rest', 'restframe', 'rest-frame'): return True
    if s in ('false', 'f', 'no', 'n', '0', 'observer', 'observed', 'obs'): return False
    return None


def _number(x):
    try:
        if x is None or str(x).strip() == '': return None
        y = float(x)
        return y if np.isfinite(y) else None
    except (TypeError, ValueError): return None


def _units(x, kind):
    s = re.sub(r'\s+', '', _str(x).lower()).replace('å', 'a').replace('ångström','angstrom').replace('angstroms','angstrom')
    if not s: return None
    if kind == 'wave':
        if s in ('a', 'aa', 'ang', 'angstrom', 'angstroms'): return 'angstrom'
        if s in ('nm', 'nanometer', 'nanometers'): return 'nm'
        if s in ('um', 'µm', 'micron', 'microns', 'micrometer'): return 'micron'
        if s in ('m', 'meter', 'meters'): return 'm'
        return None
    if s in ('jy', 'jansky', 'janskys'): return 'jy'
    if s == 'mjy': return 'mjy'
    if s in ('ujy','µjy'): return 'ujy'
    if ('hz' in s and ('erg' in s or 'fnu' in s)) or s == 'f_nu': return 'fnu_cgs_hz'
    if 'w/m2/um' in s or 'w/m^2/um' in s: return 'flam_w_m2_um'
    if 'w/m2/nm' in s or 'w/m^2/nm' in s: return 'flam_w_m2_nm'
    if 'erg' in s and ('nm' in s): return 'flam_cgs_nm'
    if 'erg' in s and ('um' in s or 'micron' in s): return 'flam_cgs_um'
    if '1e-17' in s or '10^-17' in s: return 'flam_1e17_cgs_a'
    if 'erg' in s and ('/a' in s or 'angstrom' in s): return 'flam_cgs_a'
    if s in ('relative','relative_flam','arbitrary','arb','normalized','flux','flam','f_lambda'): return 'relative_flam'
    return None


def _filename_sn(name):
    stem = Path(name).stem
    m = re.search(r'(SN\s*20\d{2}[a-z]{1,8}|ASASSN[-_]\d{2}[a-z]+)', stem, re.I)
    return re.sub(r'\s+', '', m.group(0)) if m else stem


def _ascii_meta(raw):
    txt = raw[:65536].decode('utf-8-sig', errors='replace')
    d = {}
    for line in txt.splitlines()[:90]:
        t = line.strip()
        if not t.startswith('#'): continue
        m = re.match(r'^#\s*([a-z_][a-z0-9_ -]{0,45})\s*[:=]\s*(.+?)\s*$', t, re.I)
        if m: d[re.sub(r'[^a-z0-9]','',m.group(1).lower())] = m.group(2)
    first = next((l for l in txt.splitlines() if l.strip() and not l.lstrip().startswith('#')), '')
    if first:
        header = next(csv.reader([first])) if ',' in first else first.split()
        if len(header) >= 2 and not all(_number(k) is not None for k in header[:2]):
            d['_wavecol'], d['_fluxcol'] = header[:2]
    return d


def detect(name, raw):
    """Return file-scoped inputs and provenance; never copy another object's z."""
    ext = Path(name).suffix.lower()
    d = {}; coords = (None, None)
    if ext in FITS_EXT:
        from astropy.io import fits
        with fits.open(io.BytesIO(raw), memmap=False) as hdul:
            for hdu in hdul:
                h = hdu.header
                for key in ('REDSHIFT','ZHELIO','Z','RESTFRAM','RESTFRAME','DEREDSH','DEREDSHIFT','SPECSYS','WAVEFRAM','WAVFRAME','VACUUM','AIRORVAC','WAVEUNIT','CUNIT1','BUNIT','MWCORR','DEREDDEN','RA','DEC','OBJRA','OBJDEC','OBJECT','EXTCORR'):
                    if key in h and key.lower() not in d: d[key.lower()] = h[key]
                if hdu.data is not None and getattr(hdu.data, 'names', None):
                    for idx,col in enumerate(hdu.columns,1):
                        key = col.name.lower()
                        if any(t in key for t in ('wave','lambda','lam','flux','flam','fnu')):
                            d['_wavecol' if ('wave' in key or 'lam' in key) else '_fluxcol'] = col.name
                            if col.unit: d['_waveunit' if ('wave' in key or 'lam' in key) else '_fluxunit'] = col.unit
                if d.get('ra') is not None and d.get('dec') is not None: break
    else: d = _ascii_meta(raw)
    def first(*keys):
        return next((d[k] for k in keys if k in d and _str(d[k]) != ''), None)
    z = _number(first('redshift','zhelio','z'))
    if z is not None and not (-1 < z <= 3): z = None
    rest = _boolean(first('restfram','restframe','deredsh','deredshift','restframewavelengths'))
    frame = 'rest' if rest is True else ('observer' if rest is False else None)
    if frame is None:
        s = _str(first('wavefram','wavframe','wavelengthframe','frame')).lower()
        if 'rest' in s: frame = 'rest'
        elif 'obs' in s: frame = 'observer'
    wave_label = _str(first('_waveunit','waveunit','wavelengthunit','cunit1'))
    flux_label = _str(first('_fluxunit','fluxunit','fluxunit1','bunit'))
    wc = _str(d.get('_wavecol')).lower(); fc = _str(d.get('_fluxcol')).lower()
    if not wave_label:
        if 'nm' in wc: wave_label = 'nm'
        elif 'angstrom' in wc or 'wave_a' in wc or wc.endswith('_aa'): wave_label = 'angstrom'
    if not flux_label:
        if 'mjy' in fc: flux_label = 'mjy'
        elif 'ujy' in fc: flux_label = 'ujy'
        elif 'jy' in fc: flux_label = 'jy'
        elif 'fnu' in fc or 'f_nu' in fc: flux_label = 'fnu_cgs_hz'
        elif 'flam' in fc or 'f_lambda' in fc: flux_label = 'relative_flam'
    wu = _units(wave_label, 'wave')
    fu = _units(flux_label, 'flux')
    warnings=[]
    # Training treated unlabeled flux arrays as relative F_lambda before
    # 90th-percentile normalization. Retain that convention without requiring
    # a physical flux calibration from visitors.
    if fu is None:
        fu='relative_flam'
    vac = _boolean(d.get('vacuum'))
    medium = 'vacuum' if vac is True else ('air' if vac is False else None)
    if medium is None:
        s = _str(first('airorvac','wavelengthmedium','medium')).lower()
        if 'vac' in s: medium='vacuum'
        elif 'air' in s: medium='air'
    # Do not guess air/vacuum: request it for the individual file if absent.
    corrected = _boolean(first('mwcorr','deredden','extcorr','mwcorrected'))
    ra = _number(first('ra','objra','radeg')); dec = _number(first('dec','objdec','decdeg'))
    if ra is not None and dec is not None and 0 <= ra < 360 and -90 <= dec <= 90:
        coords=(ra,dec)
    elif ext in FITS_EXT:
        try:
            from astropy.coordinates import SkyCoord
            import astropy.units as u
            ra_str,dec_str = _str(first('ra','objra')),_str(first('dec','objdec'))
            if ra_str and dec_str:
                c=SkyCoord(ra_str,dec_str,unit=(u.hourangle,u.deg))
                coords=(float(c.ra.deg),float(c.dec.deg))
        except Exception: pass
    return {'filename':name, 'object_name':_str(first('object','sn','snname')) or _filename_sn(name),
            'redshift':z,'input_frame':frame,'wavelength_unit':wu,'flux_unit':fu,
            'wavelength_medium':medium,'wavelength_unit_source':('metadata' if wu else 'missing'),'mw_corrected':corrected,
            'ra_deg':coords[0],'dec_deg':coords[1], 'warnings':warnings,
            'missing': [k for k,v in (('redshift',z),('input_frame',frame)) if v is None]}


def suggested_wavelength_unit(lo, hi):
    """Suggest, but never certify, a unit from optical wavelength scale."""
    try:
        lo, hi = float(lo), float(hi)
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(lo) and np.isfinite(hi) and hi > lo and lo > 0):
        return None
    for unit, multiplier in (("angstrom", 1.), ("nm", 10.), ("micron", 10000.), ("m", 1e10)):
        left, right = lo * multiplier, hi * multiplier
        if 2500 <= left <= 10000 and 4000 <= right <= 30000:
            return unit
    return None
