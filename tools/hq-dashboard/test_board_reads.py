"""Board reads for the re-check (piece 3, card 03): written before the build.

Every company and job here is fictional; the boards are the fakes from test_daily_search.py.
Run: python -I tools/hq-dashboard/test_board_reads.py
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

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


class WorkdayReads(Base):
    def setUp(self):
        super().setUp()
        self.profile = fx.ops_profile()
        self.company = next(c for c in self.profile['search']['companies'] if c['provider'] == 'workday')
        self.profile['search']['companies'] = [self.company]
        self.calls, self.waits, self.failures, self.request_time = [], [], {}, {}
        self.elapsed = 0
        self.wall = datetime(2026, 3, 10, tzinfo=timezone.utc).timestamp()
        self.listings = [fx.WorkdayHomeFirst.listing(n, 'Exampleland') for n in range(41)]
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def reply(self, offset, data):
                owner.calls.append((self.path, offset))
                owner.elapsed += owner.request_time.get(offset, 0)
                failures = owner.failures.get(offset, [])
                status, headers = failures.pop(0) if failures else (200, {})
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(json.dumps(data if status == 200 else {'secret': 'never log this'}).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                offset = body['offset']
                self.reply(offset, {'total': len(owner.listings),
                                    'jobPostings': owner.listings[offset:offset + body['limit']]})

            def do_GET(self):
                self.reply(None, {'jobPostingInfo': {'location': 'Exampleland'}})

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        local = build_opener(ProxyHandler({}))

        class LocalOpener:
            def open(self, request, timeout):
                target = f'http://127.0.0.1:{server.server_port}' + request.selector
                return local.open(Request(target, data=request.data, method=request.method,
                                          headers=dict(request.header_items())), timeout=timeout)

        self.enterContext(mock.patch.object(ds.boards, 'build_opener', return_value=LocalOpener()))
        clock = mock.Mock(monotonic=lambda: self.elapsed, sleep=self.sleep, time=lambda: self.wall + self.elapsed)
        self.enterContext(mock.patch.object(ds, 'time', clock))
        self.enterContext(mock.patch.object(ds.boards, 'time', clock))

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.elapsed += seconds

    def run_search(self, day=fx.TODAY, mark_landed=False):
        return ds.run(self.profile, ds.boards.fetch_json, self.backlog, day, mark_landed=mark_landed)

    def offsets(self):
        return [offset for _, offset in self.calls if offset is not None]

    def test_429_uses_retry_after_seconds(self):
        self.failures[20] = [(429, {'Retry-After': '7'})]
        self.assertEqual(self.run_search()['boards'][0]['read'], 'complete')
        self.assertEqual(self.offsets(), [0, 20, 20, 40])
        self.assertEqual(self.waits[:3], [1.5, 7, 1.5])

    def test_429_without_header_retries_three_times_and_keeps_rows(self):
        self.failures[20] = [(429, {})] * 4
        result = self.run_search()
        read = result['boards'][0]
        self.assertEqual(self.offsets(), [0, 20, 20, 20, 20])
        self.assertEqual(self.waits[:4], [1.5, 5, 15, 45])
        self.assertEqual((read['read'], read['rows'], read['next_offset']), ('partial', 20, 20))
        self.assertEqual(read['http_status'], 429)
        self.assertIn('Workday asked us to slow down after page 1 of 3', read['reason'])
        self.assertIn('20 rows', read['reason'])
        self.assertNotIn('never log this', read['reason'])
        self.assertTrue(result['landing'])

    def test_503_uses_retry_after_http_date(self):
        retry = format_datetime(datetime.fromtimestamp(self.wall + 10, timezone.utc), usegmt=True)
        self.failures[0] = [(503, {'Retry-After': retry})]
        self.assertEqual(self.run_search()['boards'][0]['read'], 'complete')
        self.assertEqual(self.offsets(), [0, 0, 20, 40])
        self.assertEqual(self.waits[:3], [10, 1.5, 1.5])

    def test_board_budget_stops_before_wait_and_uses_configured_page_pause(self):
        self.profile['search'].update(workday_page_pause_seconds=2.5, workday_board_budget_seconds=3)
        self.failures[20] = [(429, {'Retry-After': '60'})]
        read = self.run_search()['boards'][0]
        self.assertEqual(self.offsets(), [0, 20])
        self.assertEqual(self.waits[:1], [2.5])
        self.assertNotIn(60, self.waits)
        self.assertEqual(read['next_offset'], 20)
        self.assertIn('board time budget', read['reason'])
        self.assertIn('HTTP 429', read['reason'])

    def test_run_budget_defers_other_workday_boards_and_rotates_next_day(self):
        self.profile['search']['companies'].append(dict(self.company, label='Second Example', board='second'))
        self.profile['search']['workday_run_budget_seconds'] = 2
        self.request_time[20] = 0.5
        first, second = self.run_search()['boards']
        self.assertEqual(self.offsets(), [0, 20])
        self.assertIn('run time budget', first['reason'])
        self.assertIn('run time budget', second['reason'])
        self.assertEqual(second['rows'], 0)
        self.calls.clear()
        self.run_search(fx.TODAY + timedelta(days=1))
        self.assertIn('/second/', self.calls[0][0])

    def test_next_day_resumes_then_starts_fresh_without_relanding(self):
        self.failures[20] = [(429, {})] * 4
        first = self.run_search(mark_landed=True)
        landed = {job['posting_id'] for job in first['landing']}
        self.assertTrue(landed)
        self.calls.clear()
        second = self.run_search(fx.TODAY + timedelta(days=1), mark_landed=True)
        self.assertEqual(self.offsets(), [20, 40])
        self.assertEqual(second['boards'][0]['read'], 'complete')
        self.assertFalse(second['boards'][0]['full_read'])
        self.calls.clear()
        third = self.run_search(fx.TODAY + timedelta(days=2))
        self.assertEqual(self.offsets(), [0, 20, 40])
        self.assertFalse(landed & {job['posting_id'] for job in third['landing']})

    def test_sorted_board_stops_at_the_45_day_boundary(self):
        self.company['newest_first'] = True
        for n, listing in enumerate(self.listings):
            listing['postedOn'] = f'Posted {45 if n == 0 else 46} Days Ago'
        result = self.run_search()
        self.assertEqual(self.offsets(), [0])
        self.assertEqual(result['boards'][0]['read'], 'complete')
        self.assertFalse(result['boards'][0]['full_read'])
        self.assertEqual([job['posting_id'] for job in result['landing']], ['R0'])

    def test_unknown_dates_keep_paging(self):
        self.company['newest_first'] = True
        for n, listing in enumerate(self.listings):
            listing['postedOn'] = 'Posted 30+ Days Ago' if n == 0 else 'Posted 46 Days Ago'
        self.listings[20]['postedOn'] = 'Posted Today'
        self.assertIn('R20', [job['posting_id'] for job in self.run_search()['landing']])
        self.assertEqual(self.offsets(), [0, 20, 40])

    def test_same_day_rerun_replays_the_segment(self):
        self.failures[20] = [(429, {})] * 4
        self.run_search()
        self.calls.clear()
        self.run_search()
        self.assertEqual(self.offsets(), [0, 20, 40])

    def test_workday_report_counts_continuations(self):
        self.failures[20] = [(503, {})] * 4
        self.assertEqual(self.run_search()['metadata']['workday'],
                         {'read': 0, 'total': 1, 'continue_tomorrow': 1})


if __name__ == '__main__':
    unittest.main()
