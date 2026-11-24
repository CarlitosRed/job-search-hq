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
import time
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

    def test_rejected_workday_path_leaves_the_read_complete(self):
        fake = fx.FakeBoards()

        def fetch(method, url, body=None):
            data = fake(method, url, body)
            if url.endswith('/Careers/jobs') and body['offset'] == 0:
                data['jobPostings'][-1] = dict(data['jobPostings'][-1], externalPath='../escape')
            return data
        board = self.by_board(self.search(fetch=fetch))['Big Example']
        self.assertEqual(board['read'], 'complete')
        self.assertIn('1 rows skipped (no usable posting path)', board['reason'])

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
        self.details = {}
        self.facets, self.filtered, self.requests = {}, {}, []
        self.elapsed = 0
        self.wall = datetime(2026, 3, 10, tzinfo=timezone.utc).timestamp()
        self.listings = [fx.WorkdayHomeFirst.listing(n, 'Exampleland') for n in range(41)]
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def reply(self, offset, data):
                owner.calls.append((self.path, offset))
                owner.elapsed += owner.request_time.get(offset, 0)
                failures = owner.failures.get(self.path if self.path in owner.failures else offset, [])
                status, headers = failures.pop(0) if failures else (200, {})
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(json.dumps(data if status == 200 else {'secret': 'never log this'}).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                offset = body['offset']
                applied = body['appliedFacets']
                owner.requests.append((self.path, applied, offset, owner.elapsed))
                listings = owner.filtered.get(self.path, []) if applied else owner.listings
                self.reply(offset, {'total': len(listings), 'facets': owner.facets.get(self.path, []),
                                    'jobPostings': listings[offset:offset + body['limit']]})

            def do_GET(self):
                info = {'location': 'Exampleland', **owner.details.get(self.path, {})}
                self.reply(None, {'jobPostingInfo': info})

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

    def saved_offsets(self):
        progress = ds.workday_progress(self.backlog, self.profile['id'])
        return progress[ds.workday_key(self.company)][1:]

    def enrich_first_page_yesterday(self):
        listings = self.listings
        self.listings = listings[:20]
        self.run_search(fx.TODAY - timedelta(days=1))
        self.listings = listings
        self.calls.clear()
        self.waits.clear()

    def test_later_page_failure_keeps_pending_rows_and_replays_their_page(self):
        self.failures[20] = [(500, {})]
        first = self.run_search(mark_landed=True)
        read = first['boards'][0]
        self.assertEqual(read['read'], 'partial')
        self.assertEqual([row[0] for row in read['seen']], [f'R{n}' for n in range(20)])
        self.assertEqual(first['metadata']['counts']['candidates'], 0)
        self.assertEqual(first['landing'], [])
        self.assertEqual(self.saved_offsets(), (0, 0))
        self.assertIn('20 job details deferred', read['reason'])
        self.calls.clear()
        second = self.run_search(fx.TODAY + timedelta(days=1))
        self.assertEqual(self.offsets(), [0, 20, 40])
        self.assertTrue(any(job['posting_id'] == 'R0' for job in second['landing']))

    def test_cold_read_advances_past_the_posting_cap(self):
        self.profile['search']['workday_page_pause_seconds'] = 0
        with mock.patch.object(ds.boards, 'MAX_POSTINGS', 40):
            seen = {row[0] for day in range(3)
                    for row in self.run_search(fx.TODAY + timedelta(days=day))['boards'][0]['seen']}
        self.assertEqual(seen, {f'R{n}' for n in range(41)})

    def test_cold_read_shares_the_time_budget_between_paging_and_details(self):
        self.profile['search'].update(workday_page_pause_seconds=2, workday_board_budget_seconds=3)
        seen = set()
        for day in range(3):
            started = self.elapsed
            result = self.run_search(fx.TODAY + timedelta(days=day))
            seen.update(row[0] for row in result['boards'][0]['seen'])
            self.assertLess(self.elapsed - started, 3)
        self.assertEqual(seen, {f'R{n}' for n in range(41)})
        self.assertTrue(Preserve.body(self, 'R0'))

    def test_persistent_detail_failures_rotate_behind_the_untried_match(self):
        for listing in self.listings[:40]:
            self.failures['/wday/cxs/bigex/Careers' + listing['externalPath']] = [(404, {})] * 3
        results = []
        for day in range(3):
            self.calls.clear()
            results.append(self.run_search(fx.TODAY + timedelta(days=day)))
            reads = [path for path, offset in self.calls if offset is None]
            self.assertLessEqual(len(reads), 40)
            if day == 1:
                self.assertTrue(reads[0].endswith('_R40'))
            if day == 2:
                self.assertTrue(reads[0].endswith('_R39'))
        self.assertTrue(any(job['posting_id'] == 'R40' for result in results for job in result['landing']))

    def test_fresh_details_replace_the_older_sponsorship_and_location(self):
        self.listings = self.listings[:1]
        path = '/wday/cxs/bigex/Careers' + self.listings[0]['externalPath']
        self.details[path] = {'jobDescription': 'We offer relocation assistance and will sponsor your visa for this position.',
                              'location': 'Old City, Exampleland', 'remoteType': 'Remote', 'startDate': fx.ago(5)}
        self.run_search()
        self.listings[0]['title'] += ' Updated'
        self.details[path] = {'jobDescription': 'No visa sponsorship.', 'location': 'New City, Exampleland',
                              'startDate': fx.ago(2)}
        for day in (1, 2):
            job = self.run_search(fx.TODAY + timedelta(days=day))['landing'][0]
            self.assertIn('No visa sponsorship.', job['description'])
            self.assertNotIn('will sponsor', job['description'])
            self.assertEqual(job['place_text'], 'New City, Exampleland')
            self.assertFalse(job['remote'])
            self.assertEqual((job['posted_on'], job['date_source']), (fx.ago(2), 'workday'))

    def test_refreshed_workday_date_does_not_reset_the_landing_window(self):
        self.listings = self.listings[:1]
        path = '/wday/cxs/bigex/Careers' + self.listings[0]['externalPath']
        self.details[path] = {'startDate': fx.ago(45)}
        self.assertTrue(self.run_search()['landing'])
        self.listings[0]['title'] += ' Updated'
        self.details[path] = {'startDate': fx.ago(1)}
        result = self.run_search(fx.TODAY + timedelta(days=1))
        self.assertEqual(result['landing'], [])
        self.assertEqual(Preserve.body(self, 'R0')['posted_on'], fx.ago(1))

    def test_waiting_detail_cache_expires_after_seven_days(self):
        self.listings = self.listings[:2]
        self.profile['search']['max_per_company'] = 1
        path = '/wday/cxs/bigex/Careers' + self.listings[1]['externalPath']
        self.run_search(mark_landed=True)
        self.details[path] = {'jobDescription': 'No visa sponsorship.', 'location': 'New City, Exampleland'}
        self.calls.clear()
        self.run_search(fx.TODAY + timedelta(days=7))
        self.assertFalse(any(offset is None for _, offset in self.calls))
        job = self.run_search(fx.TODAY + timedelta(days=8))['landing'][0]
        self.assertEqual([url for url, offset in self.calls if offset is None], [path])
        self.assertEqual(job['posting_id'], 'R1')
        self.assertEqual(job['place_text'], 'New City, Exampleland')
        self.assertIn('No visa sponsorship.', job['description'])

    def test_30_plus_cutoff_allows_a_newer_job_on_the_next_page(self):
        self.listings = [fx.WorkdayHomeFirst.listing(n, 'Exampleland') for n in range(1000)]
        for n, listing in enumerate(self.listings):
            listing['postedOn'] = 'Posted 30+ Days Ago'
            if n != 20:
                listing['title'] = 'Example Clerk'
            path = '/wday/cxs/bigex/Careers' + listing['externalPath']
            self.details[path] = {'startDate': fx.ago(46 if n < 20 else 45 if n == 20 else 53)}
        result = self.run_search()
        self.assertEqual(self.offsets(), [0, 20])
        self.assertIn('R20', [row[0] for row in result['boards'][0]['seen']])
        self.assertEqual([job['posting_id'] for job in result['landing']], ['R20'])

    def test_list_exclusions_leave_detail_slots_for_the_eligible_match(self):
        self.profile['search']['exclude_titles'] = [{'pattern': 'Excluded', 'reason': 'Filtered'}]
        for listing in self.listings[:40]:
            listing['title'] += ' Excluded'
        for day in range(2):
            result = self.run_search(fx.TODAY + timedelta(days=day))
            self.assertEqual(result['metadata']['counts']['candidates'], 1)
            self.assertEqual(result['metadata']['counts']['excluded'], 40)
            self.assertEqual([job['posting_id'] for job in result['landing']], ['R40'])
        self.assertEqual([path.rsplit('_', 1)[1] for path, offset in self.calls if offset is None], ['R40'])

    def test_posting_http_errors_are_counted_and_healthy_details_continue(self):
        for day in range(2):
            for listing, status in zip(self.listings, (404, 410, 422)):
                self.failures['/wday/cxs/bigex/Careers' + listing['externalPath']] = [(status, {})]
            result = self.run_search(fx.TODAY + timedelta(days=day))
            read = result['boards'][0]
            self.assertEqual((read['read'], len(read['seen'])), ('complete', 41))
            self.assertEqual(result['metadata']['counts']['candidates'], 37 + day)
            self.assertEqual(read.get('skipped_details'), 3)
            self.assertIsNone(Preserve.body(self, 'R0'))
            self.assertTrue(Preserve.body(self, 'R3'))

    def test_all_41_eligible_jobs_land_over_42_mornings(self):
        self.profile['search']['max_per_company'] = 1
        posting_ids = [f'R{n}' for n in range(41)]
        landed, last_read = [], {}
        for day in range(42):
            self.calls.clear()
            result = self.run_search(fx.TODAY + timedelta(days=day), mark_landed=True)
            expected = [posting for posting in posting_ids if posting not in landed
                        and (posting not in last_read or day - last_read[posting] > 7)]
            reads = [path.rsplit('_', 1)[1] for path, offset in self.calls if offset is None]
            self.assertCountEqual(reads, expected[:40], f'morning {day}')
            last_read.update(dict.fromkeys(reads, day))
            landed.extend(job['posting_id'] for job in result['landing'])
        self.assertCountEqual(landed, posting_ids)

    def test_changed_workday_list_row_refreshes_its_details(self):
        self.listings = self.listings[:1]
        self.run_search()
        self.listings[0]['title'] += ' Updated'
        path = '/wday/cxs/bigex/Careers' + self.listings[0]['externalPath']
        self.details[path] = {'location': 'Updated City, Exampleland'}
        self.calls.clear()
        second = self.run_search(fx.TODAY + timedelta(days=1))
        self.assertEqual([url for url, offset in self.calls if offset is None], [path])
        self.assertEqual(second['landing'][0]['place_text'], 'Updated City, Exampleland')

    def test_board_wide_detail_errors_stop_sibling_requests(self):
        self.listings = self.listings[:2]
        for status in (408, 500):
            with self.subTest(status=status):
                self.calls.clear()
                self.failures[None] = [(status, {})]
                result = self.run_search()
                read = result['boards'][0]
                self.assertEqual((read['read'], read['full_read'], read['http_status']), ('complete', True, status))
                self.assertEqual(read['skipped_details'], 0)
                self.assertEqual(result['metadata']['counts']['candidates'], 0)
                self.assertEqual(sum(offset is None for _, offset in self.calls), 1)

    def test_429_uses_retry_after_seconds(self):
        self.failures[20] = [(429, {'Retry-After': '7'})]
        self.assertEqual(self.run_search()['boards'][0]['read'], 'complete')
        self.assertEqual(self.offsets(), [0, 20, 20, 40])
        self.assertEqual(self.waits[:3], [1.5, 7, 1.5])

    def test_429_without_header_backs_off_and_keeps_rows(self):
        self.enrich_first_page_yesterday()
        self.failures[20] = [(429, {})] * 4
        result = self.run_search()
        read = result['boards'][0]
        self.assertEqual(self.offsets(), [0, 20, 20, 20, 20])
        self.assertEqual(self.waits[:4], [1.5, 30, 60, 120])
        self.assertEqual((read['read'], read['rows']), ('partial', 20))
        self.assertEqual(self.saved_offsets(), (0, 20))
        self.assertNotIn('next_offset', read)
        self.assertNotIn('start_offset', read)
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
        self.enrich_first_page_yesterday()
        self.profile['search'].update(workday_page_pause_seconds=2.5, workday_board_budget_seconds=3)
        self.failures[20] = [(429, {'Retry-After': '60'})]
        read = self.run_search()['boards'][0]
        self.assertEqual(self.offsets(), [0, 20])
        self.assertEqual(self.waits[:1], [2.5])
        self.assertNotIn(60, self.waits)
        self.assertEqual(self.saved_offsets(), (0, 20))
        self.assertIn('board time budget', read['reason'])
        self.assertIn('HTTP 429', read['reason'])

    def test_run_budget_defers_other_workday_boards_and_rotates_next_day(self):
        first, second = self.country_boards()
        # Give both boards the same last-read date so the first board starts today.
        self.run_search(fx.TODAY - timedelta(days=1))
        self.requests.clear()
        self.filtered[first] = [dict(fx.WorkdayHomeFirst.listing(n, 'Exampleland'), title='Example Clerk')
                                for n in range(41)]
        self.profile['search']['workday_page_pause_seconds'] = 1.5
        self.profile['search']['workday_run_budget_seconds'] = 2
        self.request_time[20] = 0.5
        first_read, second_read = self.run_search()['boards']
        home = {'Location_Country': ['home-id', 'other-id']}
        self.assertEqual([request[:3] for request in self.requests],
                         [(first, home, 0), (first, home, 20)])
        self.assertEqual((first_read['read'], first_read['rows']), ('partial', 20))
        self.assertIn('run time budget', first_read['reason'])
        self.assertIn('run time budget', second_read['reason'])
        self.assertEqual(second_read['rows'], 0)
        self.requests.clear()
        self.run_search(fx.TODAY + timedelta(days=1))
        self.assertEqual([request[:3] for request in self.requests[:2]], [
            (second, {}, 0), (second, {'Country_and_Jurisdiction': ['second-id']}, 0)])

    def test_next_day_resumes_then_starts_fresh_without_relanding(self):
        self.enrich_first_page_yesterday()
        self.failures[20] = [(429, {})] * 4
        first = self.run_search(mark_landed=True)
        landed = {job['posting_id'] for job in first['landing']}
        self.assertTrue(landed)
        self.calls.clear()
        second = self.run_search(fx.TODAY + timedelta(days=1), mark_landed=True)
        self.assertEqual(self.offsets(), [0, 20, 40])
        self.assertEqual(second['boards'][0]['read'], 'complete')
        self.assertFalse(second['boards'][0]['full_read'])
        self.calls.clear()
        third = self.run_search(fx.TODAY + timedelta(days=2))
        self.assertEqual(self.offsets(), [0, 20, 40])
        self.assertFalse(landed & {job['posting_id'] for job in third['landing']})

    def test_sorted_board_stops_at_the_45_day_boundary(self):
        self.company['newest_first'] = True
        for n, listing in enumerate(self.listings):
            listing['postedOn'] = f'Posted {45 if n == 0 else 53} Days Ago'
        result = self.run_search()
        self.assertEqual(self.offsets(), [0, 20])
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

    def test_default_sort_uses_the_newest_row_on_each_page(self):
        for n, listing in enumerate(self.listings):
            listing['postedOn'] = f'Posted {54 if n == 20 else 53 if n > 20 else 2} Days Ago'
        read = self.run_search()['boards'][0]
        self.assertEqual(self.offsets(), [0, 20])
        self.assertEqual(read['reason'], 'Workday: finished postings from the last 45 days after page 2 of 3.')
        self.assertEqual((read['read'], read['full_read']), ('complete', False))

    def test_explicit_sort_opt_out_reads_every_page(self):
        self.company['newest_first'] = False
        for listing in self.listings:
            listing['postedOn'] = 'Posted 46 Days Ago'
        read = self.run_search()['boards'][0]
        self.assertEqual(self.offsets(), [0, 20, 40])
        self.assertTrue(read['full_read'])

    def test_resumed_30_plus_page_probes_last_posting_and_finishes_window(self):
        for listing in self.listings[20:]:
            listing.update(title='Example Clerk', postedOn='Posted 30+ Days Ago')
        probe = '/wday/cxs/bigex/Careers' + self.listings[39]['externalPath']
        self.details[probe] = {'startDate': (fx.TODAY - timedelta(days=53)).isoformat()}
        self.enrich_first_page_yesterday()
        self.failures[20] = [(429, {})] * 4
        self.run_search()
        self.calls.clear()
        self.waits.clear()
        self.failures[None] = [(429, {'Retry-After': '7'})]
        read = self.run_search(fx.TODAY + timedelta(days=1))['boards'][0]
        self.assertEqual(self.offsets(), [0, 20])
        self.assertEqual([path for path, offset in self.calls if offset is None], [probe, probe])
        self.assertEqual(self.waits, [1.5, 7])
        self.assertEqual(read['read'], 'complete')
        self.assertFalse(read['full_read'])
        self.assertEqual([row[0] for row in read['seen']], [f'R{n}' for n in range(20, 40)])
        self.assertEqual(self.saved_offsets(), (20, 0))
        self.calls.clear()
        self.run_search(fx.TODAY + timedelta(days=2))
        self.assertEqual(self.offsets()[0], 0)

    def test_30_plus_probes_keep_paging_without_an_old_exact_date(self):
        for listing in self.listings[:40]:
            listing['postedOn'] = 'Posted 30+ Days Ago'
        probes = ['/wday/cxs/bigex/Careers' + self.listings[n]['externalPath'] for n in (19, 39)]
        self.details[probes[1]] = {'startDate': (fx.TODAY - timedelta(days=45)).isoformat()}
        rows = ds.boards.board_rows(self.company, detail_match=lambda title, text: title.endswith(' 39'),
                                    today=fx.TODAY)
        self.assertEqual(self.offsets(), [0, 20, 40])
        self.assertEqual([path for path, offset in self.calls if offset is None], probes)
        self.assertTrue(rows.full_read)
        self.assertEqual(rows[19]['_posted'], '')
        self.assertEqual(rows[39]['_posted'], self.details[probes[1]]['startDate'])

    def test_detail_throttle_keeps_list_complete_and_new_jobs_go_first_tomorrow(self):
        self.listings = self.listings[:14]
        self.listings[9]['locationsText'] = 'Otherland'
        self.listings[10]['locationsText'] = '2 Locations'
        self.profile['search']['workday_board_budget_seconds'] = 16
        self.failures[None] = [(200, {})] * 9 + [(429, {'Retry-After': '60'})]
        first = self.run_search()
        read = first['boards'][0]
        self.assertEqual((read['read'], read['full_read']), ('complete', True))
        self.assertEqual(self.saved_offsets(), (0, 0))
        self.assertEqual(len(read['seen']), 14)
        self.assertEqual(first['metadata']['counts']['candidates'], 9)
        self.assertIsNone(Preserve.body(self, 'R9'))
        self.assertTrue(Preserve.body(self, 'R8'))
        self.assertEqual(read['reason'], 'Workday finished the list (page 1 of 1); job details stopped '
                         'after 9 of 14 (asked us to slow down). 5 continue tomorrow.')
        self.calls.clear()
        self.profile['search']['workday_board_budget_seconds'] = 360
        self.run_search(fx.TODAY + timedelta(days=1))
        reads = [path for path, offset in self.calls if offset is None]
        self.assertEqual([path.rsplit('_', 1)[1] for path in reads[:5]], ['R11', 'R12', 'R13', 'R10', 'R9'])
        self.assertTrue(Preserve.body(self, 'R9'))

    def test_detail_cap_defers_matching_rows_without_hiding_them_from_the_list(self):
        result = self.run_search()
        read = result['boards'][0]
        self.assertEqual((read['read'], read['full_read']), ('complete', True))
        self.assertEqual(len(read['seen']), 41)
        self.assertEqual(result['metadata']['counts']['candidates'], 40)
        self.assertIsNone(Preserve.body(self, 'R40'))
        self.assertEqual(read['reason'], 'Workday finished the list (page 3 of 3); job details stopped '
                         'after 40 of 41 (reached the detail limit). 1 continues tomorrow.')
        self.calls.clear()
        self.run_search(fx.TODAY + timedelta(days=1))
        first_detail = next(path for path, offset in self.calls if offset is None)
        self.assertTrue(first_detail.endswith('_R40'))
        self.assertTrue(Preserve.body(self, 'R40'))

    def test_unusable_rows_are_counted_and_other_details_are_read(self):
        self.listings = [self.listings[0], None, dict(self.listings[1], externalPath='../escape')]
        rows = ds.boards.board_rows(self.company, detail_match=lambda title, text: True, today=fx.TODAY)
        self.assertEqual((rows.read, rows.full_read), ('complete', True))
        self.assertEqual(rows.skipped_rows, 2)
        self.assertEqual(len(rows), 1)
        self.assertTrue(any(offset is None for _, offset in self.calls))
        read = self.run_search()['boards'][0]
        self.assertEqual(read['read'], 'complete')
        self.assertEqual(len(read['seen']), 1)
        self.assertIn('2 rows skipped (no usable posting path)', read['reason'])

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

    @staticmethod
    def facet(name, *values):
        return {'facetParameter': name, 'values': [{'descriptor': label, 'id': token} for label, token in values]}

    def country_boards(self):
        self.listings = [dict(row, title='Example Clerk') for row in self.listings[:1]]
        self.profile['search']['workday_page_pause_seconds'] = 0
        first = '/wday/cxs/bigex/Careers/jobs'
        second = '/wday/cxs/second/Careers/jobs'
        self.profile['search']['companies'].append(dict(
            self.company, label='Second Example', board='second', host='second.wd1.myworkdayjobs.com'))
        self.profile['work_rules']['authorized_countries'].append('Otherland')
        self.facets[first] = [self.facet('Location', ('Exampleland', 'city-id')),
                              {'facetParameter': 'locationMainGroup', 'values': [
                                  self.facet('Location_Country', ('EXAMPLELAND', 'home-id'),
                                             ('Otherland', 'other-id'), ('New Exampleland', 'wrong-id'))]}]
        self.facets[second] = [self.facet('Country_and_Jurisdiction', ('Otherland', 'second-id'))]
        self.filtered.update({first: self.listings, second: self.listings})
        return first, second

    def test_country_facets_page_first_and_worldwide_runs_after_both_boards(self):
        first, second = self.country_boards()
        self.filtered[first] = [dict(row, title='Example Clerk')
                                for row in [fx.WorkdayHomeFirst.listing(n, 'Exampleland') for n in range(21)]]
        result = self.run_search()
        home = {'Location_Country': ['home-id', 'other-id']}
        self.assertEqual([request[:3] for request in self.requests], [
            (first, {}, 0), (first, home, 0), (first, home, 20),
            (second, {}, 0), (second, {'Country_and_Jurisdiction': ['second-id']}, 0),
            (first, {}, 0), (second, {}, 0)])
        read = result['boards'][0]
        self.assertEqual((read['read'], read['rows'], read['full_read']), ('complete', 21, False))
        self.assertEqual(read['worldwide']['rows'], 1)

    def test_saved_country_facets_skip_discovery_and_400_rediscover_once(self):
        first, _ = self.country_boards()
        self.profile['search']['companies'] = [self.company]
        self.run_search()
        cold_requests = len(self.requests)
        self.requests.clear()
        self.run_search(fx.TODAY + timedelta(days=1))
        self.assertEqual(len(self.requests), cold_requests - 1)
        self.assertEqual(self.requests[0][1], {'Location_Country': ['home-id', 'other-id']})
        self.requests.clear()
        self.facets[first] = [self.facet('locationCountry', ('Exampleland', 'new-id'))]
        self.failures[0] = [(400, {}), (200, {}), (400, {})]
        read = self.run_search(fx.TODAY + timedelta(days=2))['boards'][0]
        self.assertEqual([applied for _, applied, _, _ in self.requests],
                         [{'Location_Country': ['home-id', 'other-id']}, {}, {'locationCountry': ['new-id']}])
        self.assertEqual((read['read'], read['http_status']), ('failed', 400))
        self.requests.clear()
        self.run_search(fx.TODAY + timedelta(days=3))
        self.assertEqual(self.requests[0][1], {})

    def test_partial_saved_country_filter_discovers_newly_listed_home_country(self):
        _, second = self.country_boards()
        self.profile['search']['companies'] = [self.profile['search']['companies'][1]]
        other = dict(fx.WorkdayHomeFirst.listing(1, 'Otherland'), title='Example Clerk')
        self.filtered[second] = self.listings = [other]
        first_day = self.run_search()['boards'][0]
        self.assertEqual([row[0] for row in first_day['seen']], ['R1'])
        self.facets[second][0]['values'].insert(0, {'descriptor': 'Exampleland', 'id': 'new-home'})
        home = dict(fx.WorkdayHomeFirst.listing(0, 'Exampleland'), title='Example Clerk')
        self.filtered[second] = self.listings = [home, other]

        def fetch(method, url, body):
            if method == 'POST' and body['appliedFacets'] == {'Country_and_Jurisdiction': ['second-id']}:
                return {'total': 1, 'jobPostings': [other], 'facets': self.facets[second]}
            return ds.boards.fetch_json(method, url, body)

        result = ds.run(self.profile, fetch, self.backlog, fx.TODAY + timedelta(days=1))
        read = result['boards'][0]
        self.assertEqual((read['read'], [row[0] for row in read['seen']]), ('complete', ['R0', 'R1']))

    def test_failed_country_rediscovery_keeps_rows_from_saved_filter(self):
        first, _ = self.country_boards()
        countries = {'exampleland': 'Exampleland', 'otherland': 'Otherland'}
        saved = ds.boards.board_rows(self.company, countries=countries, page_pause=0).saved_facets
        self.filtered[first] = [fx.WorkdayHomeFirst.listing(n, 'Exampleland') for n in range(21)]
        self.failures[20] = [(400, {})]
        self.failures[0] = [(200, {}), (500, {})]
        rows = ds.boards.board_rows(
            self.company, countries=countries, saved_facets=saved,
            page_pause=0)
        self.assertEqual((rows.read, rows.rows_read, len(rows), rows.http_status), ('partial', 20, 20, 500))
        self.assertIsNone(rows.saved_facets)
        self.assertFalse(rows.full_read)

    def test_multiple_ids_for_one_country_do_not_make_a_full_filter(self):
        _, second = self.country_boards()
        company = self.profile['search']['companies'][1]
        self.profile['search']['companies'] = [company]
        self.facets[second][0]['values'].append({'descriptor': 'OTHERLAND', 'id': 'another-id'})
        other = [dict(fx.WorkdayHomeFirst.listing(n, 'Otherland'), title='Example Clerk') for n in (1, 2)]
        self.filtered[second] = self.listings = other
        self.run_search()
        self.facets[second][0]['values'].append({'descriptor': 'Exampleland', 'id': 'home-id'})
        home = dict(fx.WorkdayHomeFirst.listing(0, 'Exampleland'), title='Example Clerk')
        self.filtered[second] = self.listings = [home, *other]

        def fetch(method, url, body):
            if method == 'POST' and body['appliedFacets'] == {'Country_and_Jurisdiction': ['second-id', 'another-id']}:
                return {'total': 2, 'jobPostings': other, 'facets': self.facets[second]}
            return ds.boards.fetch_json(method, url, body)

        read = ds.run(self.profile, fetch, self.backlog, fx.TODAY + timedelta(days=1))['boards'][0]
        self.assertEqual((read['read'], [row[0] for row in read['seen']]), ('complete', ['R0', 'R1', 'R2']))

    def test_successful_country_rediscovery_keeps_rows_from_saved_filter(self):
        first, _ = self.country_boards()
        countries = {'exampleland': 'Exampleland', 'otherland': 'Otherland'}
        saved = ds.boards.board_rows(self.company, countries=countries, page_pause=0).saved_facets
        old_filter = {'Location_Country': ['home-id', 'other-id']}
        new_facets = [self.facet('locationCountry', ('Exampleland', 'new-home'), ('Otherland', 'new-other'))]
        listings = [fx.WorkdayHomeFirst.listing(n, 'Exampleland') for n in range(21)]

        def fetch(method, url, body):
            if body['appliedFacets'] == old_filter:
                if body['offset']:
                    raise HTTPError(url, 400, 'fixture', {}, None)
                return {'total': 21, 'jobPostings': listings[:20], 'facets': self.facets[first]}
            return {'total': 1, 'jobPostings': listings[20:], 'facets': new_facets}

        rows = ds.boards.board_rows(self.company, fetch, countries=countries, saved_facets=saved, page_pause=0)
        self.assertEqual((rows.read, rows.rows_read, [row['bulletFields'][0] for row in rows]),
                         ('complete', 21, [f'R{n}' for n in range(21)]))

    def test_cold_facet_discovery_starts_at_zero_with_a_worldwide_cursor(self):
        self.listings = self.listings[:1]
        path = '/wday/cxs/bigex/Careers/jobs'
        self.facets[path] = [self.facet('locationCountry', ('Exampleland', 'home-id'))]
        self.filtered[path] = self.listings

        def fetch(method, url, body):
            if body['offset']:
                return {'total': 0, 'jobPostings': [], 'facets': []}
            return ds.boards.fetch_json(method, url, body)

        rows = ds.boards.board_rows(self.company, fetch, countries={'exampleland': 'Exampleland'},
                                    start_offset=20, page_pause=0)
        self.assertEqual([request[:3] for request in self.requests], [
            (path, {}, 0), (path, {'locationCountry': ['home-id']}, 0)])
        self.assertEqual((rows.read, [row['bulletFields'][0] for row in rows]), ('complete', ['R0']))

    def test_finished_fallback_page_survives_the_other_home_read(self):
        self.listings = self.listings[:1]
        queue = ds.boards.WorkdayQueue(10)

        def fallback(deadline):
            return ds.boards.board_rows(self.company, detail_match=lambda title, text: True,
                                        countries={'exampleland': 'Exampleland'},
                                        run_deadline=deadline, page_pause=0, queue=queue)

        def other(deadline):
            self.assertEqual(self.offsets(), [0])
            self.elapsed = 11
            yield from ()

        results = dict(queue.read([(0, self.company['host'], fallback), (1, 'other.example', other)]))
        self.assertIsNone(results[1])
        rows = results[0]
        self.assertEqual((rows.read, [row['bulletFields'][0] for row in rows]), ('complete', ['R0']))
        self.assertTrue(rows.full_read)
        self.assertEqual([offset for _, offset in self.calls], [0])

    def test_workday_queue_stops_waiting_while_shared_lock_is_held(self):
        lock = threading.Lock()
        lock.acquire()
        self.addCleanup(lock.release)
        with mock.patch.object(ds.boards, '_WORKDAY_SLOT', lock), mock.patch.object(
                ds.boards.time, 'monotonic', time.monotonic):
            released = threading.Event()

            def release():
                lock.release()
                released.set()

            timer = threading.Timer(0.4, release)
            timer.start()
            try:
                queue = ds.boards.WorkdayQueue(ds.boards.time.monotonic() + 0.05)
                fetch = mock.Mock(side_effect=AssertionError('The board was requested after the deadline'))

                def read(deadline):
                    return ds.boards.board_rows(self.company, fetch, run_deadline=deadline, queue=queue)

                [(index, rows)] = list(queue.read([(0, self.company['host'], read)]))
                self.assertFalse(released.is_set())
                self.assertEqual((index, rows.read, rows.attempted), (0, 'failed', False))
                fetch.assert_not_called()
            finally:
                timer.cancel()
                timer.join()
                if released.is_set():
                    lock.acquire()

    def test_resumed_zero_total_keeps_paging_until_a_short_or_empty_page(self):
        def fetch(method, url, body):
            offset = body['offset']
            self.calls.append((url, offset))
            return {'total': 0, 'jobPostings': self.listings[offset:offset + body['limit']]}

        for count in (41, 40):
            with self.subTest(count=count):
                self.listings = self.listings[:count]
                self.calls.clear()
                rows = ds.boards.board_rows(self.company, fetch, start_offset=20, page_pause=0)
                self.assertEqual((self.offsets(), rows.read, rows.rows_read), ([20, 40], 'complete', count - 20))
                self.assertFalse(rows.full_read)
                self.assertNotIn('of 0', rows.reason)

    def test_saved_country_filter_ignores_a_saved_worldwide_offset(self):
        first, _ = self.country_boards()
        self.profile['search']['companies'] = [self.company]
        self.run_search()
        with closing(sqlite3.connect(self.backlog)) as db, db:
            db.execute('UPDATE workday_progress SET next_offset=20')
        self.requests.clear()
        read = self.run_search(fx.TODAY + timedelta(days=1))['boards'][0]
        self.assertEqual(self.requests[0][:3], (first, {'Location_Country': ['home-id', 'other-id']}, 0))
        self.assertEqual((read['read'], read['rows']), ('complete', 1))

    def test_country_filtered_posting_keeps_place_text_and_passes_place_rule(self):
        first, _ = self.country_boards()
        self.profile['search']['companies'] = [self.company]
        self.filtered[first] = [fx.WorkdayHomeFirst.listing(0, 'EXL-Central')]
        detail_path = first.rsplit('/', 1)[0] + self.filtered[first][0]['externalPath']
        self.details[detail_path] = {'location': 'EXL-Central'}
        result = self.run_search()
        job = next(job for job in result['landing'] if job['posting_id'] == 'R0')
        self.assertEqual(job['place_text'], 'EXL-Central')
        self.assertEqual((job['place'], job['place_sentence']), ('ok', 'EXL-Central'))

    def test_nested_location_facet_can_supply_a_country_filter(self):
        first, _ = self.country_boards()
        self.profile['search']['companies'] = [self.company]
        self.facets[first] = [{'facetParameter': 'locationMainGroup', 'values': [
            self.facet('locations', (' ExampleLAND ', 'home-id'), ('New Exampleland', 'wrong-id'))]}]
        read = self.run_search()['boards'][0]
        self.assertEqual(self.requests[1][:3], (first, {'locations': ['home-id']}, 0))
        self.assertEqual((read['read'], read['rows'], read['full_read']), ('complete', 1, False))

    def test_worldwide_fallback_pages_after_other_boards_finish_their_home_read(self):
        first, second = self.country_boards()
        self.facets[first] = []
        self.listings = [dict(fx.WorkdayHomeFirst.listing(n, 'Exampleland'), title='Example Clerk')
                         for n in range(41)]
        self.run_search()
        self.assertEqual([request[:3] for request in self.requests[:5]], [
            (first, {}, 0), (second, {}, 0), (second, {'Country_and_Jurisdiction': ['second-id']}, 0),
            (first, {}, 20), (first, {}, 40)])

    def test_country_facet_with_no_whole_match_completes_with_zero_home_rows(self):
        first, _ = self.country_boards()
        self.profile['search']['companies'] = [self.company]
        self.facets[first] = [self.facet('locationCountry', ('New Exampleland', 'wrong-id')),
                              self.facet('Location', ('Exampleland', 'city-id'))]
        read = self.run_search()['boards'][0]
        self.assertEqual((read['read'], read['rows'], read['full_read']), ('complete', 0, False))
        self.assertEqual(read['reason'], 'No postings listed for Exampleland.')
        self.assertEqual([applied for _, applied, _, _ in self.requests], [{}, {}])

    def test_no_country_facet_uses_the_first_worldwide_page(self):
        first, _ = self.country_boards()
        self.profile['search']['companies'] = [self.company]
        self.facets[first] = [self.facet('Location', ('Exampleland', 'city-id'))]
        read = self.run_search()['boards'][0]
        self.assertEqual((read['read'], read['rows'], read['full_read']), ('complete', 1, True))
        self.assertEqual(len(self.requests), 1)

    def test_worldwide_failure_preserves_home_result(self):
        first, _ = self.country_boards()
        self.profile['search']['companies'] = [self.company]
        self.failures[first] = [(200, {}), (200, {}), (500, {})]
        result = self.run_search()
        read = result['boards'][0]
        self.assertEqual((read['read'], read['rows'], read['full_read']), ('complete', 1, False))
        self.assertEqual(read['worldwide']['read'], 'failed')
        self.assertEqual(result['source_errors'], [])

    def test_429_rotates_hosts_doubles_cooldown_and_success_resets_it(self):
        first, second = self.country_boards()
        self.failures[first] = [(429, {}), (200, {}), (200, {}), (429, {})]
        self.failures[second] = [(429, {}), (200, {}), (200, {})]
        self.run_search()
        self.assertEqual([(path, at) for path, _, _, at in self.requests], [
            (first, 0), (second, 30), (first, 90), (first, 90),
            (second, 90), (second, 90), (first, 90), (second, 120), (first, 120)])

    def test_retry_after_delays_that_host_without_delaying_the_other(self):
        first, second = self.country_boards()
        self.failures[first] = [(429, {'Retry-After': '50'})]
        self.run_search()
        self.assertEqual([(path, at) for path, _, _, at in self.requests[:4]], [
            (first, 0), (second, 0), (second, 0), (first, 50)])

    def test_cooldown_cannot_use_up_run_budget_or_start_worldwide_pass(self):
        first, second = self.country_boards()
        self.profile['search']['workday_run_budget_seconds'] = 25
        self.failures[first] = [(429, {})]
        result = self.run_search()
        self.assertEqual([path for path, _, _, _ in self.requests], [first])
        self.assertFalse(any('worldwide' in board for board in result['boards']))
        self.assertLess(self.elapsed, 25)
        self.requests.clear()
        self.run_search(fx.TODAY + timedelta(days=1))
        self.assertEqual(self.requests[0][0], second)


if __name__ == '__main__':
    unittest.main()
