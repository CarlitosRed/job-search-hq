"""Offline checks for numbered database upgrades, the stable job number and posting links (piece 2a).

Written before the build. Every workbook and backlog here is fictional and lives in a temporary folder.
Run: python -I tools/tracker/test_migrations.py
"""
from contextlib import closing
from datetime import date, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import openpyxl

HERE = Path(__file__).resolve().parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = load_module('tracker_store_for_migrations', HERE / 'store.py')
HEAD = ['#', 'Company', 'Role', 'Where', 'Posted', 'Score', 'Verdict', 'My status', 'Date applied', 'Link', 'Notes',
        'Agent notes']
OLD = (date.today() - timedelta(days=90)).isoformat()
NOTE = ('Daily search 2026-01-15, profile fictional-profile rev 2; lane data; score 4; titles Analyst; duties reports; '
        'place ok: Remote.; posting {provider}/{posting}; judge false.')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path):
    with closing(sqlite3.connect(path)) as db:
        return list(db.iterdump())


def make_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Jobs'
    ws.append(HEAD)
    ws.append([1, 'Example Co', 'Example Analyst', 'Remote', None, None, 'Fit pending', 'Tracked', None,
               'https://example.com/jobs/1', None, NOTE.format(provider='greenhouse', posting='111')])
    ws.append([2, 'Sample Labs', 'Sample Engineer', 'Remote', None, None, 'Fit pending', 'Tracked', None,
               'https://example.com/jobs/2', None, NOTE.format(provider='lever', posting='abc-222')])
    ws.append([3, 'Demo Inc', 'Demo Builder', 'Hybrid', None, None, None, 'Applied', None,
               'https://example.com/jobs/3', None, 'Added by hand'])
    ws.append([4, 'Twin Co', 'Twin Analyst', 'Remote', None, None, 'Fit pending', 'Tracked', None,
               'https://example.com/jobs/4', None, NOTE.format(provider='ashby', posting='twin-1')])
    ws.append([5, 'Gone Co', 'Gone Analyst', 'Remote', None, None, 'Fit pending', 'Tracked', None,
               'https://example.com/jobs/5', None, NOTE.format(provider='greenhouse', posting='999')])
    wb.save(path)
    wb.close()


def make_backlog(path):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE search_backlog (profile_id TEXT NOT NULL, identity TEXT NOT NULL, first_seen TEXT '
                   'NOT NULL, body TEXT NOT NULL, landed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (profile_id, identity))')
        rows = [('fictional-profile', ['greenhouse', 'exampleco', '111']),
                ('fictional-profile', ['lever', 'samplelabs', 'abc-222']),
                ('fictional-profile', ['ashby', 'twinone', 'twin-1']),
                ('fictional-profile', ['ashby', 'twintwo', 'twin-1']),
                ('other-profile', ['greenhouse', 'otherco', '999'])]
        for profile, identity in rows:
            db.execute('INSERT INTO search_backlog VALUES (?,?,?,?,1)',
                       (profile, json.dumps(identity), '2026-01-15', json.dumps({'link': 'https://example.com'})))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tracker-migrations-test-')
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / 'work'
        self.work.mkdir()
        self.xlsx = self.work / 'applications-tracker.xlsx'
        make_workbook(self.xlsx)
        self.assertTrue(store.migrate(self.xlsx, apply=True)['ok'])
        self.db_file = store.db_path(self.xlsx)
        self.migrations = load_module('tracker_migrations_under_test', HERE / 'migrations.py')

    def sql(self, *statements):
        with closing(sqlite3.connect(self.db_file)) as db, db:
            for statement in statements:
                db.execute(statement)

    def query(self, statement, args=()):
        with closing(sqlite3.connect(self.db_file)) as db:
            return db.execute(statement, args).fetchall()

    def columns(self, table):
        return [r[1] for r in self.query(f'PRAGMA table_info({table})')]

    def version(self):
        rows = self.query("SELECT value FROM meta WHERE key='schema_version'")
        return int(rows[0][0]) if rows else 0

    def make_legacy(self):
        """Turn the fresh database back into the shape real databases had before piece 2a."""
        self.sql('DROP TABLE IF EXISTS postings',
                 "DELETE FROM meta WHERE key='schema_version'")
        if 'job_ref' in self.columns('documents'):
            self.sql('ALTER TABLE documents DROP COLUMN job_ref')

    def backups(self):
        folder = self.work / '.tracker-backups'
        return sorted(folder.glob('*before-migration*')) if folder.is_dir() else []

    def job_id(self, company):
        return self.query('SELECT id FROM jobs WHERE company=?', (company,))[0][0]


class Upgrades(Base):
    def test_fresh_database_is_at_the_latest_version(self):
        self.assertGreaterEqual(self.migrations.LATEST, 3)
        self.assertEqual(self.version(), self.migrations.LATEST)
        self.assertIn('job_ref', self.columns('documents'))
        self.assertIn('original_job_id', self.columns('old_jobs'))
        self.assertTrue(self.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name='postings'"))

    def test_steps_are_numbered_in_order_without_gaps(self):
        numbers = [step[0] for step in self.migrations.STEPS]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))
        self.assertEqual(self.migrations.LATEST, numbers[-1])

    def test_legacy_database_upgrades_once_with_a_backup_first(self):
        self.make_legacy()
        before = store.Store(self.db_file, upgrade=False)
        counts = before.counts()
        before.close()
        legacy = dump(self.db_file)
        store.Store(self.db_file).close()
        self.assertEqual(self.version(), self.migrations.LATEST)
        backups = self.backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(dump(backups[0]), legacy, 'the backup is the database as it was before the upgrade')
        again = store.Store(self.db_file)
        self.assertEqual(again.counts(), counts, 'an upgrade never changes the jobs')
        again.close()
        self.assertEqual(len(self.backups()), 1, 'nothing pending means no second backup')

    def test_nothing_pending_means_no_write(self):
        start = digest(self.db_file)
        store.Store(self.db_file).close()
        self.assertEqual(digest(self.db_file), start)
        self.assertEqual(self.backups(), [])

    def test_upgrade_false_never_touches_a_legacy_database(self):
        self.make_legacy()
        start = digest(self.db_file)
        storage = store.Store(self.db_file, upgrade=False)
        storage.counts()
        storage.close()
        self.assertEqual(digest(self.db_file), start)
        self.assertEqual(self.backups(), [])

    def test_a_failing_step_rolls_back_and_keeps_the_version(self):
        self.make_legacy()
        original = list(self.migrations.STEPS)

        def broken(db):
            db.execute('CREATE TABLE half_done (x)')
            raise RuntimeError('fictional failure')

        self.migrations.STEPS.append((len(original) + 1, 'broken step for the test', broken))
        self.addCleanup(lambda: self.migrations.STEPS.__setitem__(slice(None), original))
        with closing(sqlite3.connect(self.db_file)) as db:
            with self.assertRaises(RuntimeError):
                self.migrations.upgrade(db, self.db_file)
        self.assertEqual(self.version(), 0)
        self.assertFalse(self.query("SELECT 1 FROM sqlite_master WHERE name IN ('half_done', 'postings')"))
        self.assertNotIn('job_ref', self.columns('documents'))

    def test_store_and_archive_no_longer_alter_tables_themselves(self):
        for name in ('store.py', 'archive_old.py'):
            text = (HERE / name).read_text(encoding='utf-8')
            self.assertNotIn('ALTER TABLE', text, f'{name}: schema changes belong in migrations.py')


class StableJobNumber(Base):
    def test_documents_get_the_stable_job_number(self):
        live = self.job_id('Sample Labs')
        self.sql("UPDATE old_jobs SET original_job_id=NULL WHERE 0",
                 "INSERT INTO documents(job_id, kind, file_name, sha256, made_by, created) "
                 "VALUES ('Jobs|3', 'cv', 'a.docx', 'x', 'site', '2026-01-01')",
                 "INSERT INTO documents(job_id, kind, file_name, sha256, made_by, created) "
                 "VALUES ('Nope|9', 'cv', 'b.docx', 'x', 'site', '2026-01-01')")
        self.make_legacy()
        store.Store(self.db_file).close()
        refs = dict(self.query('SELECT file_name, job_ref FROM documents'))
        self.assertEqual(refs['a.docx'], live)
        self.assertIsNone(refs['b.docx'], 'an unknown job stays empty, never guessed')

    def test_job_ref_resolves_live_and_archived_rows(self):
        storage = store.Store(self.db_file)
        try:
            live = self.job_id('Demo Inc')
            self.assertEqual(storage.job_ref('Jobs|4'), live)
            self.assertIsNone(storage.job_ref('Jobs|99'))
            with storage.db:
                storage.db.execute("INSERT INTO old_jobs(sheet, row, company, role, original_job_id) "
                                   "VALUES ('Old', 7, 'Archived Example', 'Old Role', 41)")
            self.assertEqual(storage.job_ref('Old|7'), 41)
        finally:
            storage.close()

    def test_archiving_keeps_the_stable_job_number(self):
        archive = load_module('tracker_archive_for_migrations', HERE / 'archive_old.py')
        job = self.job_id('Demo Inc')
        self.sql(f"UPDATE jobs SET status='Skipped', posted_on='{OLD}', first_seen='{OLD}' WHERE id={job}",
                 "INSERT INTO documents(job_id, job_ref, kind, file_name, sha256, made_by, created) "
                 f"VALUES ('Jobs|4', {job}, 'cv', 'c.docx', 'x', 'site', '2026-01-01')")
        with closing(sqlite3.connect(self.db_file)) as db, db:
            db.row_factory = sqlite3.Row
            archive.move(db)
        self.assertEqual(self.query('SELECT job_ref FROM documents')[0][0], job)
        self.assertEqual(self.query('SELECT original_job_id FROM old_jobs WHERE company=?', ('Demo Inc',))[0][0], job)


class Postings(Base):
    def test_link_is_idempotent_and_updates_last_seen(self):
        storage = store.Store(self.db_file)
        try:
            job = self.job_id('Example Co')
            with storage.db:
                storage.link_posting(job, 'fictional-profile', 'greenhouse', 'exampleco', '111',
                                     link='https://example.com/jobs/1', seen='2026-01-15', source='search')
                storage.link_posting(job, 'fictional-profile', 'greenhouse', 'exampleco', '111',
                                     link='https://example.com/jobs/1', seen='2026-01-20', source='search')
            rows = storage.postings_for(job)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['first_seen'], '2026-01-15')
            self.assertEqual(rows[0]['last_seen'], '2026-01-20')
            self.assertEqual(storage.job_for_posting('fictional-profile', 'greenhouse', 'exampleco', '111'), job)
            self.assertIsNone(storage.job_for_posting('fictional-profile', 'greenhouse', 'exampleco', '000'))
        finally:
            storage.close()

    def test_linking_a_posting_to_another_job_is_refused(self):
        storage = store.Store(self.db_file)
        try:
            first, second = self.job_id('Example Co'), self.job_id('Sample Labs')
            with storage.db:
                storage.link_posting(first, 'fictional-profile', 'greenhouse', 'exampleco', '111', seen='2026-01-15')
            with self.assertRaises(ValueError):
                with storage.db:
                    storage.link_posting(second, 'fictional-profile', 'greenhouse', 'exampleco', '111',
                                         seen='2026-01-16')
            self.assertEqual(storage.job_for_posting('fictional-profile', 'greenhouse', 'exampleco', '111'), first)
        finally:
            storage.close()

    def test_one_job_can_hold_several_postings_and_profiles_stay_apart(self):
        storage = store.Store(self.db_file)
        try:
            job = self.job_id('Example Co')
            with storage.db:
                storage.link_posting(job, 'fictional-profile', 'greenhouse', 'exampleco', '111', seen='2026-01-15')
                storage.link_posting(job, 'fictional-profile', 'greenhouse', 'exampleco', '112', seen='2026-01-15')
            self.assertEqual(len(storage.postings_for(job)), 2)
            self.assertIsNone(storage.job_for_posting('other-profile', 'greenhouse', 'exampleco', '111'))
        finally:
            storage.close()


class Backfill(Base):
    def setUp(self):
        super().setUp()
        self.backlog = self.work / 'search-backlog.sqlite3'
        make_backlog(self.backlog)
        self.backfill = load_module('tracker_backfill_under_test', HERE / 'backfill_postings.py')

    def test_dry_run_reports_and_changes_nothing(self):
        start = digest(self.db_file)
        report = self.backfill.backfill(self.xlsx, self.backlog, apply=False)
        self.assertEqual(digest(self.db_file), start)
        self.assertEqual(report['found'], 4)
        self.assertEqual(report['linkable'], 2)
        self.assertEqual(report['linked'], 0)
        reasons = {item['posting']: item['reason'] for item in report['unknown']}
        self.assertEqual(set(reasons), {'ashby/twin-1', 'greenhouse/999'})

    def test_apply_links_only_single_matches(self):
        report = self.backfill.backfill(self.xlsx, self.backlog, apply=True)
        self.assertEqual(report['linked'], 2)
        storage = store.Store(self.db_file)
        try:
            self.assertEqual(storage.job_for_posting('fictional-profile', 'greenhouse', 'exampleco', '111'),
                             self.job_id('Example Co'))
            self.assertEqual(storage.job_for_posting('fictional-profile', 'lever', 'samplelabs', 'abc-222'),
                             self.job_id('Sample Labs'))
            self.assertEqual(storage.postings_for(self.job_id('Twin Co')), [], 'two boards match: left Unknown')
            self.assertEqual(storage.postings_for(self.job_id('Gone Co')), [], 'other profile only: left Unknown')
        finally:
            storage.close()

    def test_apply_twice_adds_nothing_new(self):
        self.backfill.backfill(self.xlsx, self.backlog, apply=True)
        second = self.backfill.backfill(self.xlsx, self.backlog, apply=True)
        self.assertEqual(second['linked'], 0)
        self.assertEqual(self.query('SELECT COUNT(*) FROM postings')[0][0], 2)


if __name__ == '__main__':
    unittest.main()
