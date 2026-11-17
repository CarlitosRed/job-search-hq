"""Board reads for the re-check (piece 3, card 03): written before the build.

Every company and job here is fictional; the boards are the fakes from test_daily_search.py.
Run: python -I tools/hq-dashboard/test_board_reads.py
"""
from contextlib import closing
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.error import HTTPError

HERE = Path(__file__).resolve().parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fx = load_module('board_reads_fixtures', HERE / 'test_daily_search.py')
ds = fx.ds


def too_many(url):
    return HTTPError(url, 429, 'Too Many Requests', {}, io.BytesIO(b''))


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.backlog = Path(folder.name) / 'search-backlog.sqlite3'

    def search(self, fetch=None, profile=None):
        return ds.run(profile or fx.ops_profile(), fetch or fx.FakeBoards(), self.backlog, fx.TODAY)

    def by_board(self, result):
        return {b['board']: b for b in result['boards']}


class Reads(Base):
    def test_every_board_is_reported_with_what_it_saw(self):
        boards = self.by_board(self.search())
        self.assertEqual(set(boards), {'Example Cloud', 'Sample Labs', 'Demo AI', 'Big Example'})
        gh = boards['Example Cloud']
        self.assertEqual((gh['provider'], gh['read'], gh['rows']), ('greenhouse', 'complete', 6))
        self.assertIn(['101', 'https://example.com/gh/101', 'Partner Operations Analyst', 'Remote - Latin America'], gh['seen'])
        # Seen means fetched, not only candidates: the engineer posting is there too.
        self.assertIn('102', [s[0] for s in gh['seen']])
        self.assertEqual(boards['Big Example']['rows'], len(fx.WORKDAY_LIST))

    def test_failed_board_is_failed_with_a_reason(self):
        boards = self.by_board(self.search(fetch=fx.FakeBoards(broken=('demoai',))))
        self.assertEqual(boards['Demo AI']['read'], 'failed')
        self.assertEqual(boards['Demo AI']['seen'], [])
        self.assertTrue(boards['Demo AI']['reason'])
        self.assertEqual(boards['Example Cloud']['read'], 'complete')

    def test_lever_at_500_rows_is_partial(self):
        rows = [{'id': f'lv{n}', 'text': f'Clerk {n}', 'categories': {'location': 'Farland'},
                 'hostedUrl': f'https://jobs.lever.co/samplelabs/lv{n}', 'createdAt': fx.ms(1)} for n in range(500)]
        fake = fx.FakeBoards()

        def fetch(method, url, body=None):
            return rows if 'api.lever.co' in url else fake(method, url, body)
        lever = self.by_board(self.search(fetch=fetch))['Sample Labs']
        self.assertEqual(lever['read'], 'partial')
        self.assertIn('500', lever['reason'])

    def test_429_is_retried_once(self):
        fake, hits = fx.FakeBoards(), []

        def fetch(method, url, body=None):
            if 'demoai' in url:
                hits.append(url)
                if len(hits) == 1:
                    raise too_many(url)
            return fake(method, url, body)
        self.assertEqual(self.by_board(self.search(fetch=fetch))['Demo AI']['read'], 'complete')
        self.assertEqual(len(hits), 2)

    def test_429_twice_fails_that_board_only(self):
        fake = fx.FakeBoards()

        def fetch(method, url, body=None):
            if 'demoai' in url:
                raise too_many(url)
            return fake(method, url, body)
        boards = self.by_board(self.search(fetch=fetch))
        self.assertEqual(boards['Demo AI']['read'], 'failed')
        self.assertEqual(boards['Example Cloud']['read'], 'complete')


class Preserve(Base):
    def body(self, posting):
        with closing(sqlite3.connect(self.backlog)) as db:
            for identity, body in db.execute('SELECT identity, body FROM search_backlog'):
                if json.loads(identity)[2] == posting:
                    return json.loads(body)

    def test_backlog_keeps_the_richer_description(self):
        self.search()
        rich = self.body('101')['description']
        fake = fx.FakeBoards()

        def fetch(method, url, body=None):
            data = fake(method, url, body)
            if 'greenhouse' in url:
                for job in data['jobs']:
                    if job['id'] == 101:
                        job['content'] = 'Short.'
            return data
        self.search(fetch=fetch)
        self.assertEqual(self.body('101')['description'], rich)

    def test_gone_backlog_rows_never_wait_or_land(self):
        self.search()
        with closing(sqlite3.connect(self.backlog)) as db, db:
            for identity, body in db.execute('SELECT identity, body FROM search_backlog').fetchall():
                job = json.loads(body)
                if job['posting_id'] == '103':
                    job['gone'] = True
                    db.execute('UPDATE search_backlog SET body=? WHERE identity=?', (json.dumps(job), identity))
        fake = fx.FakeBoards()

        def fetch(method, url, body=None):
            data = fake(method, url, body)
            if 'greenhouse' in url:
                data['jobs'] = [job for job in data['jobs'] if job['id'] != 103]
            return data
        result = self.search(fetch=fetch)
        self.assertNotIn('103', [job['posting_id'] for job in result['landing']])
        self.assertTrue(self.body('103')['gone'], 'hidden, not deleted')


class ReviewFixes(Base):
    """Round 1 review findings (Astra and Fable, 2026-11-16): written before the fix."""

    def test_a_row_that_cannot_be_identified_makes_the_read_partial(self):
        fake = fx.FakeBoards()

        def fetch(method, url, body=None):
            data = fake(method, url, body)
            if 'greenhouse' in url:
                data['jobs'].append({'title': 'No link or id'})
            return data
        gh = self.by_board(self.search(fetch=fetch))['Example Cloud']
        self.assertEqual(gh['read'], 'partial')
        self.assertIn('101', [s[0] for s in gh['seen']])

    def test_rejected_workday_path_makes_the_read_partial(self):
        fake = fx.FakeBoards()

        def fetch(method, url, body=None):
            data = fake(method, url, body)
            if url.endswith('/Careers/jobs') and isinstance(data, dict):
                data = dict(data, jobPostings=[dict(p) for p in data['jobPostings']])
                if data['jobPostings']:
                    data['jobPostings'][-1]['externalPath'] = '../escape'
            return data
        self.assertEqual(self.by_board(self.search(fetch=fetch))['Big Example']['read'], 'partial')

    def test_seen_rows_carry_the_place_and_metadata_the_read_time(self):
        result = self.search()
        row = next(s for s in self.by_board(result)['Example Cloud']['seen'] if s[0] == '101')
        self.assertEqual(row[3], 'Remote - Latin America')
        self.assertRegex(result['metadata']['read_at'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}')

    def test_a_relisted_gone_row_waits_again(self):
        self.search()
        with closing(sqlite3.connect(self.backlog)) as db, db:
            for identity, body in db.execute('SELECT identity, body FROM search_backlog').fetchall():
                job = json.loads(body)
                if job['posting_id'] == '101':
                    job.update(gone=True, missed_days=['2026-03-08', '2026-03-09'])
                    db.execute('UPDATE search_backlog SET body=? WHERE identity=?', (json.dumps(job), identity))
        result = self.search()
        self.assertFalse(Preserve.body(self, '101').get('gone'))
        self.assertEqual(Preserve.body(self, '101').get('missed_days', []), [])
        self.assertIn('101', [job['posting_id'] for job in result['landing']])


if __name__ == '__main__':
    unittest.main()
