"""Preview or migrate live tracker stages, preserving unknown statuses."""
import argparse
from collections import Counter
from datetime import datetime
import importlib.util
import os
from pathlib import Path
import sqlite3
import tempfile

import openpyxl


def sibling(name):
    spec = importlib.util.spec_from_file_location('stage_migration_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = sibling('store')
stages = sibling('stages')


def plan(wb):
    report = {'ok': True, 'sheets': {}, 'changed': 0, 'unknown': {},
              'rows_in': 0, 'rows_out': 0}
    changes, table = [], []
    for ws in wb:
        head = [c.value for c in ws[1]]
        if 'My status' not in head or ws.title == 'Contacts':
            continue
        col = head.index('My status') + 1
        before, after, pairs = Counter(), Counter(), Counter()
        for number, values in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
            if len(values) < 2 or not values[1]:
                continue
            row = dict(zip(head, values))
            old = row.get('My status')
            has_fit = any(row.get(k) is not None and str(row[k]).strip() != ''
                          for k in ('Score', 'Verdict'))
            new = stages.from_legacy(old, has_fit)
            if new is None:
                report['unknown'][old] = report['unknown'].get(old, 0) + 1
                new = old
            before[old] += 1
            after[new] += 1
            pairs[(old, new)] += 1
            if old != new:
                changes.append((ws.title, number, col, old, new))
        report['sheets'][ws.title] = {'in': dict(before), 'out': dict(after)}
        report['rows_in'] += sum(before.values())
        report['rows_out'] += sum(after.values())
        table.extend((ws.title, old, new, count) for (old, new), count in pairs.items())
    report['changed'] = len(changes)
    report['ok'] = report['rows_in'] == report['rows_out'] and all(
        sum(c['in'].values()) == sum(c['out'].values()) for c in report['sheets'].values())
    report['transitions'] = table
    return report, changes


def validations(wb):
    for ws in wb:
        head = [c.value for c in ws[1]]
        if 'My status' not in head or ws.title == 'Contacts':
            continue
        col = head.index('My status') + 1
        for dv in ws.data_validations.dataValidation:
            if dv.type == 'list' and any(r.min_col <= col <= r.max_col for r in dv.sqref.ranges):
                dv.formula1 = '"' + ','.join(stages.NAMES) + '"'


def database_backup(storage, xlsx):
    folder = xlsx.parent / 'logs'
    folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    target = folder / f'{xlsx.stem}-before-stages-{stamp}.sqlite3'
    backup = sqlite3.connect(target)
    try:
        storage.db.backup(backup)
    finally:
        backup.close()
    return str(target)


def migrate(xlsx, apply=False):
    xlsx = Path(xlsx)
    storage, wb, temporary = None, None, None
    try:
        if store.db_path(xlsx).exists():
            storage = store.Store(store.db_path(xlsx))
            if apply:
                backup = database_backup(storage, xlsx)
            # Keep the read, plan, verification and updates in one snapshot.
            with storage.db:
                storage.db.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
                wb = storage.workbook()
                report, changes = plan(wb)
                old_count = storage.db.execute('SELECT COUNT(*) FROM old_jobs').fetchone()[0]
                report['old_jobs'] = {'in': old_count, 'out': old_count}
                if not report['ok']:
                    raise RuntimeError('Counts differ; no stages were changed.')
                if apply:
                    for sheet, number, col, old, new in changes:
                        ws = wb[sheet]
                        ws.cell(number, col).value = new
                        head = [c.value for c in ws[1]]
                        values = [c.value for c in ws[number]]
                        store_id = storage.db.execute('SELECT id FROM jobs WHERE sheet=? AND row=?',
                                                      (sheet, number)).fetchone()[0]
                        storage.put_row('jobs', sheet, number, head, values, history=True, who='stages-01')
                        storage.db.execute('UPDATE jobs SET old_status=? WHERE id=?', (old, store_id))
                    check = storage.workbook()
                    try:
                        verified, _ = plan(check)
                    finally:
                        check.close()
                    if verified['rows_in'] != report['rows_out'] or verified['sheets'] != {
                            s: {'in': c['out'], 'out': c['out']} for s, c in report['sheets'].items()}:
                        raise RuntimeError('Counts differ; no stages were changed.')
                    report['old_jobs']['out'] = storage.db.execute('SELECT COUNT(*) FROM old_jobs').fetchone()[0]
                    if report['old_jobs']['out'] != old_count:
                        raise RuntimeError('Archive counts differ; no stages were changed.')
                    if changes:
                        storage.set_meta('revision', int(storage.meta('revision')) + 1)
                        storage.set_meta('mirror_stale', 1)
                    report['backup'] = backup
            storage.close()
            storage = None
            if apply:
                store.rebuild_mirror(xlsx)
            return report
        wb = openpyxl.load_workbook(xlsx)
        report, changes = plan(wb)
        if not report['ok']:
            raise RuntimeError('Counts differ; no stages were changed.')
        if apply:
            report['backup'] = str(store.copy_log(xlsx, 'before-stages'))
            for sheet, number, col, old, new in changes:
                wb[sheet].cell(number, col).value = new
            validations(wb)
            fd, temporary = tempfile.mkstemp(prefix='.' + xlsx.stem + '-stages-', suffix='.xlsx', dir=xlsx.parent)
            os.close(fd)
            wb.save(temporary)
            check = openpyxl.load_workbook(temporary)
            try:
                verified, _ = plan(check)
                if verified['rows_in'] != report['rows_out'] or any(
                        verified['sheets'][s]['in'] != c['out'] for s, c in report['sheets'].items()):
                    raise RuntimeError('Counts differ; workbook was not replaced.')
            finally:
                check.close()
            os.replace(temporary, xlsx)
        return report
    finally:
        if wb is not None:
            wb.close()
        if storage is not None:
            storage.close()
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook', required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    report = migrate(args.workbook, apply=args.apply)
    print('Stages migrated. Backup: ' + report['backup'] if args.apply else 'Dry run: nothing written.')
    print(f'{"Sheet":24} {"Old status":30} {"New stage":24} {"Count":>6}')
    for sheet, old, new, count in report['transitions']:
        print(f'{sheet:24} {str(old or "(blank)"):30} {str(new or "(blank)"):24} {count:6}')
    print(f'Rows: {report["rows_in"]} in, {report["rows_out"]} out; {report["changed"]} changed.')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
