import argparse
import json
import sys
from pathlib import Path
from urllib.request import urlopen

from .catalog import DEFAULT_CATALOG, check_catalog, export_csv, load, update_from_csv


def main():
    parser = argparse.ArgumentParser(description='Git-based reviewed metadata management for AstroDASH (no S3 writes)')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('validate')
    p.add_argument('--catalog', type=Path, default=DEFAULT_CATALOG)
    p = sub.add_parser('export-csv')
    p.add_argument('--catalog', type=Path, default=DEFAULT_CATALOG)
    p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('update')
    p.add_argument('--catalog', type=Path, default=DEFAULT_CATALOG)
    p.add_argument('--csv', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True, help='Separate output file for review before replacing original')
    p.add_argument('--allow-new', action='store_true')
    p = sub.add_parser('check-live')
    p.add_argument('--catalog', type=Path, default=DEFAULT_CATALOG)
    p.add_argument('--url', default='http://localhost:4000/astrodash/twins/reviewed-literature.json')
    args = parser.parse_args()
    try:
        if args.command == 'validate':
            print(f'OK: {check_catalog(load(args.catalog))} six-field observations')
        elif args.command == 'export-csv':
            print(f'Exported {export_csv(args.catalog, args.output)} observations to {args.output}')
        elif args.command == 'update':
            if args.catalog.resolve() == args.output.resolve():
                raise ValueError('Output must differ from source; inspect diff before replacing catalog')
            edited, total = update_from_csv(args.catalog, args.csv, args.output, allow_new=args.allow_new)
            print(f'Wrote {total} observations ({edited} edited) to {args.output}')
        elif args.command == 'check-live':
            local = load(args.catalog)
            with urlopen(args.url, timeout=15) as response:
                live = json.load(response)
            check_catalog(live)
            if local != live:
                missing = set(local) - set(live)
                extra = set(live) - set(local)
                changed = sum(local[k] != live[k] for k in set(local) & set(live))
                raise ValueError(f'LIVE MISMATCH: missing={len(missing)}, extra={len(extra)}, changed={changed}')
            print(f'OK: live endpoint exactly matches all {len(local)} observations')
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
