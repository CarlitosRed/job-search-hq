"""Preview or fill tracker dates without changing the Excel copy."""
import argparse
from datetime import date, datetime
import importlib.util
import json
from pathlib import Path
import sqlite3


def sibling(name):
    spec = importlib.util.spec_from_file_location('date_migration_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = sibling('store')
freshness = store.freshness


def database_backup(db, xlsx):
    folder = xlsx.parent / 'logs'
    folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    target = folder / f'{xlsx.stem}-before-dates-{stamp}.sqlite3'
    backup = sqlite3.connect(target)
    try:
        db.backup(backup)
    finally:
        backup.close()
    return str(target)


def fill(db):
    report = {'ok': True, 'jobs': 0, 'old_jobs': 0,
              'filled': {'posted_on': 0, 'date_source': 0, 'first_seen': 0},
              'by_source': {}, 'no_posted_date': 0, 'would_archive': 0, 'backup': None}
    today = date.today()
    headers = {(r['source'], r['sheet']): store.decode(r['header'])
               for r in db.execute('SELECT source, sheet, header FROM layouts')}
    for table, source in (('jobs', 'main'), ('old_jobs', 'old')):
        columns = [r['name'] for r in db.execute(f'PRAGMA table_info({table})')]
        if not columns:
            continue
        if 'posted_on' not in columns:
            db.execute(f'ALTER TABLE {table} ADD COLUMN posted_on TEXT')
        for record in db.execute(f'SELECT * FROM {table}').fetchall():
            report[table] += 1
            row = dict(record)
            posted, date_source = freshness.parse_posted(row['posted'])
            values = dict(zip(headers.get((source, row['sheet']), []), store.decode(row['fields'])))
            sheet = values.get('From sheet') if table == 'old_jobs' else row['sheet']
            checks = [freshness.sheet_date(h) for h, value in values.items()
                      if isinstance(h, str) and h.startswith('Open check ')
                      and value not in (None, '') and freshness.sheet_date(h)]
            first_seen = (freshness.sheet_date(sheet) or (min(checks) if checks else None)
                          or row['posted_on'] or posted or today.isoformat())
            changes = {}
            for field, value in (('posted_on', posted), ('date_source', date_source),
                                 ('first_seen', first_seen)):
                if row[field] in (None, '') and value not in (None, ''):
                    changes[field] = value
                    report['filled'][field] += 1
            if changes:
                assignments = ', '.join(f'{field}=?' for field in changes)
                db.execute(f'UPDATE {table} SET {assignments} WHERE id=?', (*changes.values(), row['id']))
                row.update(changes)
            if table == 'jobs':
                source_name = row['date_source']
                report['by_source'][source_name] = report['by_source'].get(source_name, 0) + 1
                if row['posted_on'] in (None, ''):
                    report['no_posted_date'] += 1
                age = freshness.age_days(row['posted_on'], row['first_seen'], today)
                if age is not None and age > freshness.ARCHIVE_DAYS and row['status'] in freshness.ARCHIVE_STAGES:
                    report['would_archive'] += 1
    if any(report['filled'].values()):
        db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
    return report


def migrate(xlsx, apply=False):
    xlsx = Path(xlsx)
    target = store.db_path(xlsx)
    if not target.is_file():
        return {'ok': False, 'message': 'The tracker database does not exist. Migrate the workbook first.'}
    # Never open Store here: its schema upgrades would precede the backup.
    db = sqlite3.connect(target.resolve().as_uri() + ('?mode=rw' if apply else '?mode=ro'), uri=True)
    memory = None
    try:
        backup = database_backup(db, xlsx) if apply else None
        if not apply:
            memory = sqlite3.connect(':memory:')
            db.backup(memory)
        working = db if apply else memory
        working.row_factory = sqlite3.Row
        with working:
            working.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
            report = fill(working)
        report['backup'] = backup
        return report
    finally:
        if memory is not None:
            memory.close()
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook', required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    report = migrate(args.workbook, apply=args.apply)
    print(json.dumps(report, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
