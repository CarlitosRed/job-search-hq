"""Offline coverage for search credentials and their output boundaries."""
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import date
import importlib.util
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shadow = load('ai_shadow')
settings = shadow.search.settings
DAY = date(2026, 11, 25)
FAKE_KEY = 'fixture-search-secret-7XQ'
MOVE_KEY = 'Move this key to work/search-keys.json; keys in settings.json are ignored.'
MISSING_KEY = 'The API key, account ID or supported provider setting is missing.'


class SearchKeys(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.root = Path(folder)
        self.enterContext(patch.dict('os.environ', {'JOB_SEARCH_HQ_DATA': folder}))
        self.work = self.root / 'work'
        self.work.mkdir()
        self.queue = self.work / 'search-backlog.sqlite3'
        self.keys = self.work / 'search-keys.json'
        self.profile = load('test_search_profile').minimal()
        self.complete = Mock(return_value=dict(queries=[], employers=[], filters=[]))
        self.api = self.enterContext(patch.object(shadow.engines, 'api_request', side_effect=self.response))
        self.enterContext(patch.object(shadow.engines.time, 'sleep'))
        self.enterContext(patch.object(shadow.engines, 'page_request',
                                       side_effect=AssertionError('Unexpected page request')))
        self.options = {name: {'enabled': False} for name in shadow.engines.NAMES
                        if name not in (*shadow.engines.KEYED, 'remotive')}

    def response(self, method, url, body=None, *, headers=None, timeout):
        host = urlsplit(url).hostname
        if host == 'api.search.brave.com':
            return {'web': {'results': []}}
        if host == 'remotive.com':
            return {'jobs': []}
        if host == 'api.adzuna.com':
            return {'results': []}
        if host in ('jooble.org', 'search.api.careerjet.net'):
            return {'jobs': []}
        self.fail(f'Unexpected API host {host}')

    def write_keys(self, values):
        self.keys.write_text(json.dumps(values), encoding='utf-8')

    def search(self):
        (self.root / 'settings.json').write_text(
            json.dumps({'search_engines': self.options}), encoding='utf-8')
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            detail = shadow.run(self.profile, {}, DAY, 'fixture-run', self.queue, None,
                                self.work / 'ai-usage.sqlite3', self.complete,
                                settings=settings.load()['search_engines'])
        return output.getvalue() + detail

    def reasons(self):
        with closing(sqlite3.connect(self.queue)) as db:
            return dict(db.execute('SELECT source, reason FROM search_runs'))

    def calls_to(self, host):
        return [call for call in self.api.call_args_list if urlsplit(call.args[1]).hostname == host]

    def assert_keyless_ran(self):
        self.assertTrue(self.calls_to('remotive.com'))
        self.assertEqual(self.reasons()['remotive'], '')

    def test_keys_from_work_file_enable_all_keyed_engines(self):
        self.options.update(web_search={'provider': 'brave'}, adzuna={'country': 'gb'},
                            careerjet={'locale': 'en_GB'})
        self.write_keys({'web_search': {'provider': 'unsupported', 'key': FAKE_KEY, 'enabled': False},
                         'adzuna': {'id': 'fixture-account', 'key': FAKE_KEY, 'country': 'us'},
                         'jooble': {'key': FAKE_KEY},
                         'careerjet': {'key': FAKE_KEY, 'locale': 'en_US'}})
        self.search()
        reasons = self.reasons()
        prompted_engines = json.loads(self.complete.call_args.args[0].split('\n', 1)[1])['engines']
        for name in shadow.engines.KEYED:
            self.assertEqual(reasons[name], '')
            self.assertIn(name, prompted_engines)
        brave = self.calls_to('api.search.brave.com')[0]
        self.assertEqual(brave.kwargs['headers']['X-Subscription-Token'], FAKE_KEY)
        adzuna = self.calls_to('api.adzuna.com')[0].args[1]
        self.assertIn('/jobs/gb/', adzuna)
        self.assertIn('app_id=fixture-account', adzuna)
        self.assertIn('app_key=' + FAKE_KEY, adzuna)
        self.assertTrue(self.calls_to('jooble.org')[0].args[1].endswith('/' + FAKE_KEY))
        self.assertIn('locale_code=en_GB', self.calls_to('search.api.careerjet.net')[0].args[1])

    def test_settings_only_credential_is_ignored(self):
        self.options['web_search'] = {'key': FAKE_KEY}
        self.search()
        self.assertEqual(self.reasons()['web_search'], MOVE_KEY)
        self.assertFalse(self.calls_to('api.search.brave.com'))
        self.assert_keyless_ran()

    def test_work_file_credential_wins_over_settings(self):
        self.options['web_search'] = {'key': 'fixture-tracked-key'}
        self.write_keys({'web_search': {'key': FAKE_KEY}})
        self.search()
        self.assertEqual(self.reasons()['web_search'], '')
        calls = self.calls_to('api.search.brave.com')
        self.assertTrue(calls)
        self.assertTrue(all(call.kwargs['headers']['X-Subscription-Token'] == FAKE_KEY for call in calls))

    def test_missing_file_keeps_keyed_engines_off_and_keyless_engine_on(self):
        self.search()
        for name in shadow.engines.KEYED:
            self.assertEqual(self.reasons()[name], MISSING_KEY)
        self.assert_keyless_ran()

    def test_bad_json_is_refused_and_keyless_engine_runs(self):
        self.keys.write_text('{"web_search": {"key": "' + FAKE_KEY + '"}', encoding='utf-8')
        output = self.search()
        self.assertIn('work/search-keys.json', output)
        self.assertIn('valid JSON', output)
        self.assertNotIn(FAKE_KEY, output)
        self.assertFalse(self.calls_to('api.search.brave.com'))
        self.assert_keyless_ran()

    def test_non_string_key_rejects_the_file_and_keyless_engine_runs(self):
        self.write_keys({'web_search': {'key': FAKE_KEY}, 'jooble': {'key': [FAKE_KEY]}})
        output = self.search()
        self.assertIn('work/search-keys.json', output)
        self.assertIn('jooble', output)
        self.assertIn('string', output)
        self.assertNotIn(FAKE_KEY, output)
        self.assertFalse(self.calls_to('api.search.brave.com'))
        self.assertFalse(self.calls_to('jooble.org'))
        self.assert_keyless_ran()

    def test_credentials_stay_out_of_run_output_backlog_review_and_ai_prompt(self):
        self.write_keys({'jooble': {'key': FAKE_KEY}, 'adzuna': {'key': FAKE_KEY, 'id': 'fixture-account'}})
        safe_url = 'https://jobs.example.invalid/operations'

        def echo_or_fail(method, url, body=None, *, headers=None, timeout):
            host = urlsplit(url).hostname
            if host == 'jooble.org':
                return {'jobs': [{'title': 'Operations analyst', 'company': 'Fable Workshop',
                                  'link': 'https://jobs.example.invalid/?token=' + FAKE_KEY},
                                 {'title': 'Operations analyst', 'company': 'Fable Workshop', 'link': safe_url}]}
            if host == 'api.adzuna.com':
                raise shadow.engines.SiteBlocked(url)
            return self.response(method, url, body, headers=headers, timeout=timeout)

        self.api.side_effect = echo_or_fail
        output = self.search()
        self.assertTrue(self.calls_to('api.adzuna.com'))
        self.assertTrue(self.calls_to('jooble.org'))
        self.assert_keyless_ran()
        payload = shadow.review(self.queue, self.profile['id'], DAY, self.profile)
        self.assertIn(safe_url, [job['link'] for job in payload['filtered_out']])
        with closing(sqlite3.connect(self.queue)) as db:
            dump = '\n'.join(db.iterdump())
        for value in (FAKE_KEY, 'fixture-account'):
            self.assertNotIn(value, output)
            self.assertNotIn(value, dump)
            self.assertNotIn(value, json.dumps(payload))
            self.assertNotIn(value, self.complete.call_args.args[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
