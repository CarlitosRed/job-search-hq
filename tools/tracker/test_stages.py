"""Offline checks for the board stages (build step 01a).

Every workbook here is fictional and lives in a temporary folder.
"""
from datetime import date
import importlib.util
import io
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import openpyxl
from openpyxl.worksheet.datavalidation import DataValidation

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
NAMES = ['Tracked', 'Review', 'Apply', 'Skipped', 'Applied', 'Interview', 'Offer', 'Rejected']
IDS = [n.lower() for n in NAMES]
LEGACY = ['Suggested (not applied)', 'Message drafted', 'CV ready']
JOB_HEAD = ['#', 'Company', 'Role', 'Where', 'Posted', 'Score', 'Verdict', 'My status', 'Date applied', 'Link', 'Notes']


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stages = load_module('tracker_stages_under_test', HERE / 'stages.py')
store = load_module('tracker_store_for_stages', HERE / 'store.py')
_count = 0


def tracker_for(xlsx):
    global _count
    _count += 1
    module = load_module(f'tracker_for_stages_{_count}', HERE / 'server.py')
    module.XLSX = Path(xlsx).resolve()
    module.BACKUPS = module.XLSX.parent / '.tracker-backups'
    return module


def migration():
    return load_module('tracker_stage_migration', HERE / 'migrate_stages.py')


# (company, score, verdict, old status, expected stage)
ROWS = [
    ('Fit Co', 7, 'Good', 'Suggested (not applied)', 'Review'),
    ('Unscored Co', None, None, 'Suggested (not applied)', 'Tracked'),
    ('Blank Co', None, None, None, 'Tracked'),
    ('Blank Fit Co', 6, 'Stretch', None, 'Review'),
    ('Draft Co', 6, 'Stretch', 'Message drafted', 'Apply'),
    ('Ready Co', 8, 'Good', 'CV ready', 'Apply'),
    ('Sent Co', 8, 'Good', 'Applied', 'Applied'),
    ('Talk Co', 8, 'Good', 'Interview', 'Interview'),
    ('Win Co', 8, 'Good', 'Offer', 'Offer'),
    ('No Co', 8, 'Good', 'Rejected', 'Rejected'),
    ('Pass Co', 3, 'Low', 'Skipped', 'Skipped'),
    ('Odd Co', 5, 'Good', 'On hold??', 'On hold??'),
]


def make_tracker(folder):
    """A fictional tracker in the pre-01 statuses, plus an archive file."""
    xlsx = Path(folder) / 'applications-tracker.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Jobs'
    ws.append(JOB_HEAD)
    for n, (company, score, verdict, status, _) in enumerate(ROWS, 1):
        ws.append([n, company, 'Example Role', 'Remote', date(2026, 1, 2), score, verdict, status,
                   date(2026, 1, 3) if status in ('Applied', 'Interview', 'Offer', 'Rejected') else None,
                   f'https://example.com/jobs/{n}', None])
    dv = DataValidation(type='list', formula1='"' + ','.join(LEGACY + ['Applied', 'Interview', 'Rejected', 'Offer', 'Skipped']) + '"')
    ws.add_data_validation(dv)
    dv.add('H2:H200')
    other = wb.create_sheet('New (2026-01-06)')
    other.append(JOB_HEAD)
    other.append([1, 'Scan Co', 'Scan Role', 'Remote', None, 6, 'Good', 'Suggested (not applied)', None, 'https://example.com/n1', None])
    contacts = wb.create_sheet('Contacts')
    contacts.append(['Person', 'Where now', 'How you know them', 'Status', 'Date sent', 'Reply', 'Notes'])
    contacts.append(['Example Person', 'Example Co', 'Fictional', 'Message drafted', None, None, None])
    wb.save(xlsx)
    wb.close()
    old = Path(folder) / 'applications-tracker-old.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Old'
    ws.append(JOB_HEAD)
    ws.append([1, 'Archived Co', 'Old Role', 'Remote', None, 5, 'Good', 'Suggested (not applied)', None, 'https://example.com/o1', None])
    wb.save(old)
    wb.close()
    return xlsx, old


def statuses_in(xlsx):
    wb = openpyxl.load_workbook(xlsx)
    try:
        out = {}
        for ws in wb:
            head = [c.value for c in ws[1]]
            if 'My status' not in head:
                continue
            col = head.index('My status')
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row[1]:
                    out[(ws.title, row[1])] = row[col]
        return out
    finally:
        wb.close()


def validation_lists(xlsx):
    wb = openpyxl.load_workbook(xlsx)
    try:
        return [dv.formula1 for ws in wb for dv in ws.data_validations.dataValidation]
    finally:
        wb.close()


class StageList(unittest.TestCase):
    """One stage list, in one file, read by everything else."""

    def test_file_shape(self):
        data = json.loads((HERE / 'stages.json').read_text(encoding='utf-8'))
        self.assertEqual([s['name'] for s in data['stages']], NAMES)
        self.assertEqual([s['id'] for s in data['stages']], IDS)
        colors = {s['name']: s['color'] for s in data['stages']}
        self.assertEqual(colors.pop('Offer'), 'green')
        self.assertEqual(colors.pop('Rejected'), 'red')
        self.assertEqual(set(colors.values()), {'gray'})
        self.assertTrue(all(s.get('means') for s in data['stages']))
        self.assertEqual(data['new_row'], 'Tracked')
        self.assertEqual(data['rejected_from'], ['Applied', 'Interview', 'Offer'])
        self.assertEqual(data['legacy'], {'Suggested (not applied)': 'Review', 'Message drafted': 'Apply', 'CV ready': 'Apply'})
        self.assertEqual(data['without_fit'], {'Review': 'Tracked'})

    def test_module_reads_the_file(self):
        self.assertEqual(list(stages.NAMES), NAMES)
        self.assertEqual(stages.NEW_ROW, 'Tracked')
        self.assertEqual([s['name'] for s in stages.STAGES], NAMES)

    def test_allowed_moves(self):
        for before in ('Tracked', 'Review', 'Apply', 'Skipped'):
            self.assertFalse(stages.allowed(before, 'Rejected'), before)
        for before in ('Applied', 'Interview', 'Offer'):
            self.assertTrue(stages.allowed(before, 'Rejected'), before)
        # Going back one stage (undo) is always possible, also out of a closed stage.
        for before, after in (('Review', 'Tracked'), ('Apply', 'Review'), ('Applied', 'Apply'), ('Interview', 'Applied'),
                              ('Offer', 'Interview'), ('Rejected', 'Applied'), ('Rejected', 'Interview'),
                              ('Rejected', 'Offer'), ('Skipped', 'Apply'), ('Skipped', 'Review'), ('Skipped', 'Tracked')):
            self.assertTrue(stages.allowed(before, after), (before, after))
        for before in ('Tracked', 'Review', 'Apply'):
            self.assertTrue(stages.allowed(before, 'Skipped'), before)
        self.assertTrue(stages.allowed('Tracked', 'Applied'))
        self.assertTrue(stages.allowed('Rejected', 'Rejected'))
        for bad in LEGACY + ['', 'On hold??', None]:
            self.assertFalse(stages.allowed('Review', bad), bad)
        # A row still in an old status can move to any new stage except Rejected.
        self.assertTrue(stages.allowed('CV ready', 'Apply'))
        self.assertFalse(stages.allowed('CV ready', 'Rejected'))

    def test_legacy_mapping(self):
        for _, score, verdict, status, expected in ROWS:
            self.assertEqual(stages.from_legacy(status, has_fit=bool(score or verdict)),
                             None if expected == 'On hold??' else expected, status)
        for name in NAMES:
            self.assertEqual(stages.from_legacy(name, has_fit=False), name)

    def test_only_one_copy_of_the_list(self):
        """No code file outside stages.json spells out the stage list or the old status list."""
        skip = {'stages.json'}
        found = []
        for path in ROOT.rglob('*'):
            if (not path.is_file() or path.suffix not in ('.py', '.js', '.html', '.ps1') or path.name in skip
                    or path.name.startswith('test_') or '.git' in path.parts or path.name in ('index.html', 'pc-index.html')
                    and path.parent.name == 'hq-dashboard'):
                continue
            text = path.read_text(encoding='utf-8', errors='ignore')
            quoted = set(re.findall(r'''["']([A-Z][A-Za-z ()]+)["']''', text))
            if len(quoted & set(NAMES)) >= 4:
                found.append(f'{path.relative_to(ROOT)}: stage names')
            for old in LEGACY:
                if re.search(r'''["']''' + re.escape(old) + r'''["']''', text):
                    found.append(f'{path.relative_to(ROOT)}: {old}')
        self.assertEqual(found, [])


class ServerRules(unittest.TestCase):
    """The tracker accepts only the new stages and refuses Rejected before Applied."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hq-stages-')
        self.xlsx, _ = make_tracker(self.tmp.name)
        wb = openpyxl.load_workbook(self.xlsx)
        for row in wb['Jobs'].iter_rows(min_row=2):
            company, status = row[1].value, row[7].value
            row[7].value = next(e for c, _, _, _, e in ROWS if c == company)
        wb['New (2026-01-06)']['H2'] = 'Review'
        wb.save(self.xlsx)
        wb.close()
        self.t = tracker_for(self.xlsx)

    def tearDown(self):
        self.tmp.cleanup()

    def row_of(self, company):
        return 2 + [r[0] for r in ROWS].index(company)

    def move(self, company, status):
        self.t.update({'sheet': 'Jobs', 'row': self.row_of(company), 'status': status,
                       'expect': {'Company': company, 'Role': 'Example Role'}})

    def status(self, company):
        return next(j['My status'] for j in self.t.load()['jobs'] if j['Company'] == company)

    def test_statuses_are_the_stages(self):
        self.assertEqual(list(self.t.load()['statuses']), NAMES)

    def test_rejected_only_after_applied(self):
        with self.assertRaises(ValueError):
            self.move('Fit Co', 'Rejected')
        self.assertEqual(self.status('Fit Co'), 'Review')
        self.move('Sent Co', 'Rejected')
        self.assertEqual(self.status('Sent Co'), 'Rejected')
        self.move('Sent Co', 'Applied')  # undo
        self.assertEqual(self.status('Sent Co'), 'Applied')

    def test_old_status_names_refused(self):
        for old in LEGACY:
            with self.assertRaises(ValueError):
                self.move('Fit Co', old)
        self.move('Fit Co', 'Apply')
        self.move('Fit Co', 'Review')
        self.assertEqual(self.status('Fit Co'), 'Review')

    def test_new_row_starts_tracked(self):
        self.t.add({'sheet': 'Jobs', 'Company': 'Added Co', 'Role': 'Added Role'})
        self.assertEqual(self.status('Added Co'), 'Tracked')
        with self.assertRaises(ValueError):
            self.t.add({'sheet': 'Jobs', 'Company': 'Bad Co', 'Role': 'Bad Role', 'My status': 'CV ready'})

    def test_sqlite_move_writes_history_and_mirror_dropdown(self):
        self.assertTrue(store.migrate(self.xlsx, apply=True)['ok'])
        self.move('Fit Co', 'Apply')
        db = sqlite3.connect(store.db_path(self.xlsx))
        try:
            moves = db.execute('SELECT from_status, to_status FROM moves').fetchall()
        finally:
            db.close()
        self.assertEqual(moves, [('Review', 'Apply')])
        lists = validation_lists(self.xlsx)
        self.assertEqual(lists, ['"' + ','.join(NAMES) + '"'])
        with self.assertRaises(ValueError):
            self.move('Fit Co', 'Rejected')


class Migration(unittest.TestCase):
    """migrate_stages.py moves the old statuses to the new stages, dry run first."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hq-stage-move-')
        self.xlsx, self.old = make_tracker(self.tmp.name)
        self.m = migration()

    def tearDown(self):
        self.tmp.cleanup()

    def expected(self):
        out = {('Jobs', c): e for c, _, _, _, e in ROWS}
        out[('New (2026-01-06)', 'Scan Co')] = 'Review'
        return out

    def check_report(self, report):
        self.assertTrue(report['ok'])
        jobs = report['sheets']['Jobs']
        self.assertEqual(sum(jobs['in'].values()), len(ROWS))
        self.assertEqual(sum(jobs['out'].values()), len(ROWS))
        self.assertEqual(jobs['out']['Apply'], 2)
        self.assertEqual(jobs['out']['Tracked'], 2)
        self.assertEqual(jobs['out']['Review'], 2)
        self.assertEqual(jobs['out']['On hold??'], 1)
        self.assertEqual(report['unknown'], {'On hold??': 1})
        self.assertEqual(report['changed'], 7)
        self.assertEqual(report['rows_in'], report['rows_out'])

    def test_dry_run_excel_writes_nothing(self):
        before = self.xlsx.read_bytes()
        report = self.m.migrate(self.xlsx, apply=False)
        self.check_report(report)
        self.assertEqual(self.xlsx.read_bytes(), before)
        self.assertFalse((self.xlsx.parent / 'logs').exists())

    def test_apply_excel(self):
        report = self.m.migrate(self.xlsx, apply=True)
        self.check_report(report)
        self.assertEqual({k: v for k, v in statuses_in(self.xlsx).items()}, self.expected())
        self.assertEqual(validation_lists(self.xlsx), ['"' + ','.join(NAMES) + '"'])
        backups = list((self.xlsx.parent / 'logs').glob('applications-tracker-before-stages-*.xlsx'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(statuses_in(backups[0])[('Jobs', 'Ready Co')], 'CV ready')
        # Running it again changes nothing.
        self.assertEqual(self.m.migrate(self.xlsx, apply=True)['changed'], 0)

    def test_dry_run_sqlite_writes_nothing(self):
        self.assertTrue(store.migrate(self.xlsx, old=self.old, apply=True)['ok'])
        db_file = store.db_path(self.xlsx)
        before = db_file.read_bytes()
        self.check_report(self.m.migrate(self.xlsx, apply=False))
        self.assertEqual(db_file.read_bytes(), before)

    def test_apply_sqlite(self):
        self.assertTrue(store.migrate(self.xlsx, old=self.old, apply=True)['ok'])
        db_file = store.db_path(self.xlsx)
        report = self.m.migrate(self.xlsx, apply=True)
        self.check_report(report)
        self.assertEqual(report['old_jobs'], {'in': 1, 'out': 1})
        db = sqlite3.connect(db_file)
        db.row_factory = sqlite3.Row
        try:
            jobs = {(r['sheet'], r['company']): r for r in db.execute('SELECT * FROM jobs')}
            for key, stage in self.expected().items():
                self.assertEqual(jobs[key]['status'], stage, key)
                fields = json.loads(jobs[key]['fields'])
                self.assertIn(stage, json.dumps(fields))
            self.assertEqual(jobs[('Jobs', 'Ready Co')]['old_status'], 'CV ready')
            self.assertEqual(jobs[('Jobs', 'Unscored Co')]['old_status'], 'Suggested (not applied)')
            moves = db.execute('SELECT from_status, to_status, who FROM moves').fetchall()
            self.assertEqual(len(moves), report['changed'])
            self.assertEqual({m['who'] for m in moves}, {'stages-01'})
            self.assertIn(('CV ready', 'Apply'), [(m['from_status'], m['to_status']) for m in moves])
            # The archive and the contacts are not touched.
            self.assertEqual(db.execute('SELECT status FROM old_jobs').fetchone()[0], 'Suggested (not applied)')
            self.assertIn('Message drafted', db.execute('SELECT fields FROM contacts').fetchone()[0])
        finally:
            db.close()
        backups = list((self.xlsx.parent / 'logs').glob('applications-tracker-before-stages-*.sqlite3'))
        self.assertEqual(len(backups), 1)
        # The Excel copy is rebuilt from the database with the new stages.
        self.assertEqual(statuses_in(self.xlsx), self.expected())
        self.assertEqual(validation_lists(self.xlsx), ['"' + ','.join(NAMES) + '"'])
        # The site reads the new stages.
        t = tracker_for(self.xlsx)
        self.assertEqual({j['Company']: j['My status'] for j in t.load()['jobs']}['Draft Co'], 'Apply')
        self.assertEqual(self.m.migrate(self.xlsx, apply=True)['changed'], 0)

    def test_cli(self):
        cmd = [sys.executable, '-I', str(HERE / 'migrate_stages.py'), '--workbook', str(self.xlsx)]
        dry = subprocess.run(cmd + ['--dry-run'], capture_output=True, text=True, timeout=60)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn('CV ready', dry.stdout)
        self.assertIn('Apply', dry.stdout)
        self.assertIn('On hold??', dry.stdout)
        self.assertEqual(statuses_in(self.xlsx)[('Jobs', 'Ready Co')], 'CV ready')
        none = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(none.returncode, 0)
        done = subprocess.run(cmd + ['--apply'], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(statuses_in(self.xlsx)[('Jobs', 'Ready Co')], 'Apply')


class PhonePage(unittest.TestCase):
    """The old claude.ai phone page is retired; the phone uses the main page."""

    def test_old_phone_page_sources_gone(self):
        dash = ROOT / 'tools' / 'hq-dashboard'
        self.assertFalse((dash / 'hosted' / 'data.js').exists())
        self.assertFalse((dash / 'sync_page.py').exists())

    def test_generator_writes_only_the_main_page(self):
        dash = ROOT / 'tools' / 'hq-dashboard'
        source = (dash / 'port_tracker.py').read_text(encoding='utf-8')
        self.assertNotIn("'claude'", source)
        bad = subprocess.run([sys.executable, '-I', str(dash / 'port_tracker.py'), '--backend', 'claude'],
                             capture_output=True, text=True, timeout=120)
        self.assertNotEqual(bad.returncode, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
