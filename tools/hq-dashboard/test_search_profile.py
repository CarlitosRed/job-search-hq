"""Search profile format and loader (build step 2, piece 06-1a). All data is fictional."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
APP = HERE.parents[1]
spec = importlib.util.spec_from_file_location('hq_search_profile_test', HERE / 'search_profile.py')
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)

EXAMPLES = (APP / 'examples' / 'profile-example-operations.json', APP / 'examples' / 'profile-example-hr.json')


def minimal():
    return {
        'id': 'example-person', 'version': 1, 'revision': 1, 'updated': '2026-01-05',
        'goals': {'lanes': [{'id': 'ops', 'label': 'Operations', 'titles': ['operations analyst'], 'duties': ['process improvement']}],
                  'seniority': {'allowed': ['analyst'], 'stretch': ['senior']}, 'stretch_interests': []},
        'proven': {'skills': [{'name': 'Reporting', 'evidence': ['E-001'], 'status': 'stated'}]},
        'work_rules': {'home_country': 'Exampleland', 'authorized_countries': ['Exampleland'], 'remote_regions': ['Worldwide'],
                       'work_modes': ['remote'], 'relocation_wish': False, 'sponsorship_needed': True},
        'search': {'companies': [{'label': 'Example Co', 'provider': 'greenhouse', 'board': 'example', 'found_by': 'person'}],
                   'exclude_titles': [], 'exclude_companies': []},
        'review': {'reviewed_by': 'example-person', 'reviewed_on': '2026-01-05', 'open_questions': []},
    }


class ProfileLoader(unittest.TestCase):
    def write(self, data):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'profile.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def test_examples_load(self):
        for path in EXAMPLES:
            with self.subTest(path.name):
                profile = sp.load(path)
                self.assertGreaterEqual(len(profile['goals']['lanes']), 2)
                self.assertTrue(profile['search']['companies'])

    def test_minimal_loads_with_defaults(self):
        profile = sp.load(self.write(minimal()))
        search = profile['search']
        self.assertEqual((search['daily_land_limit'], search['judge_budget'], search['max_per_company'], search['wild_cards']), (30, 20, 3, 3))

    def assert_problem(self, data, words):
        with self.assertRaises(sp.ProfileError) as caught:
            sp.load(self.write(data))
        self.assertTrue(caught.exception.problems, 'problems must be listed')
        self.assertIn(words.lower(), ' '.join(caught.exception.problems).lower())

    def test_missing_and_wrong_fields_are_listed_plainly(self):
        no_lanes = minimal(); no_lanes['goals']['lanes'] = []
        self.assert_problem(no_lanes, 'lane')
        bad_provider = minimal(); bad_provider['search']['companies'][0]['provider'] = 'monster'
        self.assert_problem(bad_provider, 'provider')
        workday = minimal(); workday['search']['companies'][0].update(provider='workday')
        self.assert_problem(workday, 'host')
        bad_status = minimal(); bad_status['proven']['skills'][0]['status'] = 'unverified'
        self.assert_problem(bad_status, 'status')
        no_rules = minimal(); del no_rules['work_rules']
        self.assert_problem(no_rules, 'work_rules')

    def test_exclusions_need_a_reason(self):
        data = minimal(); data['search']['exclude_titles'] = [{'pattern': 'intern'}]
        self.assert_problem(data, 'reason')
        data = minimal(); data['search']['exclude_companies'] = [{'name': 'Example Co', 'reason': ''}]
        self.assert_problem(data, 'reason')

    def test_several_problems_reported_together(self):
        data = minimal(); data['goals']['lanes'] = []; data['search']['companies'][0]['provider'] = 'monster'
        with self.assertRaises(sp.ProfileError) as caught:
            sp.load(self.write(data))
        self.assertGreaterEqual(len(caught.exception.problems), 2)

    def test_not_json_is_a_plain_error(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'profile.json'
        path.write_text('{not json', encoding='utf-8')
        with self.assertRaises(sp.ProfileError):
            sp.load(path)

    def test_person_values_live_in_the_profile_not_the_code(self):
        """Places, companies and lane titles come from a profile; none of the examples' values is written into the code."""
        generic = {'remote', 'worldwide', 'anywhere', 'hybrid', 'onsite', 'on site'}
        values = set()
        for path in EXAMPLES:
            profile = json.loads(path.read_text(encoding='utf-8'))
            rules = profile['work_rules']
            values |= {rules['home_country'], *rules['authorized_countries'], *rules['remote_regions']}
            values |= {c['label'] for c in profile['search']['companies']}
            values |= {t for lane in profile['goals']['lanes'] for t in lane['titles']}
        values = {v for v in values if v and v.casefold() not in generic}
        self.assertTrue(values)
        for name in ('search_profile.py', 'daily_search.py'):
            text = (HERE / name).read_text(encoding='utf-8').casefold()
            for value in values:
                self.assertNotIn(value.casefold(), text, f'{name} must read "{value}" from a profile')

if __name__ == '__main__':
    unittest.main(verbosity=2)
