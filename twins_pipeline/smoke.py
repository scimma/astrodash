"""Non-destructive smoke tests for the existing AstroDASH deployment.

Does not replace model code or assume S3 permissions.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

REQUIRED_MODULES = (
    'astrodash.daep_inference',
    'astrodash.daep_upload_core',
    'astrodash.daep_upload_metadata',
    'astrodash.daep_upload_preprocess',
    'astrodash.daep_upload_runtime',
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--base-url', default='http://localhost:4000')
    p.add_argument('--catalog', default='app/astrodash/data/phase_literature/literature_phases.json')
    p.add_argument('--python', default=sys.executable, help='Python executable with Django dependencies')
    p.add_argument('--skip-imports', action='store_true', help='If app dependencies are only available in Docker')
    args = p.parse_args()
    failures = []
    src = Path(args.catalog)
    if not src.is_file():
        failures.append(f'Missing catalog: {src}')
    else:
        from .catalog import load
        local = load(src)
        print('Catalog entries:', len(local))
        try:
            with urlopen(args.base_url.rstrip('/') + '/astrodash/twins/reviewed-literature.json', timeout=15) as resp:
                remote = json.load(resp)
            if remote != local:
                failures.append('Live metadata differs from Git catalog')
            else:
                print('Live catalog: exact match')
        except Exception as exc:
            failures.append(f'Literature endpoint failed: {exc}')
    try:
        with urlopen(args.base_url.rstrip('/') + '/astrodash/twins/', timeout=15) as resp:
            if resp.status != 200:
                failures.append(f'Twins viewer HTTP {resp.status}')
            else:
                print('Twins viewer: HTTP 200')
    except Exception as exc:
        failures.append(f'Twins viewer failed: {exc}')
    if not args.skip_imports:
        code = '\n'.join(f'import {mod}' for mod in REQUIRED_MODULES)
        proc = subprocess.run([args.python, '-c', code], capture_output=True, text=True)
        if proc.returncode:
            failures.append('Pipeline module imports failed:\n' + proc.stderr[-3000:])
        else:
            print('All existing DAEP pipeline modules import successfully')
    print('\n'.join(f'FAIL: {f}' for f in failures))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
