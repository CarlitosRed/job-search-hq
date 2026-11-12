"""Preview or move jobs older than the archive limit into Old."""
import argparse
from datetime import date, datetime
import importlib.util
import json
from pathlib import Path
import sqlite3


_spec = importlib.util.spec_from_file_location('archive_store', Path(__file__).with_name('store.py'))
store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(store)
freshness = store.freshness
ARCHIVE_COLUMNS = ('From sheet', 'Archived on', 'Age days', 'Age from')


def database_backup(db, xlsx):
    folder = xlsx.parent / 'logs'
    folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    target = folder / f'{xlsx.stem}-before-archive-{stamp}.sqlite3'
    backup = sqlite3.connect(target)
    try:
        db.backup(backup)
    finally:
        backup.close()
    return str(target)


def move(db, preview=False):
    report = {'ok': True, 'moved': 0, 'by_stage': {}, 'kept_by_stage': {},
              'jobs': [], 'backup': None}
    today = date.today()
    candidates = []
    for record in db.execute('SELECT * FROM jobs ORDER BY id').fetchall():
        age = freshness.age_days(record['posted_on'], record['first_seen'], today)
        if age is None or age <= freshness.ARCHIVE_DAYS:
            continue
        stage = record['status']
        if stage not in freshness.ARCHIVE_STAGES:
            counts = report['kept_by_stage']
            counts[stage] = counts.get(stage, 0) + 1
            continue
        candidates.append((record, age))
        report['jobs'].append({'company': record['company'], 'role': record['role'],
                               'sheet': record['sheet'], 'stage': stage, 'age': age})
        counts = report['by_stage']
        counts[stage] = counts.get(stage, 0) + 1
    report['moved'] = len(candidates)
    if not candidates or preview:
        return report

    # Keep the original active id beside the archive row for history and allocation.
    columns = [r['name'] for r in db.execute('PRAGMA table_info(old_jobs)')]
    if 'original_job_id' not in columns:
        db.execute('ALTER TABLE old_jobs ADD COLUMN original_job_id INTEGER')
    headers = {r['sheet']: store.decode(r['header']) for r in db.execute(
        "SELECT sheet, header FROM layouts WHERE source='main'")}
    layout = db.execute("SELECT * FROM layouts WHERE source='old' AND sheet='Old'").fetchone()
    if layout is None:
        head = list(headers[candidates[0][0]['sheet']])
        for name in ARCHIVE_COLUMNS:
            if name not in head:
                head.append(name)
        position = db.execute("SELECT COALESCE(MAX(position), -1)+1 FROM layouts WHERE source='old'").fetchone()[0]
        db.execute('INSERT INTO layouts VALUES (?,?,?,?,?,?,?,?)',
                   ('old', 'Old', position, store.encode(head), '{}', 'A2', 0, 1))
        max_row = 1
    else:
        head = store.decode(layout['header'])
        max_row = layout['max_row']
    for record, _ in candidates:
        for name in (*headers[record['sheet']], *ARCHIVE_COLUMNS):
            if name not in head:
                head.append(name)
    next_row = max(max_row, db.execute(
        "SELECT COALESCE(MAX(row), 1) FROM old_jobs WHERE sheet='Old'").fetchone()[0]) + 1
    document_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'").fetchone()
    for record, age in candidates:
        values = dict(zip(headers[record['sheet']], store.decode(record['fields'])))
        posted = freshness.sheet_date(record['posted_on'])
        first_seen = freshness.sheet_date(record['first_seen'])
        age_from = 'Posted on' if posted and (not first_seen or posted <= first_seen) else 'First seen'
        values.update({'From sheet': record['sheet'], 'Archived on': today.isoformat(),
                       'Age days': age, 'Age from': age_from})
        archived = dict(record)
        archived.pop('id')
        archived.update(sheet='Old', row=next_row, old_status=record['status'],
                        fields=store.encode([values.get(name) for name in head]),
                        original_job_id=record['id'])
        names = ', '.join(archived)
        db.execute(f"INSERT INTO old_jobs ({names}) VALUES ({','.join('?' for _ in archived)})",
                   tuple(archived.values()))
        if document_table:
            db.execute('UPDATE documents SET job_id=? WHERE job_id=?',
                       (f'Old|{next_row}', f"{record['sheet']}|{record['row']}"))
        # Leave the layout's row positions intact; all history uses the saved id.
        db.execute('DELETE FROM jobs WHERE id=?', (record['id'],))
        next_row += 1
    db.execute("UPDATE layouts SET header=?, max_row=? WHERE source='old' AND sheet='Old'",
               (store.encode(head), next_row - 1))
    db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
    db.execute("UPDATE meta SET value=1 WHERE key='mirror_stale'")
    return report


def archive(xlsx, apply=False):
    xlsx = Path(xlsx)
    target = store.db_path(xlsx)
    if not target.is_file():
        return {'ok': False, 'moved': 0, 'by_stage': {}, 'kept_by_stage': {},
                'jobs': [], 'backup': None,
                'message': 'The tracker database does not exist. Migrate the workbook first.'}
    # Store schema upgrades must never precede the backup or touch a dry run.
    db = sqlite3.connect(target.resolve().as_uri() + ('?mode=rw' if apply else '?mode=ro'), uri=True)
    memory = None
    try:
        if apply:
            # Decide without writes before creating a backup or changing the schema.
            db.row_factory = sqlite3.Row
            report = move(db, preview=True)
            if not report['moved']:
                return report
        backup = database_backup(db, xlsx) if apply else None
        if not apply:
            memory = sqlite3.connect(':memory:')
            db.backup(memory)
        working = db if apply else memory
        working.row_factory = sqlite3.Row
        with working:
            working.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
            report = move(working)
        report['backup'] = backup
    finally:
        if memory is not None:
            memory.close()
        db.close()
    if apply and report['moved']:
        store.rebuild_mirror(xlsx)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook', required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    report = archive(args.workbook, apply=args.apply)
    print(json.dumps(report, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
