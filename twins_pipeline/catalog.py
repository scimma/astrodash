"""Validate and safely update the six-field AstroDASH reviewed-literature catalog."""
import csv
import json
import math
import os
import tempfile
from pathlib import Path

DEFAULT_CATALOG = Path('app/astrodash/data/phase_literature/literature_phases.json')


def check_catalog(data):
    if not isinstance(data, dict) or not data:
        raise ValueError('Catalog must be a nonempty observation-keyed JSON object')
    problems = []
    for key, record in data.items():
        if not isinstance(key, str) or '|' not in key or not key.split('|', 1)[0]:
            problems.append(f'{key!r}: invalid observation key')
        if not isinstance(record, list) or len(record) != 6:
            problems.append(f'{key!r}: expected 6 fields, got {type(record).__name__}')
            continue
        # Do not reinterpret the remaining five fields: their positions and
        # provenance are controlled by the existing AstroDASH viewer schema.
        phase = record[0]
        if phase is not None and (isinstance(phase, bool) or not isinstance(phase, (float, int)) or not math.isfinite(phase)):
            problems.append(f'{key!r}: invalid phase {phase!r}')
        for i, value in enumerate(record):
            if isinstance(value, float) and not math.isfinite(value):
                problems.append(f'{key!r}: nonfinite field {i}')
    if problems:
        raise ValueError(f'{len(problems)} invalid entries:\n' + '\n'.join(problems[:25]))
    return len(data)


def load(path):
    with Path(path).open(encoding='utf-8') as f:
        data = json.load(f)
    check_catalog(data)
    return data


def _atomic_json_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix='.literature-', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def update_from_csv(current_path, input_path, output_path, *, allow_new=False):
    """Merge CSV edits without changing unmentioned observations.

    CSV columns: observation_key, value_json.
    value_json must be a JSON array of exactly six fields copied from the
    current catalog and edited intentionally. No implicit phase calculations.
    """
    catalog = load(current_path)
    with Path(input_path).open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or set(reader.fieldnames) != {'observation_key', 'value_json'}:
            raise ValueError('CSV needs exactly observation_key,value_json columns')
        touched = set()
        for line, row in enumerate(reader, start=2):
            key = (row.get('observation_key') or '').strip()
            if not key or key in touched:
                raise ValueError(f'Line {line}: empty/duplicate observation key')
            if key not in catalog and not allow_new:
                raise ValueError(f'Line {line}: unknown observation {key}; use --allow-new deliberately')
            try:
                value = json.loads(row['value_json'])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f'Line {line}: invalid JSON: {exc}') from exc
            catalog[key] = value
            touched.add(key)
    if not touched:
        raise ValueError('CSV has no edits')
    check_catalog(catalog)
    _atomic_json_write(output_path, catalog)
    return len(touched), len(catalog)


def export_csv(source, output):
    catalog = load(source)
    with Path(output).open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['observation_key', 'value_json'])
        writer.writerows((key, json.dumps(value, ensure_ascii=False, allow_nan=False)) for key, value in catalog.items())
    return len(catalog)
