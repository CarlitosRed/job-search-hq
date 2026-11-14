"""Daily search: four boards, candidates, ranking, place rules, backlog and lane slots (build step 2, 06-1a).

Every company, job and place here is fictional. The fetcher is a fake keyed by URL.
"""
from datetime import date, datetime, timedelta, timezone
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('hq_daily_search_test', HERE / 'daily_search.py')
ds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ds)

TODAY = date(2026, 3, 10)
ago = lambda n: (TODAY - timedelta(days=n)).isoformat()
ms = lambda n: int(datetime.combine(TODAY - timedelta(days=n), datetime.min.time(), timezone.utc).timestamp() * 1000)

COMPANIES = [
    {'label': 'Example Cloud', 'provider': 'greenhouse', 'board': 'excloud', 'found_by': 'person'},
    {'label': 'Sample Labs', 'provider': 'lever', 'board': 'samplelabs', 'found_by': 'person'},
    {'label': 'Demo AI', 'provider': 'ashby', 'board': 'demoai', 'found_by': 'person'},
    {'label': 'Big Example', 'provider': 'workday', 'board': 'bigex', 'host': 'bigex.wd1.myworkdayjobs.com', 'site': 'Careers', 'found_by': 'person'},
]


def ops_profile(**search):
    profile = {
        'id': 'example-ops', 'version': 1, 'revision': 3, 'updated': '2026-03-01',
        'goals': {'lanes': [
            {'id': 'partner', 'label': 'Partner operations', 'titles': ['partner operations', 'partner programs', 'partner enablement'],
             'duties': ['partner incentive', 'incentive reporting']},
            {'id': 'ai', 'label': 'AI operations', 'titles': ['ai operations'], 'duties': ['ai agents']},
            {'id': 'revops', 'label': 'Revenue operations', 'titles': ['revenue operations'], 'duties': ['dashboards']}],
            'seniority': {'allowed': ['analyst', 'specialist', 'associate'], 'stretch': ['senior', 'lead', 'manager']},
            'stretch_interests': []},
        'proven': {'skills': []},
        'work_rules': {'home_country': 'Exampleland', 'authorized_countries': ['Exampleland'], 'remote_regions': ['Latin America', 'Worldwide'],
                       'work_modes': ['remote', 'hybrid'], 'relocation_wish': True, 'sponsorship_needed': True},
        'search': {'companies': copy.deepcopy(COMPANIES), 'exclude_titles': [], 'exclude_companies': [],
                   'daily_land_limit': 30, 'judge_budget': 20, 'max_per_company': 10, 'wild_cards': 3},
        'review': {'reviewed_by': 'example-ops', 'reviewed_on': '2026-03-01', 'open_questions': []},
    }
    profile['search'].update(search)
    return profile


def greenhouse_jobs():
    return {'jobs': [
        {'id': 101, 'title': 'Partner Operations Analyst', 'location': {'name': 'Remote - Latin America'},
         'absolute_url': 'https://example.com/gh/101', 'first_published': ago(2) + 'T10:00:00-05:00',
         'content': '&lt;p&gt;Own <b>partner incentive</b> programs and incentive reporting.&lt;/p&gt;'},
        {'id': 102, 'title': 'Senior Software Engineer', 'location': {'name': 'Remote'},
         'absolute_url': 'https://example.com/gh/102', 'first_published': ago(1), 'content': 'Build backend services.'},
        {'id': 103, 'title': 'Ecosystem Specialist', 'location': {'name': 'Exampleland City, Exampleland'},
         'absolute_url': 'https://example.com/gh/103', 'first_published': ago(3), 'content': 'Run partner incentive plans for resellers.'},
        {'id': 104, 'title': 'Partner Programs Analyst', 'location': {'name': 'Farland City, Farland'},
         'absolute_url': 'https://example.com/gh/104', 'first_published': ago(4), 'content': 'We are unable to sponsor visas for this role.'},
        {'id': 105, 'title': 'Partner Operations Analyst', 'location': {'name': 'Farland City, Farland'},
         'absolute_url': 'https://example.com/gh/105', 'first_published': ago(5), 'content': 'Relocation package offered.'},
        {'id': 106, 'title': 'Partner Operations Specialist', 'location': {'name': 'Remote'},
         'absolute_url': 'https://example.com/gh/106', 'first_published': ago(60), 'content': 'Old posting.'},
    ]}


def lever_jobs():
    return [{'id': 'lv1', 'text': 'Revenue Operations Analyst', 'categories': {'location': 'Exampleland City'},
             'hostedUrl': 'https://jobs.lever.co/samplelabs/lv1', 'createdAt': ms(1), 'workplaceType': 'hybrid',
             'descriptionPlain': 'Build dashboards for sales leaders.'}]


def ashby_jobs():
    return {'jobs': [{'id': 'a1', 'title': 'AI Operations Specialist', 'location': 'Remote', 'isRemote': True,
                      'publishedAt': ago(1) + 'T08:00:00.000Z', 'jobUrl': 'https://jobs.ashbyhq.com/demoai/a1',
                      'descriptionPlain': 'Work with AI agents every day. We hire worldwide.'}]}


WORKDAY_LIST = [{'title': 'Partner Enablement Lead', 'externalPath': '/job/Remote/Partner-Enablement-Lead_R100',
                 'locationsText': 'Remote', 'postedOn': 'Posted 3 Days Ago', 'bulletFields': ['R100']}] + \
               [{'title': f'Warehouse Associate {n}', 'externalPath': f'/job/Site/Warehouse_R2{n:02d}', 'locationsText': 'Farland',
                 'postedOn': 'Posted Today', 'bulletFields': [f'R2{n:02d}']} for n in range(24)]


class FakeBoards:
    def __init__(self, broken=()):
        self.calls = []
        self.broken = set(broken)

    def __call__(self, method, url, body=None):
        self.calls.append((method, url, body))
        if any(b in url for b in self.broken):
            raise OSError('board down')
        if 'boards-api.greenhouse.io/v1/boards/excloud/jobs' in url:
            assert 'content=true' in url, 'Greenhouse must ask for descriptions'
            return greenhouse_jobs()
        if 'api.lever.co/v0/postings/samplelabs' in url:
            return lever_jobs()
        if 'api.ashbyhq.com/posting-api/job-board/demoai' in url:
            return ashby_jobs()
        if url == 'https://bigex.wd1.myworkdayjobs.com/wday/cxs/bigex/Careers/jobs':
            assert method == 'POST'
            data = json.loads(body) if isinstance(body, (str, bytes)) else body
            offset, limit = data.get('offset', 0), data.get('limit', 20)
            return {'total': len(WORKDAY_LIST), 'jobPostings': WORKDAY_LIST[offset:offset + limit]}
        if url.startswith('https://bigex.wd1.myworkdayjobs.com/wday/cxs/bigex/Careers/job/'):
            assert method == 'GET'
            path = url.split('/Careers', 1)[1]
            job = next(j for j in WORKDAY_LIST if j['externalPath'] == path)
            return {'jobPostingInfo': {'title': job['title'], 'jobDescription': '<p>Lead partner enablement and incentive reporting.</p>',
                                       'startDate': ago(3), 'location': job['locationsText'], 'jobReqId': job['bulletFields'][0],
                                       'externalUrl': 'https://bigex.wd1.myworkdayjobs.com/Careers' + path}}
        raise AssertionError('unexpected URL ' + url)


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        self.backlog = self.dir / 'search-backlog.sqlite3'

    def run_search(self, profile=None, fetch=None, today=TODAY, mark_landed=False):
        return ds.run(profile or ops_profile(), fetch or FakeBoards(), self.backlog, today, mark_landed=mark_landed)

    @staticmethod
    def by_title(out):
        return {j['title']: j for j in out['landing']}


class Boards(Base):
    def test_four_boards_keep_descriptions_dates_and_identity(self):
        out = self.run_search()
        jobs = self.by_title(out)
        gh = jobs['Partner Operations Analyst']
        self.assertIn('partner incentive', gh['description'].lower())
        self.assertNotIn('<', gh['description'])
        self.assertEqual(gh['posted_on'], ago(2))
        self.assertEqual((gh['provider'], gh['company'], gh['posting_id']), ('greenhouse', 'Example Cloud', '101'))
        self.assertEqual(jobs['Revenue Operations Analyst']['posted_on'], ago(1))
        ashby = jobs['AI Operations Specialist']
        self.assertIn('AI agents', ashby['description'])
        self.assertEqual(ashby['posted_on'], ago(1))
        self.assertEqual(ashby['link'], 'https://jobs.ashbyhq.com/demoai/a1')
        wd = jobs['Partner Enablement Lead']
        self.assertIn('partner enablement', wd['description'].lower())
        self.assertEqual(wd['provider'], 'workday')

    def test_workday_pages_and_fetches_descriptions_only_for_matches(self):
        fake = FakeBoards()
        self.run_search(fetch=fake)
        lists = [c for c in fake.calls if c[0] == 'POST']
        details = [c for c in fake.calls if c[0] == 'GET' and '/Careers/job/' in c[1]]
        self.assertGreaterEqual(len(lists), 2, 'Workday list must be paged')
        self.assertEqual(len(details), 1, 'only the matching Workday job gets a description call')

    def test_a_failing_board_is_reported_and_the_rest_continue(self):
        out = self.run_search(fetch=FakeBoards(broken=['api.lever.co']))
        self.assertEqual([e['company'] for e in out['source_errors']], ['Sample Labs'])
        self.assertIn('Partner Operations Analyst', self.by_title(out))


class Candidates(Base):
    def test_candidate_rule(self):
        jobs = self.by_title(self.run_search())
        self.assertIn('Ecosystem Specialist', jobs, 'an unusual title with a duty in the description gets in')
        self.assertEqual(jobs['Ecosystem Specialist']['lane'], 'partner')
        self.assertIn('partner incentive', jobs['Ecosystem Specialist']['matched_duties'])
        self.assertNotIn('Senior Software Engineer', jobs, 'no title or duty match stays out')
        self.assertNotIn('Warehouse Associate 0', jobs)

    def test_too_old_and_excluded_are_counted_not_landed(self):
        profile = ops_profile(exclude_companies=[{'name': 'Sample Labs', 'reason': 'Not interested'}])
        out = self.run_search(profile)
        ids = {j['posting_id'] for j in out['landing']}
        self.assertNotIn('106', ids)
        self.assertNotIn('lv1', ids)
        self.assertGreaterEqual(out['metadata']['counts']['too_old'], 1)
        self.assertGreaterEqual(out['metadata']['counts']['excluded'], 1)


class Ranking(Base):
    def test_title_match_beats_duty_only_and_out_ranks_last_but_stays(self):
        out = self.run_search()
        order = [j['posting_id'] for j in sorted(out['landing'], key=lambda j: -j['score'])]
        self.assertLess(order.index('101'), order.index('103'), 'title match ranks above duty-only')
        self.assertEqual(order[-1], '104', 'Out ranks last')
        self.assertIn('104', order, 'Out stays')

    def test_place_rules(self):
        jobs = {j['posting_id']: j for j in self.run_search()['landing']}
        self.assertEqual(jobs['101']['place'], 'ok')         # remote, one of the person's regions
        self.assertEqual(jobs['103']['place'], 'ok')         # home country
        self.assertEqual(jobs['104']['place'], 'out')        # plain "unable to sponsor", place not authorized
        self.assertIn('unable to sponsor', jobs['104']['place_sentence'].lower())
        self.assertEqual(jobs['105']['place'], 'check')      # relocation alone is not permission to work
        self.assertEqual(jobs['a1']['place'], 'ok')        # remote, hires worldwide
        for job in jobs.values():
            self.assertEqual(job['fit'], 'pending')

    def test_another_profession_uses_its_own_lanes(self):
        hr = ops_profile()
        hr['goals']['lanes'] = [{'id': 'hr', 'label': 'People operations', 'titles': ['hr generalist', 'people operations'], 'duties': ['onboarding']},
                                {'id': 'rec', 'label': 'Recruiting', 'titles': ['recruiter'], 'duties': ['interview scheduling']}]
        hr['search']['companies'] = [COMPANIES[0]]
        jobs = greenhouse_jobs()['jobs'] + [{'id': 201, 'title': 'HR Generalist', 'location': {'name': 'Exampleland City'},
                                             'absolute_url': 'https://example.com/gh/201', 'first_published': ago(1), 'content': 'Own onboarding.'}]
        fake = lambda method, url, body=None: {'jobs': jobs}
        out = ds.run(hr, fake, self.backlog, TODAY)
        titles = [j['title'] for j in sorted(out['landing'], key=lambda j: -j['score'])]
        self.assertEqual(titles[0], 'HR Generalist')
        self.assertNotIn('Partner Operations Analyst', titles, 'another person never gets this person\'s lanes')


class Slots(Base):
    def many(self):
        rows = [{'id': 300 + n, 'title': f'Partner Operations Analyst {n}', 'location': {'name': 'Remote - Worldwide'},
                 'absolute_url': f'https://example.com/gh/{300 + n}', 'first_published': ago(1), 'content': 'Partner incentive work.'} for n in range(10)]
        rows += [{'id': 400 + n, 'title': f'Revenue Operations Analyst {n}', 'location': {'name': 'Remote - Worldwide'},
                  'absolute_url': f'https://example.com/gh/{400 + n}', 'first_published': ago(1), 'content': 'Dashboards.'} for n in range(2)]
        return lambda method, url, body=None: {'jobs': rows}

    def profile(self, **search):
        p = ops_profile(**search)
        p['search']['companies'] = [COMPANIES[0], dict(COMPANIES[0], label='Second Cloud', board='second')]
        return p

    def test_lane_slots_wild_cards_and_company_cap(self):
        out = ds.run(self.profile(daily_land_limit=6, wild_cards=1, max_per_company=3, judge_budget=4), self.many(), self.backlog, TODAY)
        landing = out['landing']
        self.assertEqual(len(landing), 6)
        lanes = {j['lane'] for j in landing}
        self.assertIn('revops', lanes, 'a smaller lane still gets its slots')
        per_company = {}
        for j in landing:
            per_company[j['company']] = per_company.get(j['company'], 0) + 1
        self.assertLessEqual(max(per_company.values()), 3)
        self.assertEqual(sum(1 for j in landing if j['judge']), 4)

    def test_backlog_never_drops_and_lands_the_rest_later(self):
        fetch, profile = self.many(), self.profile(daily_land_limit=5, wild_cards=1, max_per_company=10)
        first = ds.run(profile, fetch, self.backlog, TODAY)
        again = ds.run(profile, fetch, self.backlog, TODAY)
        self.assertEqual([j['posting_id'] for j in first['landing']], [j['posting_id'] for j in again['landing']],
                         'without --mark-landed nothing is marked landed')
        seen, day = set(), TODAY
        for _ in range(10):
            out = ds.run(profile, fetch, self.backlog, day, mark_landed=True)
            ids = [(j['company'], j['posting_id']) for j in out['landing']]
            self.assertFalse(seen & set(ids), 'nothing lands twice')
            seen |= set(ids)
            day += timedelta(days=1)
            if not ids:
                break
        self.assertEqual(len(seen), 24, 'every candidate lands eventually (12 per company, 2 companies)')
        self.assertEqual(out['metadata']['counts']['backlog_waiting'], 0)

    def test_first_seen_is_kept(self):
        fetch, profile = self.many(), self.profile(daily_land_limit=2, wild_cards=0, max_per_company=10)
        ds.run(profile, fetch, self.backlog, TODAY, mark_landed=True)
        later = ds.run(profile, fetch, self.backlog, TODAY + timedelta(days=3), mark_landed=True)
        for job in later['landing']:
            self.assertEqual(job['first_seen'], TODAY.isoformat())


    def test_only_jobs_seen_today_land(self):
        """A posting gone from its board (filled or closed) never lands later; a failed board's jobs wait."""
        profile = self.profile(daily_land_limit=2, wild_cards=0, max_per_company=10)
        profile['search']['companies'] = [COMPANIES[0]]
        rows = [{'id': 500 + n, 'title': f'Partner Operations Analyst {n}', 'location': {'name': 'Remote - Worldwide'},
                 'absolute_url': f'https://example.com/gh/{500 + n}', 'first_published': ago(1), 'content': 'Partner incentive work.'} for n in range(4)]
        ds.run(profile, lambda m, u, b=None: {'jobs': rows}, self.backlog, TODAY, mark_landed=True)
        gone = lambda m, u, b=None: {'jobs': rows[:2]}            # rows 502 and 503 left the board
        out = ds.run(profile, gone, self.backlog, TODAY + timedelta(days=1), mark_landed=True)
        self.assertEqual(out['landing'], [], 'jobs that left the board must not land')
        def down(m, u, b=None):
            raise OSError('board down')
        out = ds.run(profile, down, self.backlog, TODAY + timedelta(days=2), mark_landed=True)
        self.assertEqual(out['landing'], [])
        self.assertEqual(len(out['source_errors']), 1)

    def test_large_boards_fit_the_size_limit(self):
        """Live check 2026-11-13: a real Ashby board with descriptions was over 4 MB. Board reads allow at least 32 MB."""
        self.assertGreaterEqual(ds.boards.MAX_BOARD_BYTES, 32 * 1024 * 1024)

class Output(Base):
    def test_metadata(self):
        out = self.run_search()
        meta = out['metadata']
        self.assertEqual(meta['date'], TODAY.isoformat())
        self.assertEqual((meta['profile_id'], meta['profile_revision']), ('example-ops', 3))
        for key in ('fetched', 'candidates', 'excluded', 'too_old', 'backlog_waiting', 'landing'):
            self.assertIn(key, meta['counts'])

    def test_cli_never_overwrites_output(self):
        profile = self.dir / 'profile.json'
        profile.write_text(json.dumps(ops_profile()), encoding='utf-8')
        output = self.dir / 'today.json'
        output.write_text('keep', encoding='utf-8')
        r = subprocess.run([sys.executable, str(HERE / 'daily_search.py'), '--profile', str(profile), '--output', str(output),
                            '--backlog', str(self.backlog)], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(output.read_text(encoding='utf-8'), 'keep')


if __name__ == '__main__':
    unittest.main(verbosity=2)
