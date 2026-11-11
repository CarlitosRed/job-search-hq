"""Offline checks for the SQLite tracker store (build step 00b).

Every workbook here is fictional and lives in a temporary folder.
"""
from datetime import date, datetime
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager

import openpyxl
from openpyxl.worksheet.datavalidation import DataValidation

HERE = Path(__file__).resolve().parent
STATUSES = ("Suggested (not applied)", "Message drafted", "CV ready", "Applied", "Interview", "Rejected", "Offer", "Skipped")
JOB_HEAD = ['#', 'Company', 'Role', 'Where', 'Posted', 'Score', 'Verdict', 'My status', 'Date applied', 'CV used',
            'Link', 'Notes', 'Open check 2026-01-05', 'Agent notes']


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = load_module('tracker_store_under_test', HERE / 'store.py')
_count = 0


def tracker_for(xlsx):
    """Load a fresh copy of the tracker module pointed at one workbook (as hq-dashboard/server.py does)."""
    global _count
    _count += 1
    module = load_module(f'tracker_under_test_{_count}', HERE / 'server.py')
    module.XLSX = Path(xlsx).resolve()
    module.BACKUPS = module.XLSX.parent / '.tracker-backups'
    return module


def make_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Jobs'
    ws.append(JOB_HEAD)
    ws.append([1, 'Example Co', 'Example Analyst', 'Remote', date(2026, 1, 2), 7, 'Good', 'Suggested (not applied)', None, None,
               'https://example.com/jobs/1?id=1', 'First fictional note', 'OPEN (fictional)', 'Agent only'])
    ws.append([2, 'Sample Labs', 'Sample Engineer', 'Remote', date(2026, 1, 3), 6.5, 'Stretch', 'Applied', date(2026, 1, 4), 'cv-a.docx',
               'https://example.com/jobs/2?id=2', None, None, None])
    ws.append([None] * len(JOB_HEAD))  # blank row stays blank
    ws.append([None, None, None, None, None, None, None, None, None, None, None, 'A note row with no company', None, None])
    ws.append([4, 'Demo Inc', 'Demo Builder', 'Hybrid', None, None, None, 'Interview', date(2026, 1, 5), None,
               'https://example.com/jobs/4', 'Third note', 'CLOSED (fictional)', None])
    ws.column_dimensions['B'].width = 31
    dv = DataValidation(type='list', formula1='"' + ','.join(STATUSES) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add('H2:H200')
    new = wb.create_sheet('New (2026-01-06)')
    new.append(['#', 'Company', 'Role', 'My status', 'Date applied', 'Source', 'Link', 'Notes'])
    new.append(['N1', 'Fresh Example', 'Fresh Role', 'Suggested (not applied)', None, 'Fictional board', 'https://example.com/n1', None])
    new.append(['N2', 'Other Example', 'Other Role', 'Skipped', None, 'Fictional board', 'https://example.com/n2', 'Not a fit'])
    contacts = wb.create_sheet('Contacts')
    contacts.append(['Person', 'Where now', 'Status', 'Date sent', 'Notes'])
    contacts.append(['Pat Example', 'Example Co', 'Not sent', None, 'Fictional contact'])
    other = wb.create_sheet('Read me')
    other.append(['This sheet is not a job list'])
    other.append(['It must survive the move'])
    wb.save(path)
    wb.close()


def make_old_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Old'
    ws.append(['#', 'Company', 'Role', 'My status', 'Link', 'From sheet', 'Archived on'])
    ws.append([9, 'Archived Example', 'Old Role', 'Suggested (not applied)', 'https://example.com/old/9', 'Jobs', date(2026, 1, 1)])
    ws.append([10, 'Archived Two', 'Old Role 2', 'Skipped', 'https://example.com/old/10', 'Jobs', date(2026, 1, 1)])
    wb.save(path)
    wb.close()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def without_volatile(data):
    data = json.loads(data) if isinstance(data, str) else dict(data)
    for key in ('version', 'today', 'store', 'mirrorWarning'):
        data.pop(key, None)
    return data


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tracker-store-test-')
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / 'work'
        self.work.mkdir()
        self.xlsx = self.work / 'applications-tracker.xlsx'
        self.old = self.work / 'applications-tracker-old.xlsx'
        make_workbook(self.xlsx)
        make_old_workbook(self.old)

    def migrate(self, apply=True):
        return store.migrate(self.xlsx, old=self.old, apply=apply)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(store.db_path(self.xlsx))
        try:
            yield connection
        finally:
            connection.close()


class Migration(Base):
    def test_db_path_sits_next_to_the_workbook(self):
        self.assertEqual(store.db_path(self.xlsx), self.work / 'applications-tracker.sqlite3')

    def test_dry_run_counts_match_and_writes_nothing(self):
        before = digest(self.xlsx)
        listing = sorted(p.name for p in self.work.iterdir())
        report = self.migrate(apply=False)
        self.assertTrue(report['ok'])
        self.assertIsNone(report['backup'])
        self.assertEqual(report['sheets']['Jobs']['in'], {'Suggested (not applied)': 1, 'Applied': 1, 'Interview': 1})
        self.assertEqual(report['sheets']['Jobs']['in'], report['sheets']['Jobs']['out'])
        self.assertEqual(report['sheets']['New (2026-01-06)']['out'], {'Suggested (not applied)': 1, 'Skipped': 1})
        self.assertEqual(report['old']['in'], {'Suggested (not applied)': 1, 'Skipped': 1})
        self.assertEqual(report['old']['in'], report['old']['out'])
        self.assertEqual(report['contacts'], {'in': 1, 'out': 1})
        self.assertEqual(report['rows_in'], report['rows_out'])
        self.assertEqual(digest(self.xlsx), before)
        self.assertEqual(sorted(p.name for p in self.work.iterdir()), listing)
        self.assertFalse(store.db_path(self.xlsx).exists())

    def test_real_run_builds_db_backs_up_and_keeps_the_workbook(self):
        before = digest(self.xlsx)
        report = self.migrate()
        self.assertTrue(report['ok'])
        self.assertTrue(store.db_path(self.xlsx).exists())
        self.assertEqual(digest(self.xlsx), before)
        backup = Path(report['backup'])
        self.assertEqual(backup.parent, self.work / 'logs')
        self.assertIn('before-sqlite', backup.name)
        self.assertEqual(digest(backup), before)
        with self.assertRaises(FileExistsError):
            self.migrate()

    def test_tables_exist(self):
        self.migrate()
        with self.db() as db:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('jobs', 'moves', 'interview_rounds', 'sent', 'old_jobs', 'checks', 'contacts'):
            self.assertIn(table, names)

    def test_every_job_keeps_its_original_status_and_fields(self):
        self.migrate()
        with self.db() as db:
            rows = db.execute('SELECT sheet, row, company, role, link, status, old_status FROM jobs').fetchall()
            old = db.execute('SELECT COUNT(*) FROM old_jobs').fetchone()[0]
        self.assertEqual(old, 2)
        by_key = {(r[0], r[1]): r for r in rows}
        self.assertEqual(by_key[('Jobs', 2)][2:], ('Example Co', 'Example Analyst', 'https://example.com/jobs/1?id=1',
                                                   'Suggested (not applied)', 'Suggested (not applied)'))
        self.assertEqual(by_key[('Jobs', 6)][5], 'Interview')
        self.assertEqual(by_key[('New (2026-01-06)', 3)][6], 'Skipped')
        for r in rows:
            if r[2]:
                self.assertEqual(r[5], r[6])

    def test_counts_mismatch_leaves_no_db(self):
        original = store.Store.counts
        def wrong(self_):
            out = original(self_)
            out['sheets']['Jobs']['Applied'] = 99
            return out
        store.Store.counts = wrong
        try:
            with self.assertRaises(RuntimeError):
                self.migrate()
        finally:
            store.Store.counts = original
        self.assertFalse(store.db_path(self.xlsx).exists())

    def test_cli_dry_run_prints_counts(self):
        out = subprocess.run([sys.executable, '-I', str(HERE / 'migrate_to_sqlite.py'), '--workbook', str(self.xlsx),
                              '--old', str(self.old), '--dry-run'], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('Jobs', out.stdout)
        self.assertIn('Applied', out.stdout)
        self.assertIn('Dry run', out.stdout)
        self.assertFalse(store.db_path(self.xlsx).exists())

    def test_cli_apply_then_refuses_again(self):
        args = [sys.executable, '-I', str(HERE / 'migrate_to_sqlite.py'), '--workbook', str(self.xlsx), '--old', str(self.old), '--apply']
        first = subprocess.run(args, capture_output=True, text=True, timeout=120)
        self.assertEqual(first.returncode, 0, first.stderr)
        second = subprocess.run(args, capture_output=True, text=True, timeout=120)
        self.assertNotEqual(second.returncode, 0)


class SiteReadsTheSame(Base):
    def test_load_is_identical_before_and_after(self):
        before = tracker_for(self.xlsx).load()
        self.migrate()
        t = tracker_for(self.xlsx)
        after = t.load()
        self.assertEqual(without_volatile(after), without_volatile(before))
        self.assertEqual(after['store'], 'sqlite')
        self.assertEqual(after['mirrorWarning'], '')

    def test_excel_mode_without_db(self):
        data = tracker_for(self.xlsx).load()
        self.assertEqual(data['store'], 'excel')

    def test_version_is_the_db_revision_and_moves_on_write(self):
        self.migrate()
        t = tracker_for(self.xlsx)
        v1 = t.version()
        self.assertTrue(v1)
        self.assertEqual(json.loads(t.cached())['version'], v1)
        t.update({'sheet': 'Jobs', 'row': 2, 'note': 'Changed', 'expect': {'Company': 'Example Co'}})
        self.assertNotEqual(t.version(), v1)
        self.assertEqual(json.loads(t.cached())['jobs'][0]['Notes'], 'Changed')


class Writes(Base):
    def setUp(self):
        super().setUp()
        self.migrate()
        self.t = tracker_for(self.xlsx)

    def mirror(self):
        return openpyxl.load_workbook(self.xlsx)

    def test_stage_move_writes_one_moves_row(self):
        self.t.update({'sheet': 'Jobs', 'row': 2, 'status': 'Applied', 'date': '2026-01-07', 'expect': {'Company': 'Example Co'}})
        with self.db() as db:
            moves = db.execute('SELECT m.from_status, m.to_status, m.who, m.at, j.company FROM moves m JOIN jobs j ON j.id = m.job_id').fetchall()
            status = db.execute("SELECT status, old_status FROM jobs WHERE sheet='Jobs' AND row=2").fetchone()
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0][:3], ('Suggested (not applied)', 'Applied', 'site'))
        self.assertTrue(moves[0][3])
        self.assertEqual(moves[0][4], 'Example Co')
        self.assertEqual(status, ('Applied', 'Suggested (not applied)'))

    def test_note_only_edit_writes_no_move(self):
        self.t.update({'sheet': 'Jobs', 'row': 2, 'note': 'Just a note', 'expect': {'Company': 'Example Co'}})
        self.t.update({'sheet': 'Jobs', 'row': 2, 'status': 'Suggested (not applied)', 'expect': {'Company': 'Example Co'}})
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM moves').fetchone()[0], 0)

    def test_mirror_is_rebuilt_with_same_sheets_columns_and_types(self):
        self.t.update({'sheet': 'Jobs', 'row': 6, 'status': 'Offer', 'expect': {'Company': 'Demo Inc'}})
        wb = self.mirror()
        self.assertEqual(wb.sheetnames, ['Jobs', 'New (2026-01-06)', 'Contacts', 'Read me'])
        ws = wb['Jobs']
        self.assertEqual([c.value for c in ws[1]], JOB_HEAD)
        self.assertEqual(ws.cell(6, 8).value, 'Offer')
        self.assertEqual(ws.cell(2, 2).value, 'Example Co')
        self.assertIsInstance(ws.cell(2, 5).value, datetime)  # dates stay dates
        self.assertEqual(ws.cell(2, 6).value, 7)
        self.assertEqual(ws.cell(3, 6).value, 6.5)
        self.assertIsNone(ws.cell(4, 2).value)  # the blank row stays blank
        self.assertEqual(ws.cell(5, 12).value, 'A note row with no company')
        self.assertEqual(wb['Read me'].cell(2, 1).value, 'It must survive the move')
        self.assertEqual(wb['Contacts'].cell(2, 1).value, 'Pat Example')
        self.assertEqual(wb['New (2026-01-06)'].cell(3, 1).value, 'N2')
        self.assertTrue(any(dv.formula1 and 'Applied' in dv.formula1 for dv in ws.data_validations.dataValidation))
        self.assertFalse([p.name for p in self.work.iterdir() if 'saving' in p.name or p.suffix == '.tmp'])

    def test_add_gets_next_row_and_id_and_reaches_the_mirror(self):
        row, saved = self.t.add({'sheet': 'New (2026-01-06)', 'Company': 'Added Example', 'Role': 'Added Role', 'Link': 'https://example.com/added'})
        self.assertEqual(row, 4)
        self.assertEqual(saved['#'], 'N3')
        self.assertEqual(saved['My status'], 'Suggested (not applied)')
        self.assertEqual(self.mirror()['New (2026-01-06)'].cell(4, 2).value, 'Added Example')
        jobs = [j for j in self.t.load()['jobs'] if j['Company'] == 'Added Example']
        self.assertEqual((jobs[0]['_sheet'], jobs[0]['_row']), ('New (2026-01-06)', 4))
        row2, saved2 = self.t.add({'sheet': 'Jobs', 'Company': 'Added Two', 'Role': 'R', 'Date applied': '2026-01-08'})
        self.assertEqual((row2, saved2['#']), (7, '5'))  # saved values come back as text, as in Excel mode

    def test_existing_validation_still_applies(self):
        with self.assertRaises(ValueError):
            self.t.update({'sheet': 'Jobs', 'row': 2, 'status': 'Not a stage'})
        with self.assertRaises(ValueError):
            self.t.update({'sheet': 'Jobs', 'row': 2, 'date': '07/01/2026'})
        with self.assertRaises(ValueError):
            self.t.add({'sheet': 'Jobs', 'Company': '', 'Role': 'R'})
        with self.assertRaises(ValueError):
            self.t.add({'sheet': 'Jobs', 'Company': 'C', 'Role': 'R', 'Link': 'ftp://example.com'})
        with self.assertRaises(self.t.Conflict):
            self.t.update({'sheet': 'Jobs', 'row': 2, 'note': 'x', 'expect': {'Company': 'Wrong'}})

    def test_formula_text_stays_text_in_the_mirror(self):
        self.t.update({'sheet': 'Jobs', 'row': 2, 'note': ' =1+1', 'expect': {'Company': 'Example Co'}})
        cell = self.mirror()['Jobs'].cell(2, 12)
        self.assertEqual(cell.data_type, 's')

    def test_contacts_update(self):
        self.t.update({'sheet': 'Contacts', 'row': 2, 'status': 'Sent', 'date': '2026-01-09', 'expect': {'Person': 'Pat Example'}})
        data = self.t.load()
        self.assertEqual(data['contacts'][0]['Status'], 'Sent')
        self.assertEqual(data['contacts'][0]['Date sent'], '2026-01-09')

    def test_excel_edit_is_kept_as_a_copy_and_warned(self):
        self.t.update({'sheet': 'Jobs', 'row': 2, 'note': 'Site note', 'expect': {'Company': 'Example Co'}})
        wb = self.mirror()
        wb['Jobs'].cell(3, 12).value = 'Typed in Excel'
        wb.save(self.xlsx)
        wb.close()
        self.t.update({'sheet': 'Jobs', 'row': 6, 'note': 'Second site note', 'expect': {'Company': 'Demo Inc'}})
        copies = list((self.work / 'logs').glob('applications-tracker-edited-*.xlsx'))
        self.assertEqual(len(copies), 1)
        self.assertEqual(openpyxl.load_workbook(copies[0])['Jobs'].cell(3, 12).value, 'Typed in Excel')
        mirror = self.mirror()['Jobs']
        self.assertIsNone(mirror.cell(3, 12).value)  # the database wins; the edit is not merged
        self.assertEqual(mirror.cell(6, 12).value, 'Second site note')
        warning = self.t.load()['mirrorWarning']
        self.assertIn('Excel', warning)
        self.assertIn(copies[0].name, warning)

    def test_unchanged_mirror_makes_no_copy(self):
        self.t.update({'sheet': 'Jobs', 'row': 2, 'note': 'One', 'expect': {'Company': 'Example Co'}})
        self.t.update({'sheet': 'Jobs', 'row': 2, 'note': 'Two', 'expect': {'Company': 'Example Co'}})
        self.assertFalse(list((self.work / 'logs').glob('*-edited-*')))
        self.assertEqual(self.t.load()['mirrorWarning'], '')

    def test_workbook_open_in_excel_still_saves_and_catches_up_later(self):
        lock = self.xlsx.with_name('~$' + self.xlsx.name)
        lock.touch()
        before = digest(self.xlsx)
        self.t.update({'sheet': 'Jobs', 'row': 2, 'note': 'Saved while Excel was open', 'expect': {'Company': 'Example Co'}})
        self.assertEqual(self.t.load()['jobs'][0]['Notes'], 'Saved while Excel was open')
        self.assertEqual(digest(self.xlsx), before)  # mirror waits
        lock.unlink()
        self.t.update({'sheet': 'Jobs', 'row': 6, 'note': 'Next', 'expect': {'Company': 'Demo Inc'}})
        self.assertEqual(self.mirror()['Jobs'].cell(2, 12).value, 'Saved while Excel was open')
        self.assertFalse(list((self.work / 'logs').glob('*-edited-*')))

    def test_rebuild_mirror_restores_a_deleted_file(self):
        self.xlsx.unlink()
        self.t.update({'sheet': 'Jobs', 'row': 2, 'note': 'Back', 'expect': {'Company': 'Example Co'}})
        self.assertEqual(self.mirror()['Jobs'].cell(2, 12).value, 'Back')


    def test_daily_database_backup_keeps_seven(self):
        backups = self.work / '.tracker-backups'
        backups.mkdir()
        for day in range(1, 10):
            (backups / f'applications-tracker-2025-12-0{day}.sqlite3').write_bytes(b'old')
        self.t.update({'sheet': 'Jobs', 'row': 2, 'note': 'Backed up', 'expect': {'Company': 'Example Co'}})
        today = backups / f'applications-tracker-{date.today().isoformat()}.sqlite3'
        self.assertTrue(today.exists())
        connection = sqlite3.connect(today)
        try:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM jobs WHERE company IS NOT NULL').fetchone()[0], 5)
        finally:
            connection.close()
        self.assertEqual(len(list(backups.glob('applications-tracker-*.sqlite3'))), 7)

    def test_first_seen_is_unknown_for_migrated_rows_and_today_for_new_ones(self):
        self.t.add({'sheet': 'Jobs', 'Company': 'Brand New', 'Role': 'R'})
        with self.db() as db:
            migrated = db.execute("SELECT first_seen FROM jobs WHERE company='Example Co'").fetchone()[0]
            added = db.execute("SELECT first_seen FROM jobs WHERE company='Brand New'").fetchone()[0]
        self.assertIn(migrated, (None, ''))
        self.assertEqual(added, date.today().isoformat())

    def test_posted_text_is_accepted_as_in_excel_mode(self):
        row, saved = self.t.add({'sheet': 'Jobs', 'Company': 'Free Text', 'Role': 'R', 'Posted': '3 days ago'})
        self.assertEqual(saved['Posted'], '3 days ago')



class EditSavesShape(Base):
    """A writer (such as a scan importer) may add a sheet or a column inside one edit."""

    def setUp(self):
        super().setUp()
        self.migrate()

    def edit(self):
        storage = store.Store(store.db_path(self.xlsx))
        self.addCleanup(storage.close)
        return storage.edit()

    def test_new_sheet_is_saved_and_mirrored_last(self):
        with self.edit() as wb:
            ws = wb.create_sheet('New (2026-01-09)')
            ws.append(['#', 'Company', 'Role', 'My status', 'Link', 'Notes'])
            ws.append(['N0109-01', 'Scan Example', 'Scan Role', 'Suggested (not applied)', 'https://example.com/scan/1', 'Plain note'])
            ws.column_dimensions['B'].width = 27
            ws.freeze_panes = 'A2'
        store.rebuild_mirror(self.xlsx)
        data = tracker_for(self.xlsx).load()
        jobs = [j for j in data['jobs'] if j['Company'] == 'Scan Example']
        self.assertEqual(len(jobs), 1)
        self.assertEqual((jobs[0]['_sheet'], jobs[0]['_row'], jobs[0]['Notes']), ('New (2026-01-09)', 2, 'Plain note'))
        self.assertEqual(data['sheets'][-1]['name'], 'New (2026-01-09)')
        wb = openpyxl.load_workbook(self.xlsx)
        self.assertEqual(wb.sheetnames, ['Jobs', 'New (2026-01-06)', 'Contacts', 'Read me', 'New (2026-01-09)'])
        ws = wb['New (2026-01-09)']
        self.assertEqual([c.value for c in ws[1]], ['#', 'Company', 'Role', 'My status', 'Link', 'Notes'])
        self.assertEqual(ws.cell(2, 2).value, 'Scan Example')
        self.assertEqual(ws.column_dimensions['B'].width, 27)
        self.assertEqual(ws.freeze_panes, 'A2')
        self.assertFalse(list((self.work / 'logs').glob('*-edited-*')))
        counts = store.Store(store.db_path(self.xlsx))
        try:
            self.assertEqual(counts.counts()['sheets']['New (2026-01-09)'], {'Suggested (not applied)': 1})
        finally:
            counts.close()

    def test_new_sheet_survives_a_later_edit(self):
        with self.edit() as wb:
            ws = wb.create_sheet('New (2026-01-09)')
            ws.append(['#', 'Company', 'Role', 'My status'])
            ws.append(['N1', 'Scan Example', 'Scan Role', 'Suggested (not applied)'])
        t = tracker_for(self.xlsx)
        t.update({'sheet': 'New (2026-01-09)', 'row': 2, 'status': 'Skipped', 'expect': {'Company': 'Scan Example'}})
        jobs = [j for j in t.load()['jobs'] if j['Company'] == 'Scan Example']
        self.assertEqual(jobs[0]['My status'], 'Skipped')
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM moves').fetchone()[0], 1)

    def test_new_column_on_an_existing_sheet_is_saved(self):
        with self.edit() as wb:
            ws = wb['Jobs']
            col = ws.max_column + 1
            ws.cell(1, col).value = 'Open check 2026-01-09'
            ws.cell(2, col).value = 'OPEN (fictional check)'
        data = tracker_for(self.xlsx).load()
        jobs = data['sheets'][0]['columns']
        self.assertEqual(jobs[-1], 'Open check 2026-01-09')
        first = [j for j in data['jobs'] if j['Company'] == 'Example Co'][0]
        self.assertEqual(first['Open check 2026-01-09'], 'OPEN (fictional check)')
        self.assertEqual(first['_check'], 'OPEN (fictional check)')
        store.rebuild_mirror(self.xlsx)
        ws = openpyxl.load_workbook(self.xlsx)['Jobs']
        self.assertEqual([c.value for c in ws[1]], JOB_HEAD + ['Open check 2026-01-09'])
        self.assertEqual(ws.cell(6, 2).value, 'Demo Inc')  # other rows untouched

    def test_an_edit_that_fails_saves_nothing(self):
        before = tracker_for(self.xlsx).load()
        with self.assertRaises(RuntimeError):
            with self.edit() as wb:
                wb.create_sheet('New (2026-01-09)').append(['#', 'Company', 'Role', 'My status'])
                raise RuntimeError('refuse')
        self.assertEqual(without_volatile(tracker_for(self.xlsx).load()), without_volatile(before))


class HQServerOnSQLite(Base):
    """The phone/PC server keeps its API when the tracker moves to SQLite."""

    def setUp(self):
        super().setUp()
        self.migrate()
        hq = load_module('hq_server_under_test', HERE.parent / 'hq-dashboard' / 'server.py')
        self.t = hq.load_tracker(self.xlsx)

    def test_hq_tracker_reads_sqlite(self):
        data = json.loads(self.t.cached())
        self.assertEqual(data['store'], 'sqlite')
        self.assertEqual(len(data['jobs']), 5)
        self.assertTrue(self.t.version())


if __name__ == '__main__':
    unittest.main(verbosity=2)
