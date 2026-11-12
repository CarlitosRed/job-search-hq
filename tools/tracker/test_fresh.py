"""Offline checks for fresh-job dates, the 45-day window and the duplicate key (build step 02a).

Every workbook here is fictional and lives in a temporary folder.
"""
from datetime import date, datetime, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import openpyxl

HERE = Path(__file__).resolve().parent
TODAY = date.today()
JOB_HEAD = ['#', 'Company', 'Role', 'Where', 'Posted', 'Score', 'Verdict', 'My status', 'Date applied', 'Link', 'Notes']


def D(days):
    return (TODAY - timedelta(days=days)).isoformat()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fresh = load_module('tracker_freshness_under_test', HERE / 'freshness.py')
store = load_module('tracker_store_for_fresh', HERE / 'store.py')
_count = 0


def tracker_for(xlsx):
    global _count
    _count += 1
    module = load_module(f'tracker_for_fresh_{_count}', HERE / 'server.py')
    module.XLSX = Path(xlsx).resolve()
    module.BACKUPS = module.XLSX.parent / '.tracker-backups'
    return module


def migration():
    return load_module('tracker_date_migration', HERE / 'migrate_dates.py')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# (company, role, place, posted, status, first check value, second check value)
JOBS = [
    ('Lever Co', 'Ops Analyst', 'Remote', f'{D(10)} (Lever created)', 'Review', None, 'OPEN (fictional)'),
    ('About Co', 'Ops Analyst', 'Remote', f'about {D(30)} (Lever creation date)', 'Review', 'OPEN (fictional)', 'OPEN (fictional)'),
    ('Old Review Co', 'Analyst', 'Remote', D(70), 'Review', None, None),
    ('Old Applied Co', 'Analyst', 'Remote', datetime.combine(TODAY - timedelta(days=80), datetime.min.time()), 'Applied', None, None),
    ('Not Shown Co', 'Analyst', 'Remote', 'not shown', 'Skipped', None, None),
    ('Word First Co', 'Analyst', 'Remote', f'Start date field {D(3)} (Example careers page)', 'Tracked', None, None),
    ('LinkedIn Co', 'Analyst', 'Remote', f'{D(2)} (LinkedIn: 2 days ago)', 'Apply', None, None),
    ('GH Co', 'Analyst', 'Remote', f'{D(4)} (Greenhouse first published)', 'Review', None, None),
    ('Ashby Co', 'Analyst', 'Remote', f'{D(6)} (Ashby published)', 'Review', None, None),
]
SHEET = f'New ({D(62)})'
# (company, posted, status) on the dated sheet
SHEET_JOBS = [('Sheet Co', None, 'Review'), ('Sheet Posted Co', D(50), 'Tracked')]


def make_tracker(folder):
    xlsx = Path(folder) / 'applications-tracker.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Jobs'
    ws.append(JOB_HEAD + [f'Open check {D(20)}', f'Open check {D(5)}'])
    for n, (company, role, place, posted, status, c1, c2) in enumerate(JOBS, 1):
        ws.append([n, company, role, place, posted, 6, 'Good', status, None, f'https://example.com/jobs/{n}', None, c1, c2])
    other = wb.create_sheet(SHEET)
    other.append(JOB_HEAD)
    for n, (company, posted, status) in enumerate(SHEET_JOBS, 1):
        other.append([n, company, 'Example Role', 'Remote', posted, 6, 'Good', status, None, f'https://example.com/s{n}', None])
    contacts = wb.create_sheet('Contacts')
    contacts.append(['Person', 'Where now', 'How you know them', 'Status', 'Date sent', 'Reply', 'Notes'])
    contacts.append(['Example Person', 'Example Co', 'Fictional', 'Message drafted', None, None, None])
    wb.save(xlsx)
    wb.close()
    old = Path(folder) / 'applications-tracker-old.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Old'
    ws.append(JOB_HEAD + ['From sheet', 'Archived on'])
    ws.append([1, 'Archived Co', 'Old Role', 'Remote', None, 5, 'Good', 'Review', None, 'https://example.com/o1', None, f'New ({D(90)})', D(30)])
    ws.append([2, 'Archived Two', 'Data Analyst', 'Remote (LATAM)', D(100), 5, 'Good', 'Skipped', None, 'https://example.com/o2', None, 'Jobs', D(30)])
    wb.save(old)
    wb.close()
    return xlsx, old


class Rules(unittest.TestCase):
    """The one definition of dates, age, window and duplicate key."""

    def test_constants(self):
        self.assertEqual(fresh.WINDOW_DAYS, 45)
        self.assertEqual(fresh.ARCHIVE_DAYS, 60)
        self.assertEqual(tuple(fresh.KEEP_STAGES), ('Applied', 'Interview', 'Offer', 'Rejected'))
        self.assertEqual(tuple(fresh.ARCHIVE_STAGES), ('Tracked', 'Review', 'Apply', 'Skipped'))
        self.assertEqual(set(fresh.SOURCES), {'greenhouse', 'lever', 'ashby', 'linkedin', 'page', 'first_seen'})

    def test_parse_posted_real_shapes(self):
        cases = {
            '2026-11-02 (Lever created)': ('2026-11-02', 'lever'),
            'about 2026-10-15 (Lever creation date)': ('2026-10-15', 'lever'),
            '2026-11-04 (LinkedIn: 2 days ago)': ('2026-11-04', 'linkedin'),
            '2026-11-01 (GH updated)': ('2026-11-01', 'greenhouse'),
            '2026-11-03 (Greenhouse first published)': ('2026-11-03', 'greenhouse'),
            '2026-11-05 (Ashby published)': ('2026-11-05', 'ashby'),
            '2026-11-06 (Workday start date)': ('2026-11-06', 'page'),
            '2026-11-06': ('2026-11-06', 'page'),
            '  2026-11-06  ': ('2026-11-06', 'page'),
            'Start date field 2026-11-07 (Example careers page)': ('2026-11-07', 'page'),
            '2026-11-01 (Clever careers page)': ('2026-11-01', 'page'),
            '2026-11-01 (SmartRecruiters released)': ('2026-11-01', 'page'),
            'not shown': (None, 'first_seen'),
            'Unknown': (None, 'first_seen'),
            '': (None, 'first_seen'),
            None: (None, 'first_seen'),
            '2026-13-40 (Lever created)': (None, 'first_seen'),
            datetime(2026, 1, 2): ('2026-01-02', 'page'),
            date(2026, 1, 3): ('2026-01-03', 'page'),
            '2026-01-02T00:00:00': ('2026-01-02', 'page'),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(fresh.parse_posted(value), expected)

    def test_about_marks_estimated_dates(self):
        self.assertTrue(fresh.is_about('lever', 'about 2026-10-15 (Lever creation date)'))
        self.assertTrue(fresh.is_about('linkedin', '2026-11-04 (LinkedIn: 2 days ago)'))
        self.assertTrue(fresh.is_about('ashby', '2026-11-05'))
        self.assertTrue(fresh.is_about('first_seen', ''))
        self.assertTrue(fresh.is_about('page', 'About 2026-11-01'))
        self.assertFalse(fresh.is_about('page', '2026-11-06'))
        self.assertFalse(fresh.is_about('greenhouse', '2026-11-03 (Greenhouse)'))
        self.assertFalse(fresh.is_about('lever', None))

    def test_sheet_date(self):
        self.assertEqual(fresh.sheet_date('New (2026-11-05)'), '2026-11-05')
        self.assertEqual(fresh.sheet_date('New (2026-11-07 b)'), '2026-11-07')
        self.assertEqual(fresh.sheet_date('Europe (2026-10-28)'), '2026-10-28')
        self.assertIsNone(fresh.sheet_date('Jobs'))
        self.assertIsNone(fresh.sheet_date('New leads (evening)'))
        self.assertIsNone(fresh.sheet_date(None))

    def test_age_uses_the_older_date(self):
        day = date(2026, 11, 11)
        self.assertEqual(fresh.age_days('2026-11-01', '2026-10-01', day), 41)
        self.assertEqual(fresh.age_days('2026-10-01', '2026-11-01', day), 41)
        self.assertEqual(fresh.age_days(None, '2026-11-01', day), 10)
        self.assertEqual(fresh.age_days('2026-11-10', None, day), 1)
        self.assertEqual(fresh.age_days('', '', day), None)
        self.assertEqual(fresh.age_days(None, None, day), None)

    def test_repost_with_fresh_date_still_counts_as_old(self):
        day = date(2026, 11, 11)
        age = fresh.age_days('2026-11-10', '2026-08-01', day)
        self.assertEqual(age, 102)
        self.assertFalse(fresh.in_window(age))

    def test_window_and_recheck(self):
        self.assertTrue(fresh.in_window(45))
        self.assertFalse(fresh.in_window(46))
        self.assertTrue(fresh.in_window(None), 'no date yet counts as fresh')
        for stage in ('Applied', 'Interview', 'Offer'):
            self.assertTrue(fresh.recheck(stage, 300), stage)
        self.assertFalse(fresh.recheck('Rejected', 300))
        self.assertFalse(fresh.recheck('Review', 100))
        self.assertFalse(fresh.recheck('Apply', 46))
        self.assertTrue(fresh.recheck('Review', 10))
        self.assertTrue(fresh.recheck('Tracked', None))

    def test_job_key_exact_match_only(self):
        key = fresh.job_key('Acme, Inc.', 'Senior  Ops Analyst', 'Remote')
        self.assertIsNotNone(key)
        self.assertEqual(key, fresh.job_key('ACME INC', ' senior ops analyst ', 'remote'))
        self.assertEqual(key, fresh.job_key('Ａcme Inc', 'Senior Ops-Analyst', 'Remote'))
        self.assertNotEqual(key, fresh.job_key('Acme Inc', 'Senior Ops Analyst', 'Remote (LATAM)'))
        self.assertNotEqual(key, fresh.job_key('Acme Inc', 'Ops Analyst', 'Remote'))
        self.assertNotEqual(fresh.job_key('Café Co', 'Analyst', 'Remote'), fresh.job_key('Cafe Co', 'Analyst', 'Remote'))
        self.assertIsNone(fresh.job_key('', 'Analyst', 'Remote'))
        self.assertIsNone(fresh.job_key('Acme', '  ', 'Remote'))
        self.assertIsNone(fresh.job_key('Acme', None, 'Remote'))
        self.assertEqual(fresh.job_key('Acme', 'Analyst', None), fresh.job_key('acme', 'analyst', ''),
                         'an empty place is part of the exact key')


class StoreDates(unittest.TestCase):
    """The database keeps First seen, Posted on and where the date came from."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hq-fresh-')
        self.xlsx, self.old = make_tracker(self.tmp.name)
        self.assertTrue(store.migrate(self.xlsx, old=self.old, apply=True)['ok'])
        self.db = store.db_path(self.xlsx)

    def tearDown(self):
        self.tmp.cleanup()

    def columns(self, table='jobs'):
        con = sqlite3.connect(self.db)
        try:
            return [r[1] for r in con.execute(f'PRAGMA table_info({table})')]
        finally:
            con.close()

    def row(self, company, table='jobs'):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            return dict(con.execute(f'SELECT * FROM {table} WHERE company=?', (company,)).fetchone())
        finally:
            con.close()

    def test_new_databases_have_posted_on(self):
        self.assertIn('posted_on', self.columns('jobs'))
        self.assertIn('posted_on', self.columns('old_jobs'))

    def test_older_database_gets_the_column_on_open(self):
        con = sqlite3.connect(self.db)
        con.execute('ALTER TABLE jobs DROP COLUMN posted_on')
        con.execute('ALTER TABLE old_jobs DROP COLUMN posted_on')
        con.commit()
        con.close()
        store.Store(self.db).close()
        self.assertIn('posted_on', self.columns('jobs'))
        self.assertIn('posted_on', self.columns('old_jobs'))

    def test_import_reads_posted_dates(self):
        lever = self.row('Lever Co')
        self.assertEqual((lever['posted_on'], lever['date_source']), (D(10), 'lever'))
        shown = self.row('Not Shown Co')
        self.assertEqual((shown['posted_on'], shown['date_source']), (None, 'first_seen'))
        applied = self.row('Old Applied Co')
        self.assertEqual((applied['posted_on'], applied['date_source']), (D(80), 'page'))
        old = self.row('Archived Two', 'old_jobs')
        self.assertEqual((old['posted_on'], old['date_source']), (D(100), 'page'))

    def edit(self, change):
        storage = store.Store(self.db)
        try:
            with storage.edit() as wb:
                change(wb)
        finally:
            storage.close()

    def test_new_row_gets_first_seen_today_and_its_posted_date(self):
        def add(wb):
            ws = wb[SHEET]
            ws.append([3, 'Fresh Co', 'New Role', 'Remote', f'{D(3)} (Greenhouse)', 7, 'Good', 'Tracked', None,
                       'https://example.com/s3', None])
        self.edit(add)
        row = self.row('Fresh Co')
        self.assertEqual(row['first_seen'], TODAY.isoformat())
        self.assertEqual((row['posted_on'], row['date_source']), (D(3), 'greenhouse'))

    def test_posted_change_rereads_date_and_first_seen_never_changes(self):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE jobs SET first_seen='2026-01-01' WHERE company='Lever Co'")
        con.commit()
        con.close()

        def repost(wb):
            ws = wb['Jobs']
            ws.cell(2, 5).value = f'{D(1)} (LinkedIn: 1 day ago)'
        self.edit(repost)
        row = self.row('Lever Co')
        self.assertEqual((row['posted_on'], row['date_source']), (D(1), 'linkedin'))
        self.assertEqual(row['first_seen'], '2026-01-01')

        def note(wb):
            wb['Jobs'].cell(2, 11).value = 'A plain note'
        self.edit(note)
        row = self.row('Lever Co')
        self.assertEqual((row['posted_on'], row['date_source'], row['first_seen']), (D(1), 'linkedin', '2026-01-01'))

    def test_known_keys_cover_jobs_and_old(self):
        storage = store.Store(self.db)
        try:
            keys = storage.known_keys()
        finally:
            storage.close()
        self.assertIn(fresh.job_key('Lever Co', 'Ops Analyst', 'Remote'), keys)
        self.assertIn(fresh.job_key('archived co', 'OLD ROLE', 'remote'), keys)
        self.assertIn(fresh.job_key('Archived Two', 'Data Analyst', 'Remote (LATAM)'), keys)
        self.assertNotIn(None, keys)
        self.assertNotIn(fresh.job_key('Example Person', 'Example Co', 'Fictional'), keys, 'contacts are not jobs')

    def test_dates_by_sheet_and_row(self):
        storage = store.Store(self.db)
        try:
            dates = storage.dates()
        finally:
            storage.close()
        self.assertEqual(dates[('Jobs', 2)]['posted_on'], D(10))
        self.assertEqual(dates[('Jobs', 2)]['date_source'], 'lever')
        self.assertIn('first_seen', dates[('Jobs', 2)])
        self.assertEqual(len(dates), len(JOBS) + len(SHEET_JOBS))


class DateMigration(unittest.TestCase):
    """migrate_dates.py fills every job's dates once, after a dry run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hq-fresh-mig-')
        self.xlsx, self.old = make_tracker(self.tmp.name)
        self.assertTrue(store.migrate(self.xlsx, old=self.old, apply=True)['ok'])
        self.db = store.db_path(self.xlsx)
        # A database as it is today: no dates filled, no posted_on column.
        con = sqlite3.connect(self.db)
        for table in ('jobs', 'old_jobs'):
            con.execute(f"UPDATE {table} SET date_source='', first_seen=NULL")
            con.execute(f'ALTER TABLE {table} DROP COLUMN posted_on')
        con.commit()
        con.close()
        self.m = migration()

    def tearDown(self):
        self.tmp.cleanup()

    def query(self, sql, args=()):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, args)]
        finally:
            con.close()

    def by_company(self, table='jobs'):
        return {r['company']: r for r in self.query(f'SELECT * FROM {table}')}

    def test_dry_run_writes_nothing(self):
        before = digest(self.db)
        xlsx_before = digest(self.xlsx)
        report = self.m.migrate(self.xlsx)
        self.assertTrue(report['ok'])
        self.assertEqual(digest(self.db), before, 'not even the new column is added')
        self.assertEqual(digest(self.xlsx), xlsx_before)
        self.assertIsNone(report['backup'])
        self.assertEqual(report['jobs'], len(JOBS) + len(SHEET_JOBS))
        self.assertEqual(report['old_jobs'], 2)
        self.assertEqual(report['filled']['first_seen'], len(JOBS) + len(SHEET_JOBS) + 2)
        self.assertEqual(report['would_archive'], 3)
        self.assertEqual(report['no_posted_date'], 2)
        self.assertEqual(report['by_source'], {'lever': 2, 'page': 4, 'first_seen': 2, 'linkedin': 1,
                                               'greenhouse': 1, 'ashby': 1})
        json.dumps(report)

    def test_apply_fills_every_job(self):
        revision = self.query("SELECT value FROM meta WHERE key='revision'")[0]['value']
        statuses = {r['company']: (r['status'], r['fields']) for r in self.query('SELECT * FROM jobs')}
        report = self.m.migrate(self.xlsx, apply=True)
        self.assertTrue(report['ok'])
        self.assertTrue(Path(report['backup']).is_file())
        jobs = self.by_company()
        expected = {
            'Lever Co': (D(5), D(10), 'lever'),
            'About Co': (D(20), D(30), 'lever'),
            'Old Review Co': (D(70), D(70), 'page'),
            'Old Applied Co': (D(80), D(80), 'page'),
            'Not Shown Co': (TODAY.isoformat(), None, 'first_seen'),
            'Word First Co': (D(3), D(3), 'page'),
            'LinkedIn Co': (D(2), D(2), 'linkedin'),
            'GH Co': (D(4), D(4), 'greenhouse'),
            'Ashby Co': (D(6), D(6), 'ashby'),
            'Sheet Co': (D(62), None, 'first_seen'),
            'Sheet Posted Co': (D(62), D(50), 'page'),
        }
        for company, values in expected.items():
            with self.subTest(company=company):
                row = jobs[company]
                self.assertEqual((row['first_seen'], row['posted_on'], row['date_source']), values)
        old = self.by_company('old_jobs')
        self.assertEqual((old['Archived Co']['first_seen'], old['Archived Co']['date_source']), (D(90), 'first_seen'))
        self.assertEqual((old['Archived Two']['first_seen'], old['Archived Two']['posted_on']), (D(100), D(100)))
        self.assertEqual({r['company']: (r['status'], r['fields']) for r in self.query('SELECT * FROM jobs')}, statuses)
        self.assertEqual(len(self.query('SELECT * FROM old_jobs')), 2)
        self.assertEqual(self.query('SELECT * FROM moves'), [])
        self.assertEqual(int(self.query("SELECT value FROM meta WHERE key='revision'")[0]['value']), int(revision) + 1)

    def test_second_run_fills_nothing(self):
        self.m.migrate(self.xlsx, apply=True)
        revision = self.query("SELECT value FROM meta WHERE key='revision'")[0]['value']
        report = self.m.migrate(self.xlsx, apply=True)
        self.assertTrue(report['ok'])
        self.assertEqual(sum(report['filled'].values()), 0)
        self.assertEqual(self.query("SELECT value FROM meta WHERE key='revision'")[0]['value'], revision)

    def test_filled_first_seen_is_never_changed(self):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE jobs SET first_seen='2025-12-01' WHERE company='Lever Co'")
        con.commit()
        con.close()
        self.m.migrate(self.xlsx, apply=True)
        self.assertEqual(self.by_company()['Lever Co']['first_seen'], '2025-12-01')

    def test_excel_only_mode_refuses_plainly(self):
        folder = Path(self.tmp.name) / 'excel'
        folder.mkdir()
        xlsx, _ = make_tracker(folder)
        report = self.m.migrate(xlsx, apply=True)
        self.assertFalse(report['ok'])
        self.assertIn('database', report['message'])
        self.assertFalse(store.db_path(xlsx).exists())


class PageData(unittest.TestCase):
    """The page gets each job's dates and age in both modes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hq-fresh-load-')
        self.xlsx, self.old = make_tracker(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def jobs(self):
        return {j['Company']: j for j in tracker_for(self.xlsx).load()['jobs']}

    def test_sqlite_mode(self):
        self.assertTrue(store.migrate(self.xlsx, old=self.old, apply=True)['ok'])
        self.assertTrue(migration().migrate(self.xlsx, apply=True)['ok'])
        jobs = self.jobs()
        lever = jobs['Lever Co']
        self.assertEqual((lever['_first_seen'], lever['_posted_on'], lever['_date_source']), (D(5), D(10), 'lever'))
        self.assertEqual(lever['_age'], 10)
        self.assertFalse(lever['_about'])
        self.assertTrue(jobs['About Co']['_about'])
        self.assertTrue(jobs['LinkedIn Co']['_about'])
        shown = jobs['Not Shown Co']
        self.assertEqual((shown['_posted_on'], shown['_date_source'], shown['_age']), ('', 'first_seen', 0))
        self.assertTrue(shown['_about'])
        self.assertEqual(jobs['Sheet Posted Co']['_age'], 62)
        for job in jobs.values():
            self.assertTrue({'_first_seen', '_posted_on', '_date_source', '_age', '_about'} <= job.keys())

    def test_excel_mode_computes_without_storing(self):
        before = digest(self.xlsx)
        jobs = self.jobs()
        self.assertEqual(digest(self.xlsx), before)
        self.assertFalse(store.db_path(self.xlsx).exists())
        self.assertEqual((jobs['Lever Co']['_posted_on'], jobs['Lever Co']['_date_source'], jobs['Lever Co']['_age']),
                         (D(10), 'lever', 10))
        self.assertEqual(jobs['Lever Co']['_first_seen'], '')
        self.assertEqual((jobs['Sheet Co']['_first_seen'], jobs['Sheet Co']['_age']), (D(62), 62))
        self.assertEqual(jobs['Not Shown Co']['_age'], None)


if __name__ == '__main__':
    unittest.main()
