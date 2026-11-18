"""Closing and re-check (piece 3, card 03): written before the build.

Every company, job, link and board here is fictional and lives in a temporary folder. Network calls are
fakes. Run: python -I tools/tracker/test_recheck.py
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
DAY1 = date(2026, 3, 10)
DAY2 = DAY1 + timedelta(days=1)
DAY3 = DAY1 + timedelta(days=2)
PROFILE = 'fictional-profile'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = load_module('tracker_store_for_recheck', HERE / 'store.py')
migrations = load_module('tracker_migrations_for_recheck', HERE / 'migrations.py')
recheck = load_module('tracker_recheck_test', HERE / 'recheck.py')

HEAD = ['#', 'Company', 'Role', 'Where', 'Posted', 'Score', 'Verdict', 'My status', 'Date applied', 'Link', 'Notes',
        'Agent notes']


def read(posting_id, title='Data Analyst', company='Example Co'):
    return [posting_id, f'https://example.com/{company.lower().replace(" ", "")}/{posting_id}', title]


def board(seen, read_state='complete', company='Example Co', provider='greenhouse', reason=''):
    return {'provider': provider, 'board': company, 'read': read_state, 'reason': reason,
            'rows': len(seen), 'seen': seen}


def snapshot(day, boards):
    return {'metadata': {'date': day.isoformat(), 'profile_id': PROFILE, 'profile_revision': 1, 'counts': {}},
            'landing': [], 'source_errors': [], 'boards': boards}


class Links:
    """Fake link checker: answers per link; records calls."""

    def __init__(self, answers=None, default=('open', '')):
        self.answers, self.default, self.calls = dict(answers or {}), default, []

    def __call__(self, link):
        self.calls.append(link)
        answer = self.answers.get(link, self.default)
        return answer.pop(0) if isinstance(answer, list) else answer


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='recheck-test-')
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / 'work'
        self.work.mkdir()
        self.xlsx = self.work / 'applications-tracker.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Jobs'
        ws.append(HEAD)
        # Rows 2-4 get postings below; row 5 is a pasted link; row 6 is Skipped; row 7 is LinkedIn.
        ws.append([1, 'Example Co', 'Data Analyst', 'Remote', None, None, None, 'Applied', None,
                   'https://example.com/exampleco/101', 'Keep me', None])
        ws.append([2, 'Example Co', 'Report Analyst', 'Remote', None, None, None, 'Tracked', None,
                   'https://example.com/exampleco/102', None, None])
        ws.append([3, 'Big Example', 'Partner Lead', 'Remote', None, None, None, 'Apply', None,
                   'https://bigex.wd1.myworkdayjobs.com/Careers/job/Remote/Partner-Lead_R100', None, None])
        ws.append([4, 'Pasted Co', 'Ops Analyst', 'Remote', None, None, None, 'Review', None,
                   'https://careers.pasted.example/jobs/77', None, None])
        ws.append([5, 'Skip Co', 'Ops Analyst', 'Remote', None, None, None, 'Skipped', None,
                   'https://careers.skip.example/jobs/5', None, None])
        ws.append([6, 'Linked Co', 'Ops Analyst', 'Remote', None, None, None, 'Tracked', None,
                   'https://www.linkedin.com/jobs/view/123', None, None])
        wb.save(self.xlsx)
        wb.close()
        self.assertTrue(store.migrate(self.xlsx, apply=True)['ok'])
        self.db_file = store.db_path(self.xlsx)
        self.storage = store.Store(self.db_file)
        self.addCleanup(self.storage.close)
        ids = {r['company'] if r['role'] == 'Ops Analyst' else r['role']: r['id']
               for r in self.storage.db.execute('SELECT id, company, role FROM jobs')}
        self.ids = ids
        with self.storage.db:
            self.storage.link_posting(ids['Data Analyst'], PROFILE, 'greenhouse', 'Example Co', '101',
                                      link='https://example.com/exampleco/101', seen='2026-03-01', source='search')
            self.storage.link_posting(ids['Report Analyst'], PROFILE, 'greenhouse', 'Example Co', '102',
                                      link='https://example.com/exampleco/102', seen='2026-03-01', source='search')
            self.storage.link_posting(ids['Partner Lead'], PROFILE, 'workday', 'Big Example', 'R100',
                                      link='https://bigex.wd1.myworkdayjobs.com/Careers/job/Remote/Partner-Lead_R100',
                                      seen='2026-03-01', source='search')
        self.links = Links()

    def run_check(self, day, boards, run_id=None, dry_run=False, links=None, backlog=None):
        return recheck.recheck(self.storage, snapshot(day, boards), day, run_id or ('run-' + day.isoformat()),
                               links or self.links, backlog=backlog, dry_run=dry_run)

    def full(self, *ids, company='Example Co'):
        return board([read(i, company=company) for i in ids], company=company)

    def state(self, role):
        return recheck.posting_status(self.storage, self.ids[role])['state']

    def snapshot_of_db(self):
        with closing(sqlite3.connect(self.db_file)) as db:
            return {t: db.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall()
                    for t in ('jobs', 'postings', 'checks', 'board_reads', 'moves')}


class Migration(Base):
    def test_step_four_adds_columns_and_board_reads(self):
        db = self.storage.db
        self.assertGreaterEqual(migrations.LATEST, 4)
        self.assertTrue({'run_id', 'posting_row', 'kind', 'observation', 'detail', 'at'} <= migrations.columns(db, 'checks'))
        self.assertTrue({'state', 'closed_on', 'close_reason'} <= migrations.columns(db, 'postings'))
        self.assertTrue({'run_id', 'date', 'provider', 'board', 'read', 'rows', 'reason'} <= migrations.columns(db, 'board_reads'))
        self.assertEqual({r[0] for r in db.execute('SELECT state FROM postings')}, {'open'})

    def test_old_tracker_is_backed_up_then_upgraded_once(self):
        old = self.work / 'old.sqlite3'
        with closing(sqlite3.connect(old)) as db, db:
            db.executescript('''CREATE TABLE meta (key TEXT PRIMARY KEY, value);
                INSERT INTO meta VALUES ('schema_version', 3);
                CREATE TABLE jobs (id INTEGER PRIMARY KEY, posted_on TEXT);
                CREATE TABLE old_jobs (id INTEGER PRIMARY KEY, posted_on TEXT, original_job_id INTEGER);
                CREATE TABLE documents (id INTEGER PRIMARY KEY, job_id TEXT, job_ref INTEGER);
                CREATE TABLE checks (id INTEGER PRIMARY KEY, job_id INTEGER, date TEXT, result TEXT);
                INSERT INTO checks (job_id, date, result) VALUES (7, '2026-01-02', 'Open');
                CREATE TABLE postings (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, profile_id TEXT NOT NULL,
                    provider TEXT NOT NULL, board TEXT NOT NULL, posting_id TEXT NOT NULL, link TEXT,
                    first_seen TEXT, last_seen TEXT, source TEXT, UNIQUE(profile_id, provider, board, posting_id));''')
        store.Store(old).close()
        store.Store(old).close()
        backups = list((self.work / '.tracker-backups').glob('old-before-migration-3-to-*.sqlite3'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(old)) as db:
            self.assertEqual(db.execute('SELECT job_id, date, result FROM checks').fetchall(), [(7, '2026-01-02', 'Open')])


class Misses(Base):
    def test_two_complete_misses_on_different_days_close(self):
        self.run_check(DAY1, [self.full('101')])
        self.assertEqual(self.state('Report Analyst'), 'open')
        report = self.run_check(DAY2, [self.full('101')])
        self.assertEqual(self.state('Report Analyst'), 'closed')
        self.assertEqual([c['job_id'] for c in report['closed']], [self.ids['Report Analyst']])
        self.assertIn('two complete reads', recheck.posting_status(self.storage, self.ids['Report Analyst'])['reason'])
        self.assertEqual(self.state('Data Analyst'), 'open')

    def test_same_day_rerun_does_not_add_a_second_miss(self):
        self.run_check(DAY1, [self.full('101')], run_id='a')
        self.run_check(DAY1, [self.full('101')], run_id='b')
        self.assertEqual(self.state('Report Analyst'), 'open')

    def test_partial_and_failed_reads_neither_count_nor_reset(self):
        self.run_check(DAY1, [self.full('101')])
        self.run_check(DAY2, [board([read('101')], read_state='partial', reason='Lever returned 500 rows')])
        self.assertEqual(self.state('Report Analyst'), 'open')
        self.run_check(DAY3, [self.full('101')])
        self.assertEqual(self.state('Report Analyst'), 'closed')

    def test_failed_read_records_no_information(self):
        report = self.run_check(DAY1, [board([], read_state='failed', reason='board down')])
        self.assertEqual(self.state('Report Analyst'), 'open')
        self.assertIn('Example Co', [b['board'] for b in report['no_information']])

    def test_a_sighting_resets_the_count(self):
        self.run_check(DAY1, [self.full('101')])
        self.run_check(DAY2, [self.full('101', '102')])
        self.run_check(DAY3, [self.full('101')])
        self.assertEqual(self.state('Report Analyst'), 'open')

    def test_zero_rows_is_no_information(self):
        self.run_check(DAY1, [board([])])
        self.run_check(DAY2, [board([])])
        self.assertEqual(self.state('Data Analyst'), 'open')

    def test_drop_over_half_from_last_complete_read_is_no_information(self):
        many = [read(str(n)) for n in range(200, 210)] + [read('101'), read('102')]
        self.run_check(DAY1, [board(many)])
        self.run_check(DAY2, [board([read('101'), read('200')])])
        self.run_check(DAY3, [board([read('101'), read('201')])])
        self.assertEqual(self.state('Report Analyst'), 'open')

    def test_stage_rows_and_notes_never_change(self):
        before = self.snapshot_of_db()['jobs']
        self.run_check(DAY1, [board([read('999', title='Warehouse Clerk')])])
        self.run_check(DAY2, [board([read('999', title='Warehouse Clerk')])])
        self.assertEqual(self.state('Data Analyst'), 'closed')
        after = self.snapshot_of_db()
        self.assertEqual(after['jobs'], before)
        self.assertEqual(after['moves'], [])
        self.assertEqual(len(after['postings']), 3)


class Explicit(Base):
    def test_404_or_410_closes_at_once(self):
        links = Links({'https://example.com/exampleco/102': ('gone', '404')})
        report = self.run_check(DAY1, [self.full('101')], links=links)
        self.assertEqual(self.state('Report Analyst'), 'closed')
        self.assertIn('404', report['closed'][0]['reason'])

    def test_missed_posting_link_is_checked_once(self):
        self.run_check(DAY1, [self.full('101')])
        self.assertEqual(self.links.calls.count('https://example.com/exampleco/102'), 1)
        self.assertNotIn('https://example.com/exampleco/101', self.links.calls)


class LinkChecks(Base):
    def test_job_without_posting_row_closes_on_404(self):
        links = Links({'https://careers.pasted.example/jobs/77': ('gone', '410')})
        self.run_check(DAY1, [self.full('101', '102')], links=links)
        self.assertEqual(self.state('Pasted Co'), 'closed')

    def test_closing_text_needs_two_days(self):
        text = ('closed_text', 'This position is no longer accepting applications.')
        links = Links({'https://careers.pasted.example/jobs/77': text})
        self.run_check(DAY1, [self.full('101', '102')], links=links)
        self.assertEqual(self.state('Pasted Co'), 'suspected')
        self.run_check(DAY1, [self.full('101', '102')], links=links, run_id='again')
        self.assertEqual(self.state('Pasted Co'), 'suspected')
        self.run_check(DAY2, [self.full('101', '102')], links=links)
        self.assertEqual(self.state('Pasted Co'), 'closed')

    def test_open_page_resets_closing_text(self):
        url = 'https://careers.pasted.example/jobs/77'
        links = Links({url: [('closed_text', 'No longer accepting applications.'), ('open', ''),
                             ('closed_text', 'No longer accepting applications.')]})
        for day in (DAY1, DAY2, DAY3):
            self.run_check(day, [self.full('101', '102')], links=links)
        # Not closed: the open page on day 2 reset the count; day 3 is a fresh first notice.
        self.assertEqual(self.state('Pasted Co'), 'suspected')

    def test_unclear_and_linkedin_are_not_checked(self):
        links = Links({'https://careers.pasted.example/jobs/77': ('unclear', 'The page asks you to sign in.')})
        report = self.run_check(DAY1, [self.full('101', '102')], links=links)
        self.assertEqual(self.state('Pasted Co'), 'not_checked')
        status = recheck.posting_status(self.storage, self.ids['Pasted Co'])
        self.assertIn('sign in', status['reason'])
        self.assertNotIn('https://www.linkedin.com/jobs/view/123', links.calls)
        linked = self.ids['Linked Co']
        self.assertEqual(recheck.posting_status(self.storage, linked)['state'], 'not_checked')
        self.assertIn(linked, [n['job_id'] for n in report['not_checked']])

    def test_skipped_jobs_are_not_link_checked(self):
        self.run_check(DAY1, [self.full('101', '102')])
        self.assertNotIn('https://careers.skip.example/jobs/5', self.links.calls)

    def test_link_checked_once_a_day(self):
        self.run_check(DAY1, [self.full('101', '102')], run_id='a')
        self.run_check(DAY1, [self.full('101', '102')], run_id='b')
        self.assertEqual(self.links.calls.count('https://careers.pasted.example/jobs/77'), 1)


class Identity(Base):
    def test_workday_link_match_when_id_changes(self):
        lead = ['JR-9', 'https://bigex.wd1.myworkdayjobs.com/Careers/job/Remote/Partner-Lead_R100', 'Partner Lead']
        for day in (DAY1, DAY2):
            self.run_check(day, [self.full('101', '102'),
                                 board([lead], company='Big Example', provider='workday')])
        self.assertEqual(self.state('Partner Lead'), 'open')

    def test_new_id_already_linked_to_another_job_does_not_stop_the_check(self):
        # Found on a copy of real data: a link match whose new ID belongs to another tracker job.
        with self.storage.db:
            self.storage.link_posting(self.ids['Data Analyst'], PROFILE, 'greenhouse', 'Example Co', '777',
                                      link='https://example.com/exampleco/777', seen='2026-03-01', source='search')
        moved = ['777', 'https://example.com/exampleco/102', 'Report Analyst']
        report = self.run_check(DAY1, [board([read('101'), moved])])
        self.assertEqual(self.state('Report Analyst'), 'open')
        self.assertEqual(self.storage.job_for_posting(PROFILE, 'greenhouse', 'Example Co', '777'), self.ids['Data Analyst'])
        self.assertEqual(report['closed'], [])

    def test_one_bad_posting_does_not_stop_the_others(self):
        calls = []

        def links(link):
            calls.append(link)
            if link.endswith('/102'):
                raise RuntimeError('unexpected')
            return ('gone', '404')
        report = self.run_check(DAY1, [self.full('999')], links=links)
        self.assertIn('https://careers.pasted.example/jobs/77', calls)
        self.assertEqual(self.state('Pasted Co'), 'closed')
        self.assertTrue(report['errors'])

    def test_repost_links_new_id_and_keeps_job_open(self):
        for day in (DAY1, DAY2):
            self.run_check(day, [board([read('101'), read('555', title='Report Analyst')])])
        self.assertEqual(self.state('Report Analyst'), 'open')
        ids = {p['posting_id'] for p in self.storage.postings_for(self.ids['Report Analyst'])}
        self.assertEqual(ids, {'102', '555'})

    def test_relisting_reopens(self):
        self.run_check(DAY1, [self.full('101')])
        self.run_check(DAY2, [self.full('101')])
        self.assertEqual(self.state('Report Analyst'), 'closed')
        report = self.run_check(DAY3, [self.full('101', '102')])
        self.assertEqual(self.state('Report Analyst'), 'open')
        self.assertIn(self.ids['Report Analyst'], [r['job_id'] for r in report['reopened']])

    def test_sighting_updates_last_seen_but_keeps_first_seen(self):
        self.run_check(DAY2, [self.full('101', '102')])
        posting = self.storage.postings_for(self.ids['Data Analyst'])[0]
        self.assertEqual((posting['first_seen'], posting['last_seen']), ('2026-03-01', DAY2.isoformat()))


class DryRun(Base):
    def test_dry_run_writes_nothing_and_names_results(self):
        links = Links({'https://example.com/exampleco/102': ('gone', '404')})
        before = self.snapshot_of_db()
        digest = hashlib.sha256(self.db_file.read_bytes()).hexdigest()
        report = self.run_check(DAY1, [self.full('101')], dry_run=True, links=links)
        self.assertEqual(self.snapshot_of_db(), before)
        self.assertEqual(hashlib.sha256(self.db_file.read_bytes()).hexdigest(), digest)
        closed = report['closed'][0]
        self.assertEqual((closed['company'], closed['role']), ('Example Co', 'Report Analyst'))
        self.assertTrue(report['dry_run'])

    def test_dry_run_does_not_count_as_a_miss(self):
        self.run_check(DAY1, [self.full('101')], dry_run=True)
        self.run_check(DAY2, [self.full('101')])
        self.assertEqual(self.state('Report Analyst'), 'open')


class Backlog(Base):
    def make_backlog(self):
        path = self.work / 'search-backlog.sqlite3'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE search_backlog (profile_id TEXT NOT NULL, identity TEXT NOT NULL, first_seen TEXT NOT NULL, body TEXT NOT NULL, landed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (profile_id, identity))')
            for posting in ('301', '302'):
                key = json.dumps(['greenhouse', 'Example Co', posting])
                db.execute('INSERT INTO search_backlog VALUES (?,?,?,?,0)',
                           (PROFILE, key, '2026-03-01', json.dumps({'provider': 'greenhouse', 'company': 'Example Co',
                                                                     'posting_id': posting, 'title': 'X'})))
        return path

    def bodies(self, path):
        with closing(sqlite3.connect(path)) as db:
            return {json.loads(i)[2]: json.loads(b) for i, b in db.execute('SELECT identity, body FROM search_backlog')}

    def test_backlog_rows_missing_twice_are_hidden_not_deleted(self):
        path = self.make_backlog()
        self.run_check(DAY1, [self.full('101', '102', '301')], backlog=path)
        self.run_check(DAY2, [self.full('101', '102', '301')], backlog=path)
        bodies = self.bodies(path)
        self.assertEqual(set(bodies), {'301', '302'})
        self.assertTrue(bodies['302'].get('gone'))
        self.assertFalse(bodies['301'].get('gone'))


class Stale(Base):
    def test_board_without_complete_read_for_three_days_is_listed(self):
        self.run_check(DAY1, [self.full('101', '102'), board([], read_state='failed', company='Big Example',
                                                             provider='workday', reason='board down')])
        for day in (DAY2, DAY3, DAY3 + timedelta(days=1)):
            self.run_check(day, [self.full('101', '102'), board([], read_state='failed', company='Big Example',
                                                                provider='workday', reason='board down')])
        stale = recheck.stale_boards(self.storage, DAY3 + timedelta(days=1))
        self.assertEqual([s['board'] for s in stale], ['Big Example'])
        self.assertIn('board down', stale[0]['reason'])
        self.assertEqual(recheck.stale_boards(self.storage, DAY2), [])


class ReviewFixes(Base):
    """Round 1 review findings (Astra and Fable, 2026-11-16): written before the fix."""

    def test_open_posting_wording_is_not_closing_text(self):
        for sentence in ('Applications will be accepted until the position is filled.',
                         'We review applications until the role has been filled.',
                         'This posting remains open until the job is closed out.',
                         'If this job is no longer available, browse our other vacancies.',
                         'Once the position has been filled we will remove this posting.'):
            self.assertEqual(recheck.classify(200, 'Apply now. ' + sentence)[0], 'open', sentence)
        for sentence in ('This position has been filled.', 'This job is no longer available.',
                         'We are no longer accepting applications for this role.'):
            self.assertEqual(recheck.classify(200, sentence)[0], 'closed_text', sentence)

    def test_blocked_page_is_unclear_even_with_closing_words(self):
        self.assertEqual(recheck.classify(200, 'Please sign in. This job is no longer available.')[0], 'unclear')

    def test_closing_reason_is_capped(self):
        reason = recheck.classify(200, 'This job is no longer available ' + 'x' * 900 + '.')[1]
        self.assertLessEqual(len(reason), 200)

    def test_one_snapshot_never_counts_as_two_days(self):
        snap = snapshot(DAY1, [self.full('101')])
        recheck.recheck(self.storage, snap, DAY1, 'a', self.links)
        recheck.recheck(self.storage, snap, DAY2, 'b', self.links)
        self.assertEqual(self.state('Report Analyst'), 'open')

    def test_misses_less_than_eighteen_hours_apart_count_once(self):
        first, second = snapshot(DAY1, [self.full('101')]), snapshot(DAY2, [self.full('101')])
        first['metadata']['read_at'] = DAY1.isoformat() + 'T23:50:00'
        second['metadata']['read_at'] = DAY2.isoformat() + 'T00:10:00'
        recheck.recheck(self.storage, first, DAY1, 'a', self.links)
        recheck.recheck(self.storage, second, DAY2, 'b', self.links)
        self.assertEqual(self.state('Report Analyst'), 'open')

    def test_query_string_links_are_distinct_postings(self):
        with self.storage.db:
            self.storage.db.execute("UPDATE postings SET link='https://acme.example/careers?gh_jid='||posting_id")
        other = ['999', 'https://acme.example/careers?gh_jid=999', 'Totally Different Role']
        for day in (DAY1, DAY2):
            self.run_check(day, [board([read('101'), other])])
        self.assertEqual(self.state('Report Analyst'), 'closed')
        self.assertIsNone(self.storage.job_for_posting(PROFILE, 'greenhouse', 'Example Co', '999'))

    def test_same_title_in_another_place_is_not_a_repost(self):
        london = ['555', 'https://example.com/exampleco/555', 'Report Analyst', 'London']
        for day in (DAY1, DAY2):
            self.run_check(day, [board([read('101'), london])])
        self.assertEqual(self.state('Report Analyst'), 'closed')
        self.assertIsNone(self.storage.job_for_posting(PROFILE, 'greenhouse', 'Example Co', '555'))

    def test_ambiguous_reposts_are_not_guessed(self):
        with self.storage.db:
            self.storage.db.execute("UPDATE jobs SET role='Data Analyst' WHERE id=?", (self.ids['Report Analyst'],))
        new = [['601', 'https://example.com/exampleco/601', 'Data Analyst', 'Remote'],
               ['602', 'https://example.com/exampleco/602', 'Data Analyst', 'Remote']]
        report = self.run_check(DAY1, [board(new)])
        for posting in ('601', '602'):
            self.assertIsNone(self.storage.job_for_posting(PROFILE, 'greenhouse', 'Example Co', posting))
        self.assertTrue(any('repost' in n['reason'].lower() for n in report['not_checked']))

    def test_two_boards_with_one_label_are_read_together(self):
        failed = board([], read_state='failed', reason='board down')
        for day in (DAY1, DAY2):
            self.run_check(day, [self.full('101'), failed])
        self.assertEqual(self.state('Report Analyst'), 'open')

    def test_links_are_checked_before_the_tracker_is_locked(self):
        other = sqlite3.connect(self.db_file, timeout=0.2)
        self.addCleanup(other.close)

        def links(link):
            with other:
                other.execute("UPDATE meta SET value=value WHERE key='revision'")
            return ('open', '')
        self.run_check(DAY1, [self.full('101', '102')], links=links)

    def test_link_checks_stop_at_the_time_budget(self):
        report = recheck.recheck(self.storage, snapshot(DAY1, [self.full('101', '102')]), DAY1, 'a',
                                 self.links, budget_seconds=0)
        self.assertEqual(self.links.calls, [])
        self.assertTrue(any('time' in n['reason'] for n in report['not_checked']))

    def test_shared_link_is_fetched_once_per_run(self):
        with self.storage.db:
            self.storage.db.execute("UPDATE jobs SET link='https://careers.pasted.example/jobs/77' WHERE company='Linked Co'")
        self.run_check(DAY1, [self.full('101', '102')])
        self.assertEqual(self.links.calls.count('https://careers.pasted.example/jobs/77'), 1)

    def test_first_closing_notice_is_shown_as_suspected(self):
        links = Links({'https://careers.pasted.example/jobs/77': ('closed_text', 'This job is no longer available.')})
        self.run_check(DAY1, [self.full('101', '102')], links=links)
        self.assertEqual(self.state('Pasted Co'), 'suspected')

    def test_a_board_sighting_resets_closing_text(self):
        url = 'https://example.com/exampleco/102'
        links = Links({url: ('closed_text', 'This job is no longer available.')})
        self.run_check(DAY1, [self.full('101')], links=links)
        self.run_check(DAY2, [self.full('101', '102')], links=links)
        self.run_check(DAY3, [self.full('101')], links=links)
        self.assertEqual(self.state('Report Analyst'), 'suspected')

    def test_relisted_backlog_row_clears_gone(self):
        path = Backlog.make_backlog(self)
        self.run_check(DAY1, [self.full('101', '102', '301')], backlog=path)
        self.run_check(DAY2, [self.full('101', '102', '301')], backlog=path)
        self.run_check(DAY3, [self.full('101', '102', '301', '302')], backlog=path)
        body = Backlog.bodies(self, path)['302']
        self.assertFalse(body.get('gone'))
        self.assertEqual(body.get('missed_days', []), [])

    def test_errors_are_named_in_the_report(self):
        report = self.run_check(DAY1, [self.full('999')], links=lambda link: 1 / 0)
        self.assertTrue(report['errors'])
        self.assertTrue(all(e['reason'] for e in report['errors']))

    def test_stale_boards_carry_reason_and_since_and_follow_the_profile(self):
        down = board([], read_state='failed', company='Big Example', provider='workday', reason='board down')
        for n in range(4):
            self.run_check(DAY1 + timedelta(days=n), [self.full('101', '102'), down])
        today = DAY1 + timedelta(days=3)
        stale = recheck.stale_boards(self.storage, today)
        self.assertEqual((stale[0]['since'], stale[0]['reason']), (DAY1.isoformat(), 'board down'))
        self.assertEqual(recheck.stale_boards(self.storage, today, current={('greenhouse', 'Example Co')}), [])


class ReviewFixes2(Base):
    """Round 2 re-check findings (Astra, 2026-11-16): written before the fix."""

    def test_qualified_closing_wording_after_the_phrase_is_not_closing_text(self):
        for sentence in ('We are no longer accepting applications after December 31, 2026.',
                         'We are no longer accepting applications by email; apply through this portal.'):
            self.assertEqual(recheck.classify(200, sentence)[0], 'open', sentence)

    def test_a_link_changed_during_the_fetch_is_not_judged_by_the_old_link(self):
        other = sqlite3.connect(self.db_file)
        self.addCleanup(other.close)

        def links(link):
            if link == 'https://careers.pasted.example/jobs/77':
                with other:
                    other.execute("UPDATE jobs SET link='https://careers.pasted.example/jobs/78' WHERE company='Pasted Co'")
                return ('gone', '404')
            return ('open', '')
        self.run_check(DAY1, [self.full('101', '102')], links=links)
        self.assertNotEqual(self.state('Pasted Co'), 'closed')

    def test_an_older_snapshot_never_reopens_a_newer_close(self):
        self.run_check(DAY2, [self.full('101')])
        self.run_check(DAY3, [self.full('101')])
        self.assertEqual(self.state('Report Analyst'), 'closed')
        self.run_check(DAY1, [self.full('101', '102')], run_id='replay')
        self.assertEqual(self.state('Report Analyst'), 'closed')

    def test_a_failed_posting_check_is_retried_the_same_day(self):
        answers = {'https://example.com/exampleco/102': [RuntimeError('flaky'), ('gone', '404')]}

        def links(link):
            answer = answers.get(link, [('open', '')])
            value = answer.pop(0) if len(answer) > 1 else answer[0]
            if isinstance(value, Exception):
                raise value
            return value
        first = self.run_check(DAY1, [self.full('101')], links=links, run_id='a')
        self.assertTrue(first['errors'])
        self.run_check(DAY1, [self.full('101')], links=links, run_id='b')
        self.assertEqual(self.state('Report Analyst'), 'closed')

    def test_budget_stops_new_fetches_once_spent(self):
        import time
        calls = []

        def slow(link):
            calls.append(link)
            time.sleep(0.3)
            return ('open', '')
        recheck.recheck(self.storage, snapshot(DAY1, [self.full('999')]), DAY1, 'a', slow, budget_seconds=0.5)
        self.assertLessEqual(len(calls), 2)

    def test_link_status_respects_the_deadline_before_retrying(self):
        import time
        calls = []

        def fetch(link, timeout=None):
            calls.append(timeout)
            return (429, '')
        result = recheck.link_status('https://careers.example/jobs/1', fetch=fetch, wait=1,
                                     deadline=time.monotonic() + 0.05)
        self.assertEqual(result[0], 'unclear')
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(calls[0])
        self.assertLessEqual(calls[0], 0.05 + 1e-9)


class LinkStatus(unittest.TestCase):
    def test_linkedin_is_unclear_without_a_request(self):
        self.assertEqual(recheck.link_status('https://www.linkedin.com/jobs/view/1')[0], 'unclear')

    def test_classifies_answers(self):
        cases = [((404, ''), 'gone'), ((410, ''), 'gone'), ((200, 'Apply now for this role.'), 'open'),
                 ((200, 'Sorry, this job is no longer accepting applications.'), 'closed_text'),
                 ((403, ''), 'unclear'), ((None, ''), 'unclear')]
        for (code, text), expected in cases:
            self.assertEqual(recheck.classify(code, text)[0], expected, (code, text))

    def test_429_is_retried_once(self):
        calls = []

        def fetch(link):
            calls.append(link)
            return (429, '') if len(calls) == 1 else (404, '')
        self.assertEqual(recheck.link_status('https://careers.example/jobs/1', fetch=fetch, wait=0)[0], 'gone')
        calls.clear()
        self.assertEqual(recheck.link_status('https://careers.example/jobs/1', fetch=lambda l: calls.append(l) or (429, ''),
                                             wait=0)[0], 'unclear')
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
