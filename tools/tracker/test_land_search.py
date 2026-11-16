"""Offline checks for the public landing writer and the one profile path (piece 2b).

Written before the build. Every workbook, backlog, profile and snapshot here is fictional and lives in a
temporary folder. Run: python -I tools/tracker/test_land_search.py
"""
from contextlib import closing
from datetime import date, timedelta
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

import openpyxl

HERE = Path(__file__).resolve().parent
DASH = HERE.parent / 'hq-dashboard'
TODAY = date.today()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = load_module('tracker_store_for_landing', HERE / 'store.py')
HEAD = ['#', 'Company', 'Role', 'Where', 'Posted', 'Score', 'Verdict', 'My status', 'Date applied', 'Link', 'Notes',
        'Agent notes']


def day(n):
    return (TODAY - timedelta(days=n)).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def job(posting, company='Example Co', title='Data Analyst', place='Remote', first_seen=None, posted=None,
        provider='greenhouse'):
    # As in real snapshots, 'company' is both the shown company and the board part of the posting identity.
    board = company.lower().replace(' ', '')
    return {'provider': provider, 'company': company, 'posting_id': posting, 'title': title, 'place_text': place,
            'remote': True, 'posted_on': posted if posted is not None else day(3), 'date_source': provider,
            'first_seen': first_seen or day(2), 'link': f'https://example.com/{board}/{posting}',
            'description': 'Build fictional reports.', 'lane': 'data', 'score': 4, 'matched_titles': ['Analyst'],
            'matched_duties': ['reports'], 'place': 'ok', 'place_sentence': 'Remote is allowed.', 'fit': 'pending',
            'judge': False}


def snapshot(jobs, stamp=None):
    return {'metadata': {'date': stamp or TODAY.isoformat(), 'profile_id': 'fictional-profile', 'profile_revision': 2,
                         'counts': {}},
            'landing': jobs, 'source_errors': []}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='land-search-test-')
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / 'work'
        self.work.mkdir()
        self.xlsx = self.work / 'applications-tracker.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Jobs'
        ws.append(HEAD)
        ws.append([1, 'Known Co', 'Known Analyst', 'Remote', None, None, None, 'Applied', None,
                   'https://example.com/known/1', 'Already here', None])
        wb.save(self.xlsx)
        wb.close()
        self.assertTrue(store.migrate(self.xlsx, apply=True)['ok'])
        self.db_file = store.db_path(self.xlsx)
        self.backlog = self.work / 'search-backlog.sqlite3'
        with closing(sqlite3.connect(self.backlog)) as db, db:
            db.execute('CREATE TABLE search_backlog (profile_id TEXT NOT NULL, identity TEXT NOT NULL, first_seen '
                       'TEXT NOT NULL, body TEXT NOT NULL, landed INTEGER NOT NULL DEFAULT 0, '
                       'PRIMARY KEY (profile_id, identity))')
        self.land = load_module('tracker_land_search_under_test', HERE / 'land_search.py')

    def backlog_add(self, *jobs):
        with closing(sqlite3.connect(self.backlog)) as db, db:
            for item in jobs:
                identity = json.dumps([item['provider'], item['company'], item['posting_id']])
                db.execute('INSERT OR IGNORE INTO search_backlog VALUES (?,?,?,?,0)',
                           ('fictional-profile', identity, item['first_seen'], json.dumps(item)))

    def landed_flags(self):
        with closing(sqlite3.connect(self.backlog)) as db:
            return dict(db.execute('SELECT identity, landed FROM search_backlog'))

    def write(self, data, name='today.json'):
        path = self.work / name
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def run_land(self, data, apply=True, name='today.json'):
        return self.land.land(self.xlsx, self.write(data, name), self.backlog, apply=apply)

    def rows(self, sql, args=()):
        with closing(sqlite3.connect(self.db_file)) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(sql, args)]


class Landing(Base):
    def test_dry_run_counts_and_changes_nothing(self):
        jobs = [job('1'), job('2', title='Report Analyst')]
        self.backlog_add(*jobs)
        tracker, backlog = digest(self.db_file), digest(self.backlog)
        report = self.run_land(snapshot(jobs), apply=False)
        self.assertTrue(report['ok'])
        self.assertEqual(report['added'], 2)
        self.assertEqual(digest(self.db_file), tracker)
        self.assertEqual(digest(self.backlog), backlog)

    def test_apply_lands_jobs_links_postings_and_marks_backlog(self):
        jobs = [job('1', first_seen=day(5)), job('2', title='Report Analyst')]
        self.backlog_add(*jobs)
        report = self.run_land(snapshot(jobs))
        self.assertEqual(report['added'], 2)
        landed = self.rows('SELECT * FROM jobs WHERE sheet=? ORDER BY row', (self.land.LANDING_SHEET,))
        self.assertEqual(len(landed), 2)
        first = landed[0]
        self.assertEqual(first['status'], 'Tracked')
        self.assertEqual(first['fit'], 'Fit pending')
        self.assertEqual(first['first_seen'], day(5), 'first seen comes from the backlog, not today')
        self.assertIn('posting greenhouse/1', first['agent_notes'])
        self.assertNotIn('greenhouse', first['notes'] or '', 'technical identifiers stay out of human Notes')
        self.assertIn('Fit: pending', first['notes'])
        links = self.rows('SELECT * FROM postings ORDER BY posting_id')
        self.assertEqual([(r['provider'], r['board'], r['posting_id'], r['job_id'], r['source']) for r in links],
                         [('greenhouse', 'Example Co', '1', first['id'], 'search'),
                          ('greenhouse', 'Example Co', '2', landed[1]['id'], 'search')])
        self.assertEqual(set(self.landed_flags().values()), {1})
        self.assertEqual(report['marked_landed'], 2)

    def test_no_new_sheet_per_day(self):
        one, two = job('1'), job('2', title='Report Analyst')
        self.backlog_add(one, two)
        self.run_land(snapshot([one], day(1)), name='a.json')
        sheets = [r['sheet'] for r in self.rows('SELECT sheet FROM layouts WHERE source="main"')]
        self.run_land(snapshot([two]), name='b.json')
        self.assertEqual([r['sheet'] for r in self.rows('SELECT sheet FROM layouts WHERE source="main"')], sheets)
        self.assertEqual(sheets.count(self.land.LANDING_SHEET), 1)
        self.assertFalse(any(name.startswith('New (') for name in sheets))
        self.assertEqual(len(self.rows('SELECT id FROM jobs WHERE sheet=?', (self.land.LANDING_SHEET,))), 2)

    def test_running_the_same_snapshot_twice_adds_nothing_and_does_not_fail(self):
        jobs = [job('1')]
        self.backlog_add(*jobs)
        self.run_land(snapshot(jobs))
        second = self.run_land(snapshot(jobs))
        self.assertTrue(second['ok'])
        self.assertEqual(second['added'], 0)
        self.assertEqual(second['known'], 1)
        self.assertEqual(len(self.rows('SELECT id FROM jobs WHERE sheet=?', (self.land.LANDING_SHEET,))), 1)

    def test_retry_after_backlog_marking_failed_finishes_the_marking(self):
        jobs = [job('1')]
        self.backlog_add(*jobs)
        self.run_land(snapshot(jobs))
        with closing(sqlite3.connect(self.backlog)) as db, db:
            db.execute('UPDATE search_backlog SET landed=0')
        retry = self.run_land(snapshot(jobs))
        self.assertEqual(retry['added'], 0)
        self.assertEqual(set(self.landed_flags().values()), {1})

    def test_existing_tracker_job_gets_the_posting_link_instead_of_a_copy(self):
        same = job('7', company='Known Co', title='Known Analyst', place='Remote')
        self.backlog_add(same)
        report = self.run_land(snapshot([same]))
        self.assertEqual(report['added'], 0)
        self.assertEqual(report['merged'], 1)
        known = self.rows("SELECT id FROM jobs WHERE company='Known Co'")
        self.assertEqual(len(known), 1)
        self.assertEqual(self.rows('SELECT job_id FROM postings')[0]['job_id'], known[0]['id'])

    def test_archived_job_does_not_come_back(self):
        old = job('8', company='Archived Co', title='Old Analyst')
        self.backlog_add(old)
        with closing(sqlite3.connect(self.db_file)) as db, db:
            db.execute("INSERT INTO old_jobs(sheet, row, company, role, place, original_job_id) "
                       "VALUES ('Old', 2, 'Archived Co', 'Old Analyst', 'Remote', 99)")
        report = self.run_land(snapshot([old]))
        self.assertEqual(report['added'], 0)
        self.assertEqual(report['skipped_archived'], 1)

    def test_posting_older_than_the_window_is_skipped(self):
        stale = job('9', posted=day(100), first_seen=day(100))
        self.backlog_add(stale)
        report = self.run_land(snapshot([stale]))
        self.assertEqual(report['added'], 0)
        self.assertEqual(report['skipped_old'], 1)

    def test_invalid_snapshot_writes_nothing(self):
        bad = job('1')
        bad['fit'] = 'Strong'
        self.backlog_add(bad)
        tracker = digest(self.db_file)
        with self.assertRaises(ValueError):
            self.run_land(snapshot([bad]))
        self.assertEqual(digest(self.db_file), tracker)

    def test_excel_only_tracker_is_refused_plainly(self):
        self.db_file.unlink()
        report = self.run_land(snapshot([job('1')]))
        self.assertFalse(report['ok'])
        self.assertIn('database', report['message'].lower())

    def test_no_personal_or_private_paths_in_the_module(self):
        text = (HERE / 'land_search.py').read_text(encoding='utf-8').lower()
        for word in ('c:\\users', 'apply-codex-scan'):
            self.assertNotIn(word, text)


class ProfilePath(unittest.TestCase):
    def test_default_profile_is_the_data_folder_profile(self):
        with tempfile.TemporaryDirectory(prefix='profile-path-') as folder:
            old = os.environ.get('JOB_SEARCH_HQ_DATA')
            os.environ['JOB_SEARCH_HQ_DATA'] = folder
            try:
                profiles = load_module('search_profile_for_landing', DASH / 'search_profile.py')
                self.assertEqual(profiles.default_path(), Path(folder).resolve() / 'profile.json')
            finally:
                if old is None:
                    os.environ.pop('JOB_SEARCH_HQ_DATA', None)
                else:
                    os.environ['JOB_SEARCH_HQ_DATA'] = old

    def test_daily_search_profile_flag_is_optional(self):
        text = (DASH / 'daily_search.py').read_text(encoding='utf-8')
        self.assertNotIn("add_argument('--profile', type=Path, required=True)", text)
        self.assertIn('default_path()', text)


if __name__ == '__main__':
    unittest.main()
