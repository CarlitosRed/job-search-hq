"""Offline coverage for the owner's automatic-read settings."""
from datetime import date
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engines = load('search_engines')
settings = engines.search.settings
links = engines.links
DAY = date(2026, 11, 25)
AMAZON = 'https://www.amazon.jobs/jobs/1'
SMARTRECRUITERS = 'https://jobs.smartrecruiters.com/FableWorkshop/1'
DEFAULT_HOSTS = ['amazon.jobs', 'amazon.com', 'smartrecruiters.com']


class AutomaticReadHosts(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict('os.environ', {'JOB_SEARCH_HQ_DATA': folder.name})
        env.start()
        self.addCleanup(env.stop)

    def override(self, hosts):
        (self.root / 'settings.json').write_text(
            json.dumps({'no_automatic_read_hosts': hosts}), encoding='utf-8')

    def search(self):
        calls = []

        def api(method, url, *args, **kwargs):
            target = urlsplit(url)
            calls.append((target.hostname, target.path))
            if target.hostname == 'www.amazon.jobs':
                return {'jobs': []}
            if target.hostname == 'api.smartrecruiters.com':
                return {'content': [{'id': '1', 'name': 'Research Analyst'}]} if target.path.endswith('/postings') else {}
            return {'jobs': []}

        disabled = {name: {'enabled': False} for name in engines.NAMES if name != 'careers_page'}
        profile = load('test_search_profile').minimal()
        plan = dict(queries=[dict(engine='careers_page', words='Research Analyst', place='Fableland')],
                    employers=[dict(name='Amazon', url=AMAZON, why='Fixture'),
                               dict(name='Fable Workshop', url=SMARTRECRUITERS, why='Fixture')])
        page = Mock(return_value=(200, 'text/html', '<p>Example careers page</p>'))
        engines.find(profile, plan, DAY, self.root / 'queue.sqlite3', disabled,
                     api=api, page=page, board_fetch=api)
        return [call.args[0] for call in page.call_args_list], calls

    def test_default_blocks_links_careers_pages_and_public_board_requests(self):
        self.assertEqual(settings.load()['no_automatic_read_hosts'], DEFAULT_HOSTS)
        with patch.object(links, '_direct') as direct:
            self.assertEqual(links.fetch_status_text(AMAZON), (links.READ_DISALLOWED, ''))
            direct.assert_not_called()
        self.assertEqual(links._recheck().link_skip_reason(SMARTRECRUITERS), links.READ_DISALLOWED_REASON)
        pages, calls = self.search()
        self.assertEqual(pages, [])
        self.assertFalse({'www.amazon.jobs', 'api.smartrecruiters.com'} & {host for host, _ in calls})

    def test_empty_list_allows_fixture_careers_pages_and_public_board_requests(self):
        self.override([])
        self.assertEqual(settings.load()['no_automatic_read_hosts'], [])
        pages, calls = self.search()
        self.assertCountEqual(pages, [AMAZON, SMARTRECRUITERS])
        self.assertIn(('www.amazon.jobs', '/en/search.json'), calls)
        self.assertIn(('api.smartrecruiters.com', '/v1/companies/FableWorkshop/postings'), calls)
        self.assertIn(('api.smartrecruiters.com', '/v1/companies/FableWorkshop/postings/1'), calls)

    def test_custom_list_applies_to_posting_reads_and_recheck(self):
        self.override(['EXAMPLE.INVALID.'])
        link = 'https://careers.example.invalid/jobs/1'
        self.assertTrue(links.automatic_read_disallowed(link))
        self.assertFalse(links.automatic_read_disallowed('https://otherexample.invalid/jobs/1'))
        recheck = links._recheck()
        fetch = Mock(return_value=(404, ''))
        self.assertEqual(recheck.link_status(link, fetch), ('unclear', links.READ_DISALLOWED_REASON))
        fetch.assert_not_called()
        self.assertEqual(recheck.link_status(AMAZON, fetch)[0], 'gone')
        fetch.assert_called_once_with(AMAZON)
        with patch.object(links, '_direct', return_value=(404, '')) as direct:
            self.assertEqual(links.fetch_status_text(AMAZON), (404, ''))
            direct.assert_called_once()

    def test_invalid_values_are_refused_with_a_plain_message(self):
        for value in ('amazon.jobs', None, [3], [''], ['https://example.invalid'], ['*.example.invalid'],
                      ['example.invalid:443'], ['bad-.example.invalid']):
            with self.subTest(value=value):
                self.override(value)
                with self.assertRaisesRegex(ValueError, r'^no_automatic_read_hosts must be a list of host names\.$'):
                    settings.load()


if __name__ == '__main__':
    unittest.main(verbosity=2)
