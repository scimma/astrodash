"""Transient uploaded-spectrum inference against validated DAEP reference bundles."""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path
import io
import json
import re
import os
import zipfile
import numpy as np
from astrodash.upload_preflight import (
    SUPPORTED, MAX_FILE_BYTES, MAX_TOTAL_BYTES, MAX_MEMBERS,
    _parse_ascii, _parse_fits, inspect_spectrum,
)
from astrodash.daep_upload_metadata import detect, _units, suggested_wavelength_unit

RUNS = {
    'rest_frame': Path('/mnt/daep-models/rest'),
    'observer_frame': Path('/mnt/daep-models/observer'),
    'multi_object_rest': Path('/mnt/daep-models/multi'),
}
BUNDLES = {
    'rest_frame': Path('/mnt/astrodash-data/explorer/daep'),
    'observer_frame': Path('/mnt/astrodash-data/explorer/daep_observer_frame'),
    'multi_object_rest': Path('/mnt/astrodash-data/explorer/daep_multi_object'),
}

def file_rows(files):
    """Yield individual files and ZIP members, enforcing preflight resource limits."""
    total=0
    for name, raw in files:
        total += len(raw)
        if total > MAX_TOTAL_BYTES: raise ValueError('Maximum upload size is 256 MB')
        if Path(name).suffix.lower() != '.zip':
            yield name, raw
            continue
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos=[m for m in archive.infolist() if not m.is_dir()]
            if len(infos)>MAX_MEMBERS or sum(m.file_size for m in infos)>MAX_TOTAL_BYTES:
                raise ValueError('ZIP exceeds member count or uncompressed size limit')
            for item in infos:
                nm=item.filename
                if nm.startswith('/') or '..' in Path(nm).parts or '\\' in nm or item.flag_bits&1:
                    yield nm, b''
                elif Path(nm).suffix.lower() in SUPPORTED:
                    yield nm, archive.read(item) if item.file_size<=MAX_FILE_BYTES else b''

def preview(files):
    rows=[]
    for idx,(name,raw) in enumerate(file_rows(files)):
        check=inspect_spectrum(name,raw)
        row={'index':idx, 'filename':name, 'status':check.status, 'reason':check.reason,
             'n_samples':check.n_samples,'wavelength_min':check.wavelength_min,'wavelength_max':check.wavelength_max}
        if check.status=='parsed':
            try:
                meta = detect(name, raw)
                if meta['wavelength_unit'] is None:
                    meta['wavelength_unit'] = suggested_wavelength_unit(check.wavelength_min, check.wavelength_max)
                    if meta['wavelength_unit'] is None:
                        meta['wavelength_unit'] = 'angstrom'
                        meta['wavelength_unit_source'] = 'assumed'
                    else:
                        meta['wavelength_unit_source'] = 'suggested'
                row.update(meta)
            except Exception as exc: row.update(status='rejected',reason='Metadata parsing failed: '+str(exc)[:250])
        rows.append(row)
    return rows

def _sn_key(value):
    return re.sub('[^a-z0-9]', '', str(value).casefold())

@lru_cache(maxsize=3)
def _reference_check(mode):
    """Verify live bundle identity against the model's own ordered reference rows."""
    run=RUNS[mode]; live=BUNDLES[mode]
    for p in (run/'best_ckpt.pt',run/'cfg_used.json',run/'latent_raw_z_best.npz',run/'data_meta.json',live/'data_meta.json'):
        if not p.is_file(): raise RuntimeError('DAEP model or reference file missing in Docker: '+str(p))
    with (run/'data_meta.json').open() as f: training=json.load(f)
    with (live/'data_meta.json').open() as f: bundle=json.load(f)
    for name,meta in (('model',training),('live',bundle)):
        if not isinstance(meta.get('sn'),list) or not isinstance(meta.get('spec_id'),list):
            raise RuntimeError(name+' reference metadata lacks ordered SN/spectrum IDs')
    ta=training['sn'];tb=bundle['sn']; sa=training['spec_id'];sb=bundle['spec_id']
    n=len(ta)
    if not (n==len(tb)==len(sa)==len(sb)):
        raise RuntimeError(f'Reference row count mismatch: model {n}, live {len(tb)}')
    for i in range(n):
        if _sn_key(ta[i])!=_sn_key(tb[i]) or str(sa[i])!=str(sb[i]):
            raise RuntimeError(f'Reference identity mismatch at row {i}; matching disabled')
    with np.load(run/'latent_raw_z_best.npz',allow_pickle=False) as npz:
        if 'z' not in npz or npz['z'].shape!=(n,16,64):
            raise RuntimeError('Reference latent array shape does not match live metadata rows')
    return True

@lru_cache(maxsize=3)
def _load_model(mode):
    _reference_check(mode)
    from astrodash.daep_inference import QueryManager
    repo=Path(os.environ.get('ASTRODASH_DAEP_REPO','/app/astrodash/infrastructure/ml/classifiers'))
    return QueryManager({mode:RUNS[mode]},repo=repo).get(mode)

def _values(filename,raw,mode,metadata,override):
    check=inspect_spectrum(filename,raw)
    if check.status!='parsed': raise ValueError(check.reason)
    found = detect(filename, raw)
    if mode not in ('rest_frame', 'observer_frame', 'multi_object_rest'):
        raise ValueError('Unsupported DAEP mode')
    # Multi-object training uses the rest-frame wavelength convention.
    frame = 'observer' if mode == 'observer_frame' else 'rest'
    if found.get('input_frame') and found['input_frame'] != frame:
        found.setdefault('warnings', []).append(
            'File frame metadata differs from selected model; wavelengths were left unchanged.')
    found['input_frame'] = frame
    # The original reference models are already in their corresponding frames.
    # Never silently deredshift, MW-correct, or convert air/vacuum here.
    ext=Path(filename).suffix.lower()
    wave,flux=_parse_fits(raw) if ext in {'.fits','.fit','.fts'} else _parse_ascii(raw,ext)
    from astrodash.daep_upload_preprocess import prepare
    w,v,mask=prepare(
        wave,flux,redshift=None,deredshift=False,
        input_frame=frame,wavelength_medium='air',
        wavelength_unit=found['wavelength_unit'],flux_unit=found['flux_unit'],
        mask_telluric=False)
    return found,w,v,mask,None,'not_applied'

def run_uploads(files,*,mode,overrides=None):
    if mode not in RUNS: raise ValueError('Unknown DAEP model')
    overrides=overrides or {}
    items=list(file_rows(files))
    output=[]; model=None; model_error=None
    for idx,(filename,raw) in enumerate(items):
        row={'index':idx,'filename':filename,'status':'rejected'}
        try:
            found,w,values,mask,ebv,mw_source=_values(filename,raw,mode,None,overrides.get(str(idx)))
            if model is None and model_error is None:
                try: model=_load_model(mode)
                except Exception as exc: model_error=str(exc)
            if model_error: raise RuntimeError('DAEP model unavailable: '+model_error)
            z=model.encode(w,values,mask)
            twins=model.twins(z,k=50)
            row.update(status='matched',reason='',object_name=found['object_name'],
                flux=values.tolist(),mask=mask.astype(int).tolist(),twins=twins,
                redshift=found['redshift'],input_frame=found['input_frame'],
                mw_ebv=ebv,mw_ebv_source=mw_source,warnings=found['warnings'])
        except Exception as exc: row['reason']=str(exc)[:450]
        output.append(row)
    return output
