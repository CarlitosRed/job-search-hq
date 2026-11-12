"""Offline checks for the 60-day archive move and the Old sheet (build step 02b).

Every workbook here is fictional and lives in a temporary folder.
"""
from datetime import date, timedelta
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import openpyxl

HERE = Path(__file__).resolve().parent
TODAY = date.today()
HEAD = ['#', 'Company', 'Role', 'Where', 'Posted', 'Score', 'Verdict', 'My status', 'Date applied', 'Link', 'Notes']


def D(days):
    return (TODAY - timedelta(days=days)).isoformat()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = load_module('tracker_store_for_archive', HERE / 'store.py')
dates = load_module('tracker_dates_for_archive', HERE / 'migrate_dates.py')
_count = 0


def archive_module():
    return load_module('tracker_archive_under_test', HERE / 'archive_old.py')


def tracker_for(xlsx):
    global _count
    _count += 1
    module = load_module(f'tracker_for_archive_{_count}', HERE / 'server.py')
    module.XLSX = Path(xlsx).resolve()
    module.BACKUPS = module.XLSX.parent / '.tracker-backups'
    return module


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


CHECK = f'Open check {D(20)}'
# (company, posted, status, open check value)
JOBS = [
    ('Move Review', D(70), 'Review', 'OPEN (fictional)'),
    ('Move Tracked', f'{D(61)} (Lever created)', 'Tracked', None),
    ('Move Apply', D(90), 'Apply', None),
    ('Move Skipped', D(65), 'Skipped', None),
    ('Keep Applied', D(90), 'Applied', None),
    ('Keep Interview', D(90), 'Interview', None),
    ('Keep Offer', D(90), 'Offer', None),
    ('Keep Rejected', D(90), 'Rejected', None),
    ('Keep Sixty', D(60), 'Review', None),
    ('Keep Fresh', D(5), 'Review', None),
    ('Keep Undated', 'not shown', 'Review', None),
    ('Repost', f'{D(3)} (Greenhouse first published)', 'Review', None),
]
SHEET = f'New ({D(80)})'
MOVED = {'Move Review', 'Move Tracked', 'Move Apply', 'Move Skipped', 'Repost', 'Sheet Move'}


def make_tracker(folder, with_old=True):
    xlsx = Path(folder) / 'applications-tracker.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Jobs'
    ws.append(HEAD + [CHECK])
    for n, (company, posted, status, check) in enumerate(JOBS, 1):
        ws.append([n, company, 'Example Role', 'Remote', posted, 6, 'Good', status, None,
                   f'https://example.com/jobs/{n}', f'Note for {company}', check])
    other = wb.create_sheet(SHEET)
    other.append(HEAD)
    other.append([1, 'Sheet Keep', 'Example Role', 'Remote', D(2), 6, 'Good', 'Applied', None, 'https://example.com/s1', None])
    other.append([2, 'Sheet Move', 'Example Role', 'Remote', None, 6, 'Good', 'Review', None, 'https://example.com/s2', None])
    contacts = wb.create_sheet('Contacts')
    contacts.append(['Person', 'Where now', 'How you know them', 'Status', 'Date sent', 'Reply', 'Notes'])
    contacts.append(['Example Person', 'Example Co', 'Fictional', 'Message drafted', None, None, None])
    wb.save(xlsx)
    wb.close()
    if not with_old:
        return xlsx, None
    old = Path(folder) / 'applications-tracker-old.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Old'
    ws.append(HEAD + ['From sheet', 'Archived on', 'Age days', 'Age from'])
    ws.append([1, 'Archived Co', 'Old Role', 'Remote', D(120), 5, 'Good', 'Review', None,
               'https://example.com/o1', None, 'Jobs', D(30), 90, 'Posted on'])
    wb.save(old)
    wb.close()
    return xlsx, old


def prepare(folder, with_old=True):
    """A database as it is after 02a: migrated, dates filled, one repost with an old First seen."""
    xlsx, old = make_tracker(folder, with_old)
    assert store.migrate(xlsx, old=old, apply=True)['ok']
    assert dates.migrate(xlsx, apply=True)['ok']
    con = sqlite3.connect(store.db_path(xlsx))
    con.execute("UPDATE jobs SET first_seen=? WHERE company='Repost'", (D(70),))
    con.commit()
    con.close()
    store.rebuild_mirror(xlsx)
    return xlsx


class Base(unittest.TestCase):
    with_old = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hq-archive-')
        self.xlsx = prepare(self.tmp.name, self.with_old)
        self.db = store.db_path(self.xlsx)
        self.m = archive_module()

    def tearDown(self):
        self.tmp.cleanup()

    def query(self, sql, args=()):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, args)]
        finally:
            con.close()

    def companies(self, table):
        return {r['company'] for r in self.query(f'SELECT company FROM {table}')}

    def revision(self):
        return int(self.query("SELECT value FROM meta WHERE key='revision'")[0]['value'])

    def old_layout(self):
        rows = self.query("SELECT header FROM layouts WHERE source='old' AND sheet='Old'")
        return store.decode(rows[0]['header']) if rows else None

    def old_values(self, company):
        record = self.query("SELECT * FROM old_jobs WHERE company=?", (company,))[0]
        head = self.old_layout()
        return record, dict(zip(head, store.decode(record['fields'])))


class DryRun(Base):
    def test_dry_run_writes_nothing_and_lists_every_move(self):
        before, xlsx_before = digest(self.db), digest(self.xlsx)
        report = self.m.archive(self.xlsx)
        self.assertTrue(report['ok'])
        self.assertEqual(report['moved'], 6)
        self.assertEqual({j['company'] for j in report['jobs']}, MOVED)
        sample = next(j for j in report['jobs'] if j['company'] == 'Move Apply')
        self.assertEqual((sample['role'], sample['sheet'], sample['stage'], sample['age']),
                         ('Example Role', 'Jobs', 'Apply', 90))
        self.assertEqual(report['by_stage'], {'Review': 3, 'Tracked': 1, 'Apply': 1, 'Skipped': 1})
        self.assertEqual(report['kept_by_stage'], {'Applied': 2, 'Interview': 1, 'Offer': 1, 'Rejected': 1})
        self.assertIsNone(report['backup'])
        self.assertEqual(digest(self.db), before)
        self.assertEqual(digest(self.xlsx), xlsx_before)
        self.assertEqual(len(list((self.xlsx.parent / 'logs').glob('*before-archive*'))) if (self.xlsx.parent / 'logs').exists() else 0, 0)

    def test_cli_dry_run_prints_json(self):
        before = digest(self.db)
        result = subprocess.run([sys.executable, '-I', str(HERE / 'archive_old.py'), '--workbook', str(self.xlsx), '--dry-run'],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['moved'], 6)
        self.assertEqual(digest(self.db), before)


class Apply(Base):
    def setUp(self):
        super().setUp()
        self.jobs_before = len(self.query('SELECT id FROM jobs'))
        self.old_before = len(self.query('SELECT id FROM old_jobs'))
        self.revision_before = self.revision()
        self.report = self.m.archive(self.xlsx, apply=True)

    def test_moves_exactly_the_old_jobs_in_early_stages(self):
        self.assertTrue(self.report['ok'])
        self.assertEqual(self.report['moved'], 6)
        self.assertFalse(MOVED & self.companies('jobs'))
        self.assertTrue(MOVED <= self.companies('old_jobs'))
        for keep in ('Keep Applied', 'Keep Interview', 'Keep Offer', 'Keep Rejected',
                     'Keep Sixty', 'Keep Fresh', 'Keep Undated', 'Sheet Keep'):
            self.assertIn(keep, self.companies('jobs'), keep)

    def test_nothing_is_deleted(self):
        self.assertEqual(len(self.query('SELECT id FROM jobs')), self.jobs_before - 6)
        self.assertEqual(len(self.query('SELECT id FROM old_jobs')), self.old_before + 6)
        self.assertIn('Archived Co', self.companies('old_jobs'))

    def test_values_move_by_column_name(self):
        record, values = self.old_values('Move Review')
        self.assertEqual(record['sheet'], 'Old')
        self.assertEqual(values['Company'], 'Move Review')
        self.assertEqual(values['Posted'], D(70))
        self.assertEqual(values['Link'], 'https://example.com/jobs/1')
        self.assertEqual(values['Notes'], 'Note for Move Review')
        self.assertEqual(values['My status'], 'Review')
        self.assertEqual(values['From sheet'], 'Jobs')
        self.assertEqual(str(values['Archived on'])[:10], TODAY.isoformat())
        self.assertEqual(int(values['Age days']), 70)
        self.assertEqual(values['Age from'], 'Posted on')
        _, sheet_move = self.old_values('Sheet Move')
        self.assertEqual(sheet_move['From sheet'], SHEET)
        self.assertEqual(sheet_move['Age from'], 'First seen')

    def test_missing_columns_are_added_to_the_old_sheet(self):
        head = self.old_layout()
        self.assertEqual(head[:15], HEAD + ['From sheet', 'Archived on', 'Age days', 'Age from'])
        self.assertIn(CHECK, head)
        _, values = self.old_values('Move Review')
        self.assertEqual(values[CHECK], 'OPEN (fictional)')
        _, archived = self.old_values('Archived Co')
        self.assertEqual(archived['Company'], 'Archived Co')
        self.assertEqual(archived['From sheet'], 'Jobs')

    def test_dates_and_stage_are_kept(self):
        record, _ = self.old_values('Repost')
        self.assertEqual(record['first_seen'], D(70))
        self.assertEqual(record['posted_on'], D(3))
        self.assertEqual(record['date_source'], 'greenhouse')
        self.assertEqual(record['old_status'], 'Review')
        record, _ = self.old_values('Move Tracked')
        self.assertEqual((record['posted_on'], record['date_source']), (D(61), 'lever'))

    def test_second_run_moves_nothing(self):
        revision = self.revision()
        again = self.m.archive(self.xlsx, apply=True)
        self.assertTrue(again['ok'])
        self.assertEqual(again['moved'], 0)
        self.assertEqual(self.revision(), revision)
        # A daily run with nothing to move leaves no extra backup behind.
        self.assertIsNone(again['backup'])
        self.assertEqual(len(list((self.xlsx.parent / 'logs').glob('*before-archive*'))), 1)

    def test_revision_backup_and_keys(self):
        self.assertEqual(self.revision(), self.revision_before + 1)
        self.assertTrue(self.report['backup'] and Path(self.report['backup']).is_file())
        self.assertIn('logs', Path(self.report['backup']).parts)
        storage = store.Store(self.db)
        try:
            keys = storage.known_keys()
        finally:
            storage.close()
        self.assertIn(store.freshness.job_key('Move Review', 'Example Role', 'Remote'), keys)

    def test_excel_copy_gets_the_old_sheet_last(self):
        wb = openpyxl.load_workbook(self.xlsx)
        try:
            self.assertEqual(wb.sheetnames[-1], 'Old')
            self.assertEqual(wb.sheetnames[:-1], ['Jobs', SHEET, 'Contacts'])
            old = wb['Old']
            head = [c.value for c in old[1]]
            self.assertIn('From sheet', head)
            names = {r[1] for r in old.iter_rows(min_row=2, values_only=True) if r and r[1]}
            self.assertEqual(names, MOVED | {'Archived Co'})
            main = {r[1] for r in wb['Jobs'].iter_rows(min_row=2, values_only=True) if r and r[1]}
            self.assertFalse(MOVED & main)
        finally:
            wb.close()

    def test_site_never_shows_old(self):
        tracker = tracker_for(self.xlsx)
        data = tracker.load()
        shown = {j['Company'] for j in data['jobs']}
        self.assertFalse(MOVED & shown)
        self.assertNotIn('Archived Co', shown)
        self.assertNotIn('Old', [s['name'] for s in data['sheets']])
        self.assertEqual(len(shown), len(JOBS) + 2 - 6)


class History(Base):
    def test_history_is_kept_and_never_inherited(self):
        moved = self.query("SELECT id, sheet, row FROM jobs WHERE company='Sheet Move'")[0]
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO moves(job_id, from_status, to_status, at, who) VALUES (?,?,?,?,?)",
                    (moved['id'], 'Tracked', 'Review', D(75) + 'T00:00:00+00:00', 'site'))
        con.execute("INSERT INTO interview_rounds(job_id, round, date, type, note) VALUES (?,?,?,?,?)",
                    (moved['id'], 1, D(75), 'Fictional', ''))
        con.commit()
        con.close()
        storage = store.Store(self.db)
        try:
            with storage.db:
                storage.add_document('cv', 'Fictional_CV.docx', 'abc', 'site', D(70),
                                     job_id=f"{moved['sheet']}|{moved['row']}")
        finally:
            storage.close()
        moves_before = len(self.query('SELECT id FROM moves'))
        self.assertTrue(self.m.archive(self.xlsx, apply=True)['ok'])
        self.assertEqual(len(self.query('SELECT id FROM moves')), moves_before)
        self.assertEqual(len(self.query('SELECT id FROM interview_rounds')), 1)
        old_row = self.query("SELECT row FROM old_jobs WHERE company='Sheet Move'")[0]['row']
        self.assertEqual([d['job_id'] for d in self.query('SELECT job_id FROM documents')], [f'Old|{old_row}'])
        # A new job lands in the freed row of the same sheet.
        storage = store.Store(self.db)
        try:
            with storage.edit() as wb:
                ws = wb[SHEET]
                for column, value in enumerate([3, 'Brand New', 'Example Role', 'Remote', D(1), 6, 'Good', 'Review',
                                                None, 'https://example.com/new', None], 1):
                    ws.cell(moved['row'], column).value = value
        finally:
            storage.close()
        new = self.query("SELECT id, sheet, row FROM jobs WHERE company='Brand New'")[0]
        self.assertEqual((new['sheet'], new['row']), (moved['sheet'], moved['row']))
        self.assertNotEqual(new['id'], moved['id'])
        self.assertFalse(self.query('SELECT id FROM moves WHERE job_id=?', (new['id'],)))
        self.assertFalse(self.query('SELECT id FROM interview_rounds WHERE job_id=?', (new['id'],)))
        self.assertFalse(self.query('SELECT id FROM documents WHERE job_id=?', (f"{new['sheet']}|{new['row']}",)))


class NoOldSheetYet(Base):
    with_old = False

    def test_first_move_creates_the_old_sheet(self):
        self.assertIsNone(self.old_layout())
        report = self.m.archive(self.xlsx, apply=True)
        self.assertTrue(report['ok'])
        self.assertEqual(report['moved'], 6)
        head = self.old_layout()
        self.assertEqual(head[:len(HEAD)], HEAD)
        for name in ('From sheet', 'Archived on', 'Age days', 'Age from'):
            self.assertIn(name, head)
        self.assertEqual(self.companies('old_jobs'), MOVED)
        wb = openpyxl.load_workbook(self.xlsx)
        try:
            self.assertEqual(wb.sheetnames[-1], 'Old')
        finally:
            wb.close()


class ExcelOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hq-archive-xl-')
        self.xlsx, _ = make_tracker(self.tmp.name, with_old=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_refuses_plainly(self):
        before = digest(self.xlsx)
        for apply in (False, True):
            report = archive_module().archive(self.xlsx, apply=apply)
            self.assertFalse(report['ok'])
            self.assertIn('database', report['message'])
        self.assertEqual(digest(self.xlsx), before)
        self.assertFalse(store.db_path(self.xlsx).exists())

    def test_loader_skips_an_old_sheet(self):
        wb = openpyxl.load_workbook(self.xlsx)
        ws = wb.create_sheet('Old')
        ws.append(HEAD + ['From sheet'])
        ws.append([1, 'Hidden Old Co', 'Example Role', 'Remote', D(90), 6, 'Good', 'Review', None, 'https://example.com/h', None, 'Jobs'])
        wb.save(self.xlsx)
        wb.close()
        data = tracker_for(self.xlsx).load()
        self.assertEqual(data['store'], 'excel')
        self.assertNotIn('Hidden Old Co', {j['Company'] for j in data['jobs']})
        self.assertNotIn('Old', [s['name'] for s in data['sheets']])


if __name__ == '__main__':
    unittest.main(verbosity=2)
