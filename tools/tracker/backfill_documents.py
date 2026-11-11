"""Register existing Word documents without changing the files."""
import argparse
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location('hq_backfill_store', Path(__file__).with_name('store.py'))
_store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_store)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook', required=True, type=Path)
    parser.add_argument('--work', type=Path)
    parser.add_argument('--prefix')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    prefix = args.prefix
    if prefix is None:
        spec = importlib.util.spec_from_file_location('hq_backfill_settings',
                    HERE.parent / 'hq-dashboard' / 'hq_settings.py')
        settings = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(settings)
        prefix = settings.load()['document_prefix']
    result = _store.backfill_documents(args.workbook, args.work or args.workbook.parent, prefix, args.apply)
    if not result['ok']:
        print(result['message'])
        return 1
    print(f"{'Applied' if args.apply else 'Dry run'}: found {result['found']} files; added {result['added']} records.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
