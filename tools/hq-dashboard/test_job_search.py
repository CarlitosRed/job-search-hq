# Fictional fixtures and settings-aware expectations.
"""Fictional offline job discovery checks."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('hq_job_search_test', HERE / 'job_search.py')
search = importlib.util.module_from_spec(spec)
spec.loader.exec_module(search)
SOURCES = (
    {'id': 'fictional-a', 'label': 'Example Company', 'provider': 'greenhouse', 'board': 'example'},
    {'id': 'fictional-b', 'label': 'Another Example', 'provider': 'lever', 'board': 'another-example'},
)


def fixture(source):
    if source['provider'] == 'greenhouse':
        return {'jobs': [
            {'title': 'AI Workflow Engineer', 'location': {'name': 'Remote - Latin America'}, 'absolute_url': 'https://example.com/jobs/one'},
            {'title': '<b>Operations</b> Analyst', 'location': {'name': 'London'}, 'absolute_url': 'https://example.com/jobs/two'},
            {'title': 'Assistant', 'location': {'name': 'Remote'}, 'absolute_url': 'https://example.com/jobs/three'},
            {'title': 'AI Unsafe', 'location': {'name': 'Remote'}, 'absolute_url': 'javascript:alert(1)'},
        ]}
    return [
        {'text': 'AI Support Engineer', 'categories': {'location': 'Spain'}, 'workplaceType': 'remote', 'hostedUrl': 'https://example.org/jobs/four'},
        {'text': 'Engineering Manager', 'categories': {'location': 'London'}, 'hostedUrl': 'https://example.org/jobs/five'},
    ]


class PostedDates(unittest.TestCase):
    """Step 02a: each result carries the job board's first-posted date and where it came from."""

    def test_greenhouse_first_published_never_updated_at(self):
        source = SOURCES[0]
        raw = {'title': 'AI Workflow Engineer', 'location': {'name': 'Remote'}, 'absolute_url': 'https://example.com/jobs/one',
               'first_published': '2026-10-20T23:30:00-04:00', 'updated_at': '2026-11-06T15:25:07-05:00'}
        result = search.normalize(source, raw, 'now')
        self.assertEqual((result['postedOn'], result['dateSource']), ('2026-10-20', 'greenhouse'))
        raw.pop('first_published')
        result = search.normalize(source, raw, 'now')
        self.assertEqual((result['postedOn'], result['dateSource']), ('', ''))
        raw['first_published'] = 'not a date'
        result = search.normalize(source, raw, 'now')
        self.assertEqual((result['postedOn'], result['dateSource']), ('', ''))

    def test_lever_created_at(self):
        source = SOURCES[1]
        raw = {'text': 'AI Support Engineer', 'categories': {'location': 'Spain'}, 'hostedUrl': 'https://example.org/jobs/four',
               'createdAt': 1762041600000}
        result = search.normalize(source, raw, 'now')
        self.assertEqual((result['postedOn'], result['dateSource']), ('2025-11-02', 'lever'))
        for bad in (None, 'yesterday', -5, True):
            raw['createdAt'] = bad
            result = search.normalize(source, raw, 'now')
            self.assertEqual((result['postedOn'], result['dateSource']), ('', ''), bad)

    def test_discover_results_carry_dates(self):
        result = search.discover({'query': 'AI operations', 'location': '', 'remote': False}, fixture, SOURCES)
        for item in result['results']:
            self.assertIn('postedOn', item)
            self.assertIn('dateSource', item)


class Discovery(unittest.TestCase):
    def query(self, **changes):
        return {'query': 'AI operations', 'location': '', 'remote': False, **changes}

    def test_real_source_shape_and_exact_short_keyword(self):
        result = search.discover(self.query(), fixture, SOURCES)
        self.assertEqual([r['role'] for r in result['results']], ['AI Workflow Engineer', 'Operations Analyst', 'AI Support Engineer'])
        self.assertEqual(result['sourcesChecked'], 2)
        self.assertEqual(result['sourceErrors'], [])
        self.assertTrue(all(r['checkedAt'] and r['source'] and r['summary'] for r in result['results']))
        self.assertEqual(len({r['id'] for r in result['results']}), 3)

    def test_location_and_remote_are_explicit_filters(self):
        result = search.discover(self.query(location='Latin America, Spain', remote=True), fixture, SOURCES)
        self.assertEqual([r['role'] for r in result['results']], ['AI Workflow Engineer', 'AI Support Engineer'])
        self.assertIn('Remote', result['results'][1]['location'])
        result = search.discover(self.query(location='Example Country'), fixture, SOURCES)
        self.assertEqual(result['results'], [], 'remote is not silently treated as country eligibility')

    def test_visible_partial_source_failure_and_progress(self):
        def failing(source):
            if source['provider'] == 'lever':
                raise RuntimeError('Fictional upstream private failure details')
            return fixture(source)
        progress = []
        result = search.discover(self.query(), failing, SOURCES, lambda done, total: progress.append((done, total)))
        self.assertEqual(result['sourcesChecked'], 1)
        self.assertEqual(len(result['sourceErrors']), 1)
        self.assertEqual(progress[-1], (2, 2))
        self.assertNotIn('private failure', json.dumps(result))

    def test_duplicate_links_and_result_limit_are_reported(self):
        def many(source):
            return {'jobs': [{'title': 'AI Engineer', 'location': {'name': 'Remote'}, 'absolute_url': 'https://example.com/jobs/' + str(i)} for i in range(55)] +
                            [{'title': 'AI Engineer duplicate', 'absolute_url': 'https://example.com/jobs/1'}]}
        result = search.discover(self.query(), many, SOURCES[:1])
        self.assertEqual(len(result['results']), 40)
        self.assertEqual(result['totalMatches'], 55)
        self.assertTrue(result['truncated'])

    def test_validation_rejects_unsupported_inputs(self):
        for payload in ({'query': ''}, {'query': 10}, {'query': 'or the jobs'}, {'query': 'AI\nEngineer'},
                        {'query': 'AI', 'location': []}, {'query': 'AI', 'remote': 'true'},
                        {'query': 'AI', 'url': 'http://127.0.0.1'}, {'query': 'AI', 'notes': 'quietly ignored'}):
            with self.assertRaises(ValueError, msg=str(payload)):
                search.validate(payload)
        with self.assertRaises(ValueError):
            search.source_url({'provider': 'greenhouse', 'board': '../private'})
        with self.assertRaises(ValueError):
            search.source_url({'provider': 'other', 'board': 'example'})

    def test_source_urls_are_fixed_https_get_endpoints(self):
        self.assertEqual(search.source_url(SOURCES[0]), 'https://boards-api.greenhouse.io/v1/boards/example/jobs')
        self.assertEqual(search.source_url(SOURCES[1]), 'https://api.lever.co/v0/postings/another-example?mode=json&limit=500')
        for url in ('javascript:alert(1)', 'http://example.com/job', 'https://user:password@example.com/job', 'https://example.com:8080/job', 'https://example.com/\njob'):
            self.assertEqual(search.safe_link(url), '')
        with self.assertRaises(ValueError):
            search.NoRedirect().redirect_request(None, None, 302, '', {}, 'http://127.0.0.1/private')


class Journal(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hq-fictional-search-')
        self.root = Path(self.temp.name)
        self.queue = search.Searches(self.root, fixture, SOURCES)

    def tearDown(self):
        self.queue.stop()
        self.temp.cleanup()

    def payload(self, **changes):
        return {'query': 'AI', 'location': '', 'remote': False, 'requestId': str(uuid.uuid4()), **changes}

    def test_idempotency_concurrency_and_no_workbook_changes(self):
        workbook = self.root / 'applications-tracker.xlsx'
        workbook.write_bytes(b'Fictional sentinel; never opened by discovery')
        before = workbook.read_bytes()
        payload = self.payload()
        key = self.queue.submit(payload)
        self.assertEqual(self.queue.submit(payload), key)
        with self.assertRaises(ValueError):
            self.queue.submit({**payload, 'query': 'engineer'})
        with self.assertRaises(ValueError):
            self.queue.submit(self.payload())
        self.queue.process_one()
        item = self.queue.state()['searches'][0]
        self.assertEqual((item['id'], item['status'], len(item['results'])), (key, 'completed', 2))
        self.assertEqual(workbook.read_bytes(), before)
        self.assertEqual([p.name for p in self.root.iterdir() if p.suffix != '.sqlite3'], [workbook.name])
        reloaded = search.Searches(self.root, fixture, SOURCES)
        self.assertEqual(reloaded.state()['searches'][0]['results'], item['results'])

    def test_single_active_search_under_parallel_submit(self):
        def submit(_):
            try:
                return self.queue.submit(self.payload())
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=6) as pool:
            result = list(pool.map(submit, range(6)))
        self.assertEqual(sum(item is not None for item in result), 1)

    def test_restart_marks_running_interrupted_and_never_replays(self):
        key = self.queue.submit(self.payload())
        with self.queue.connect() as db, db:
            db.execute("UPDATE searches SET status='running' WHERE id=?", (key,))
        restarted = search.Searches(self.root, lambda _: self.fail('Interrupted search must not rerun'), SOURCES)
        restarted.process_one()
        item = restarted.state()['searches'][0]
        self.assertEqual(item['status'], 'interrupted')
        self.assertTrue(item['finished'])

    def test_all_sources_fail_is_failed_not_empty_success(self):
        def fail(_):
            raise OSError('offline fixture')
        self.queue.fetcher = fail
        self.queue.submit(self.payload())
        self.queue.process_one()
        item = self.queue.state()['searches'][0]
        self.assertEqual(item['status'], 'failed')
        self.assertEqual(len(item['sourceErrors']), 2)

    def test_cli_new_json_snapshot_and_existing_files_preserved(self):
        output = self.root / 'fictional-snapshot.json'
        argv = ['job_search.py', '--query', 'AI', '--output', str(output)]
        snapshot = search.discover({'query': 'AI', 'location': '', 'remote': False}, fixture, SOURCES)
        with patch.object(sys, 'argv', argv), patch.object(search, 'discover', return_value=snapshot):
            self.assertEqual(search.main(), 0)
        self.assertEqual(len(json.loads(output.read_text())['results']), 2)
        before = output.read_bytes()
        with patch.object(sys, 'argv', argv), patch.object(search, 'discover') as call:
            with self.assertRaises(FileExistsError):
                search.main()
            call.assert_not_called()
        self.assertEqual(output.read_bytes(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
