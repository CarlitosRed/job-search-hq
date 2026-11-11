"""Count and optionally migrate a tracker workbook to SQLite."""
import argparse
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('tracker_store', Path(__file__).with_name('store.py'))
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


def table(title, counts):
    print(f'\n{title}')
    print(f'{"Status":30} {"In":>6} {"Out":>6}')
    for status in sorted(counts['in'].keys() | counts['out'].keys(), key=str):
        print(f'{str(status or "(blank)"):30} {counts["in"].get(status, 0):6} {counts["out"].get(status, 0):6}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook', required=True, type=Path)
    parser.add_argument('--old', type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        report = store.migrate(args.workbook, old=args.old, apply=args.apply)
    except FileExistsError as error:
        print(error)
        return 2
    except RuntimeError as error:
        print(error)
        return 1
    if args.dry_run:
        print('Dry run: nothing written.')
    else:
        print(f'Migrated to {report["db"]}. Backup: {report["backup"]}')
    for sheet, counts in report['sheets'].items():
        table(sheet, counts)
    if report['old'] is not None:
        table('Old jobs', report['old'])
    print(f'\n{"":30} {"In":>6} {"Out":>6}')
    print(f'{"Contacts":30} {report["contacts"]["in"]:6} {report["contacts"]["out"]:6}')
    print(f'{"Total rows":30} {report["rows_in"]:6} {report["rows_out"]:6}')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
