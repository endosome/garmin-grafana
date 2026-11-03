"""python -m garmin_fit_store: import, export, verify, roundtrip and convert, using the PG* environment variables."""
import argparse
import sys
from pathlib import Path

import psycopg

from . import decoder, ir, store


def _parser():
    parser = argparse.ArgumentParser(prog='garmin_fit_store', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    imp = commands.add_parser('import', help='store FIT files')
    imp.add_argument('files', nargs='+', type=Path)
    imp.add_argument('--source-id', type=int)
    imp.add_argument('--garmin-activity-id', type=int)
    imp.add_argument('--ignore-crc', action='store_true', help='accept files with a bad checksum')
    exp = commands.add_parser('export', help='write a stored file back to FIT')
    exp.add_argument('file_id', type=int)
    exp.add_argument('output', type=Path)
    ver = commands.add_parser('verify', help='check stored files against their original bytes')
    ver.add_argument('file_ids', nargs='*', type=int, help='default: every file')
    rnd = commands.add_parser('roundtrip', help='import, export and compare, then roll everything back')
    rnd.add_argument('files', nargs='+', type=Path)
    rnd.add_argument('--ignore-crc', action='store_true')
    conv = commands.add_parser('convert', help='write a FIT file as gpx, tcx, json or csv (no database)')
    conv.add_argument('--to', required=True, choices=['gpx', 'tcx', 'json', 'csv', 'jsonl'])
    conv.add_argument('input', type=Path)
    conv.add_argument('output', type=Path)
    return parser


def _fit_paths(paths):
    for path in paths:
        yield from sorted(path.rglob('*.fit')) if path.is_dir() else [path]


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.command == 'convert':
        from . import convert
        args.output.write_text(convert.convert(decoder.decode(args.input.read_bytes(), check_crc=False), args.to))
        return 0
    with psycopg.connect() as connection:
        if args.command == 'import':
            for path in _fit_paths(args.files):
                file_id = store.import_file(connection, path.read_bytes(), args.source_id, args.garmin_activity_id, not args.ignore_crc)
                print(f'{path}: {file_id}')
        elif args.command == 'export':
            args.output.write_bytes(store.export_file(connection, args.file_id))
        elif args.command == 'verify':
            ids = args.file_ids or [row[0] for row in connection.execute('SELECT id FROM fit.files ORDER BY id')]
            failed = 0
            for file_id in ids:
                differences = store.verify(connection, file_id)
                failed += bool(differences)
                print(f'{file_id}: {"ok" if not differences else "; ".join(differences[:5])}')
            return 1 if failed else 0
        else:
            return _roundtrip(connection, args)
    return 0


def _roundtrip(connection, args):
    paths = list(_fit_paths(args.files))
    differences = 0
    for path in paths:
        data = path.read_bytes()
        try:
            with connection.transaction(force_rollback=True):
                file_id = store.import_file(connection, data, check_crc=not args.ignore_crc)
                found = store.verify(connection, file_id)
                exported = ir.canonical(decoder.decode(store.export_file(connection, file_id)))
                if exported != ir.canonical(decoder.decode(data, check_crc=False)):
                    found.append('export differs from the original')
        except Exception as error:  # report per file and go on
            found = [f'{type(error).__name__}: {error}']
        differences += bool(found)
        print(f'{path}: {"ok" if not found else "; ".join(found[:5])}')
    print(f'{len(paths)} files, {differences} with differences')
    return 1 if differences else 0


if __name__ == '__main__':
    sys.exit(main())
