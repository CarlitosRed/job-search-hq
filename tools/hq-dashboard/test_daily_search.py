"""Daily search: four boards, candidates, ranking, place rules, backlog and lane slots (build step 2, 06-1a).

Every company, job and place here is fictional. The fetcher is a fake keyed by URL.
"""
from datetime import date, datetime, timedelta, timezone
import copy
from email.message import Message
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPSHandler, build_opener
from urllib.response import addinfourl

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
        {'id': 105, 'title': 'Partner Operations Associate', 'location': {'name': 'Farland City, Farland'},
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


def gh_row(n, title, location, content='Partner operations work.', days=1):
    return {'id': n, 'title': title, 'location': {'name': location}, 'absolute_url': f'https://example.com/gh/{n}',
            'first_published': ago(days), 'content': content}


class LandingRules(Base):
    """Ranking and landing rules decided after the 06-1b trial (spec 06, 2026-11-14)."""

    def search(self, rows, today=TODAY, mark_landed=False, **search):
        profile = ops_profile(**{'max_per_company': 10, 'wild_cards': 0, **search})
        profile['search']['companies'] = [COMPANIES[0]]
        return ds.run(profile, lambda method, url, body=None: {'jobs': rows}, self.backlog, today, mark_landed=mark_landed)

    def test_a_job_posted_several_times_lands_once_a_day(self):
        rows = [gh_row(501, 'Partner Operations Analyst', 'Remote - Worldwide'),
                gh_row(502, 'partner operations analyst!', 'Remote - Latin America'),
                gh_row(503, 'Partner Operations Analyst', 'Exampleland City, Exampleland'),
                gh_row(504, 'Partner Programs Analyst', 'Remote - Worldwide')]
        first = self.search(rows, daily_land_limit=10, mark_landed=True)
        titles = [j['title'].casefold().rstrip('!') for j in first['landing']]
        self.assertEqual(titles.count('partner operations analyst'), 1, 'same company and title land once a day')
        self.assertIn('partner programs analyst', titles)
        self.assertEqual(first['metadata']['counts']['candidates'], 4, 'every copy stays in the backlog')
        second = self.search(rows, today=TODAY + timedelta(days=1), daily_land_limit=10)
        self.assertEqual(len(second['landing']), 1, 'the next copy waits for another day')

    def test_on_site_abroad_without_help_ranks_six_lower(self):
        rows = [gh_row(511, 'Partner Operations Analyst North', 'Farland City, Farland', 'Partner operations work.'),
                gh_row(512, 'Partner Operations Analyst South', 'Farland City, Farland', 'Partner operations work. Relocation package offered.'),
                gh_row(513, 'Partner Operations Analyst East', 'Farland City, Farland', 'Partner operations work. We provide visa sponsorship.')]
        jobs = {j['posting_id']: j for j in self.search(rows, daily_land_limit=10)['landing']}
        self.assertEqual(jobs['512']['score'] - jobs['511']['score'], 6)
        self.assertEqual(jobs['513']['score'], jobs['512']['score'], 'visa help counts like relocation')
        self.assertEqual(jobs['511']['place'], 'check', 'ranked lower, never Out and never dropped')

    def test_remote_limited_to_another_place_is_check(self):
        rows = [gh_row(521, 'Partner Operations Analyst A', 'Remote - Farland', 'Partner operations work. Teams across Latin America.'),
                gh_row(522, 'Partner Operations Analyst B', 'Remote - Latin America'),
                gh_row(523, 'Partner Operations Analyst C', 'Remote', 'Partner operations work. We hire worldwide.'),
                gh_row(524, 'Partner Operations Analyst D', 'Farland, Remote', 'Partner operations work. Open to Latin America.')]
        jobs = {j['posting_id']: j for j in self.search(rows, daily_land_limit=10)['landing']}
        self.assertEqual(jobs['521']['place'], 'check', 'a region named only in the description does not make it OK')
        self.assertEqual(jobs['524']['place'], 'check')
        self.assertEqual(jobs['522']['place'], 'ok')
        self.assertEqual(jobs['523']['place'], 'ok', 'plain remote still reads the description')

    def test_reachable_jobs_land_first(self):
        strong = 'Own partner incentive plans and incentive reporting.'
        rows = [gh_row(531, 'Partner Operations Analyst One', 'Remote - Farland', strong),
                gh_row(532, 'Partner Operations Analyst Two', 'Remote - Farland', strong),
                gh_row(533, 'Partner Operations Analyst Three', 'Exampleland City, Exampleland'),
                gh_row(534, 'Partner Operations Analyst Four', 'Farland City, Farland', 'Partner operations work. Relocation package offered.')]
        out = self.search(rows, daily_land_limit=2)
        scores = {j['posting_id']: j['score'] for j in out['landing']}
        self.assertEqual(set(scores), {'533', '534'}, 'reachable jobs fill the places before higher-scored Check jobs')

    def test_check_jobs_fill_only_the_places_left_over(self):
        strong = 'Own partner incentive plans and incentive reporting.'
        rows = [gh_row(541, 'Partner Operations Analyst One', 'Remote - Farland', strong),
                gh_row(542, 'Partner Operations Analyst Two', 'Remote - Farland', strong, days=2),
                gh_row(543, 'Partner Operations Analyst Three', 'Exampleland City, Exampleland')]
        landing = self.search(rows, daily_land_limit=2)['landing']
        self.assertEqual({j['posting_id'] for j in landing}, {'543', '541'}, 'the one reachable job, then the best Check job')


class WorkdayHomeFirst(Base):
    """Workday: description reads go to home-country jobs first; the place comes from the job's own page."""
    SOURCE = {'label': 'Big Example', 'provider': 'workday', 'board': 'bigex', 'host': 'bigex.wd1.myworkdayjobs.com', 'site': 'Careers'}

    @staticmethod
    def listing(n, place):
        return {'title': f'Partner Operations Analyst {n}', 'externalPath': f'/job/Far-City/Partner-Ops_R{n}',
                'locationsText': place, 'postedOn': 'Posted 2 Days Ago', 'bulletFields': [f'R{n}']}

    def fake(self, listings, details):
        calls = []
        def fetch(method, url, body=None):
            calls.append((method, url))
            if url.endswith('/Careers/jobs'):
                return {'total': len(listings), 'jobPostings': listings[body['offset']:body['offset'] + body['limit']]}
            path = url.split('/Careers', 1)[1]
            job = next(j for j in listings if j['externalPath'] == path)
            info = {'title': job['title'], 'jobDescription': 'Read in full: ' + path, 'startDate': ago(2), 'jobReqId': job['bulletFields'][0],
                    'location': job['locationsText']}
            info.update(details.get(path, {}))
            return {'jobPostingInfo': info}
        return fetch, calls

    def test_description_reads_go_to_home_country_jobs_first(self):
        listings = [self.listing(n, 'Far City, Otherland') for n in range(45)]
        listings[43] = self.listing(43, '2 Locations')
        listings[44] = self.listing(44, 'Exampleland City, Exampleland')
        fetch, calls = self.fake(listings, {listings[43]['externalPath']: {'location': 'Exampleland City, Exampleland'}})
        home = lambda text: ds.boards.phrase_matches('Exampleland', text)
        rows = ds.boards.board_rows(self.SOURCE, fetch, lambda title, text: True, TODAY, home_first=home)
        reads = [url for method, url in calls if method == 'GET']
        self.assertEqual(len(reads), 40, 'still at most 40 description reads per board')
        self.assertTrue(any(url.endswith(listings[44]['externalPath']) for url in reads), 'the home-country job is read first, not dropped')
        self.assertTrue(any(url.endswith(listings[43]['externalPath']) for url in reads), 'a job hiding its places ("2 Locations") is read next')
        self.assertEqual([r['title'] for r in rows], [l['title'] for l in listings], 'rows keep the board order')
        self.assertIn('Read in full', rows[44]['description'])
        place = ds.boards.normalize(self.SOURCE, rows[43], TODAY.isoformat())['location']
        self.assertIn('Exampleland', place, 'the place comes from the job page, not "2 Locations"')

    def test_place_comes_from_the_job_page(self):
        listings = [self.listing(1, '3 Locations'), self.listing(2, 'Far City, Otherland')]
        fetch, calls = self.fake(listings, {
            listings[0]['externalPath']: {'location': 'Far City, Otherland', 'additionalLocations': ['Exampleland City, Exampleland', 'Third Town, Otherland'],
                                          'remoteType': 'Remote'}})
        matches = lambda title, text: title.endswith(' 1')
        rows = ds.boards.board_rows(self.SOURCE, fetch, matches, TODAY)
        first = ds.boards.normalize(self.SOURCE, rows[0], TODAY.isoformat())
        for part in ('Far City, Otherland', 'Exampleland City, Exampleland', 'Third Town, Otherland'):
            self.assertIn(part, first['location'])
        self.assertNotIn('3 Locations', first['location'])
        self.assertTrue(first['remote'], 'a Remote job page marks the job remote')
        second = ds.boards.normalize(self.SOURCE, rows[1], TODAY.isoformat())
        self.assertEqual(second['location'], 'Far City, Otherland', 'jobs without a page read keep the list text')

    def test_daily_search_asks_for_the_home_country_first(self):
        from unittest import mock
        seen = []
        real = ds.boards.board_rows
        def spy(*args, **kwargs):
            seen.append(kwargs.get('home_first'))
            return real(*args, **kwargs)
        with mock.patch.object(ds.boards, 'board_rows', spy):
            self.run_search()
        self.assertTrue(seen and all(callable(h) for h in seen), 'every board read gets the home-country check')
        self.assertTrue(seen[0]('Exampleland City, Exampleland'))
        self.assertFalse(seen[0]('Far City, Otherland'))


class FakeWorkable:
    origin = 'https://jobs.workable.com'

    def __init__(self, rows=None, *, ignored=False, error=None, query_rows=None):
        self.rows = rows or [dict()]
        self.ignored = ignored
        self.error = error
        self.query_rows = query_rows or {}
        self.calls = []

    @staticmethod
    def uid(index):
        return f'00000000-0000-4000-8000-{index:012d}'

    def __call__(self, method, url, *, deadline, boards=None):
        self.calls.append(url)
        assert method == 'GET' and deadline > 0
        parsed = urlsplit(url)
        assert parsed.hostname == 'jobs.workable.com'
        if parsed.path == '/jobs.md':
            query = parse_qs(parsed.query)
            assert query['day_range'] == ['14']
            if self.error is not None:
                status, headers = self.error
                if status != 403:
                    self.error = None
                raise HTTPError(url, status, '', headers, None)
            scoped = 'location' in query and not self.ignored
            phrase = query.get('query', [None])[0]
            indices = self.query_rows.get(phrase, range(len(self.rows)))
            total = len(indices) if scoped else 1000
            total_line = (f'Found **{total:,}** matching jobs (showing first 30).' if total > 30
                          else f'Found **{total:,}** matching job(s).')
            lines = [total_line, '',
                     '| Title | Company | Location | Workplace | Type | Link |',
                     '| --- | --- | --- | --- | --- | --- |']
            for index in indices[:30]:
                row = self.rows[index]
                title = row.get('title', f'Example Analyst {index}')
                company = row.get('company', 'Example Cloud')
                lines.append(f'| {title} | {company} | Remote | Remote | Full-time | '
                             f'[View]({self.origin}/jobs/{self.uid(index)}.md) |')
            return '\n'.join(lines)
        part, token = parsed.path.strip('/').split('/')
        assert part == 'view'
        index = int(token[-12:])
        row = self.rows[index]
        if 'status' in row:
            raise HTTPError(url, row['status'], '', {}, None)
        canonical = f'{self.origin}/view/sample-code/fictional-posting'
        ld = {'@type': 'JobPosting', 'url': canonical, 'id': token, 'identifier': {'value': token},
              'datePosted': row.get('date', ago(1)), 'title': row.get('title', f'Example Analyst {index}'),
              'hiringOrganization': {'name': row.get('company', 'Example Cloud')},
              'jobLocationType': 'TELECOMMUTE', 'description': '<p>Write fictional reports.</p>'}
        countries = [{'@type': 'Country', 'name': name} for name in row.get('countries', ['Exampleland'])]
        if countries:
            ld['applicantLocationRequirements'] = countries[0] if len(countries) == 1 else countries
        ld.update(row.get('ld', {}))
        if row.get('extra_posting'):
            ld = [ld, {'@type': 'JobPosting', 'id': self.uid(999)}]
        return ('<script>window.posting = {"state":"published"}</script>'
                f'<script type="application/ld+json">{json.dumps(ld)}</script>')


class WorkableTransport(unittest.TestCase):
    def test_only_one_same_host_https_view_redirect_can_be_followed(self):
        start = f'{FakeWorkable.origin}/view/{FakeWorkable.uid(0)}'
        final = f'{FakeWorkable.origin}/view/sample-code/fictional-posting'
        cases = [
            ('/view/sample-code/fictional-posting', 200, [start, final]),
            ('/view/sample-code/fictional-engineer---remote-(region)-at-e.x.a.m.p.l.e.', 200,
             [start, f'{FakeWorkable.origin}/view/sample-code/fictional-engineer---remote-(region)-at-e.x.a.m.p.l.e.']),
            ('/view/sample-code/fictional-reports-%26-data-analyst', 200,
             [start, f'{FakeWorkable.origin}/view/sample-code/fictional-reports-%26-data-analyst']),
            (final, 301, [start, final]),
            (final, 204, [start, final]),
            ('https://example.com/view/sample-code/fictional-posting', 200, [start]),
            ('http://jobs.workable.com/view/sample-code/fictional-posting', 200, [start]),
            ('/jobs.md', 200, [start]),
            ('/view/%2e%2e/jobs.md', 200, [start]),
            ('/view/sample-code', 200, [start]),
            ('/view/sample-code/', 200, [start]),
            ('/view/sample-code/fictional/posting', 200, [start]),
            ('/view/sample-code/unused/../fictional-posting', 200, [start]),
            ('/view/sample-code/%2E', 200, [start]),
            ('/view/sample-code/.%2e', 200, [start]),
            ('/view/sample-code/fictional%2fposting', 200, [start]),
            ('/view/sample-code/fictional%5Cposting', 200, [start]),
            ('/view/sample-code/fictional\\posting', 200, [start]),
            ('/view/sample-code/fictional%00posting', 200, [start]),
            ('/view/sample-code/fictional%7Fposting', 200, [start]),
            ('/view/sample-code/fictional\t-posting', 200, [start]),
        ]
        for target, final_status, expected in cases:
            with self.subTest(target=target, final_status=final_status):
                calls, accepts = [], []

                class CannedHTTPS(HTTPSHandler):
                    def https_open(self, req):
                        calls.append(req.full_url)
                        accepts.append(req.get_header('Accept'))
                        initial = req.full_url == start
                        status = 301 if initial else final_status
                        headers = Message()
                        if status == 301:
                            headers['Location'] = target if initial else '/view/sample-code/second-fictional-posting'
                        response = addinfourl(BytesIO(b'Fictional page'), headers, req.full_url, status)
                        response.msg = 'Fictional response'
                        return response

                with mock.patch.object(ds.boards, 'build_opener',
                                       side_effect=lambda *handlers: build_opener(*handlers, CannedHTTPS())):
                    if len(expected) == 2 and final_status == 200:
                        self.assertEqual(ds.workable.fetch_text('GET', start, deadline=None, boards=ds.boards),
                                         'Fictional page')
                    else:
                        with self.assertRaises((ds.boards.BoardReadError, HTTPError)):
                            ds.workable.fetch_text('GET', start, deadline=None, boards=ds.boards)
                self.assertEqual(calls, expected)
                self.assertEqual(accepts, ['text/html'] * len(expected))


class SearchSource(Base):
    def profile(self, *phrases, **search):
        profile = ops_profile(companies=search.pop('companies', []),
                              search_sources=['workable_search'], **search)
        lane = profile['goals']['lanes'][0]
        lane['titles'], lane['duties'] = list(phrases[:1]) or ['Example analyst'], list(phrases[1:])
        profile['goals']['lanes'] = [lane]
        return profile

    def search(self, fake, profile=None, today=TODAY, **options):
        clock = [1.0]
        self.delays = []

        def sleep(seconds):
            self.delays.append(seconds)
            clock[0] += seconds

        with mock.patch.object(ds.workable.time, 'monotonic', side_effect=lambda: clock[0]), \
             mock.patch.object(ds.workable.time, 'sleep', side_effect=sleep):
            return ds.run(profile or self.profile(), FakeBoards(), self.backlog, today,
                          mark_landed=True, search_fetch=fake, **options)

    def saved(self, profile='example-ops'):
        with ds.connect(self.backlog) as db:
            return [(json.loads(body), landed) for body, landed in db.execute(
                'SELECT body, landed FROM search_backlog WHERE profile_id=? ORDER BY rowid', (profile,))]

    def queries(self):
        with ds.connect(self.backlog) as db:
            return db.execute('SELECT profile_id, query, total, kept, reason FROM search_runs ORDER BY rowid').fetchall()

    def test_ignored_location_is_refused_before_details(self):
        fake = FakeWorkable(ignored=True)
        out = self.search(fake)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(self.saved(), [])
        self.assertEqual(self.queries(), [])
        self.assertIn('location filter could not be confirmed', out['boards'][0]['reason'])
        self.assertEqual(out['boards'][0]['read'], 'failed')

    def test_caps_distinct_phrases_request_spacing_and_retry_after(self):
        fake = FakeWorkable([{} for _ in range(45)], error=(429, {'Retry-After': '4'}),
                            query_rows={'Example analyst': range(30), 'Fictional duty 0': range(30, 45)})
        self.search(fake, self.profile('Example analyst', 'EXAMPLE ANALYST', *[f'Fictional duty {i}' for i in range(40)]))
        listing = [url for url in fake.calls if '/jobs.md?' in url]
        self.assertEqual(len(listing), 25)
        details = fake.calls[len(listing):]
        self.assertEqual(details, [f'{fake.origin}/view/{fake.uid(index)}' for index in range(40)])
        self.assertIn(4, self.delays)
        self.assertEqual(len(self.delays), len(fake.calls) - 1)
        self.assertTrue(all(delay >= 1.5 for delay in self.delays))
        queries = [row[1].casefold() for row in self.queries()]
        self.assertEqual(len(queries), len(set(queries)))
        self.assertTrue(any(query.startswith('fictional duty') for query in queries))

    def test_location_guard_once_and_queries_and_details_follow_title_first_lane_order(self):
        titles = ['Example first title', 'Sample first title', 'Example second title']
        duties = ['Example first duty', 'Sample first duty', 'Example second duty']
        order = titles + duties
        fake = FakeWorkable([{} for _ in order],
                            query_rows={phrase: [index] for index, phrase in enumerate(order)})
        profile = self.profile()
        profile['goals']['lanes'] = [
            dict(id='example', titles=titles[::2], duties=duties[::2]),
            dict(id='sample', titles=titles[1::2], duties=duties[1::2])]
        self.search(fake, profile)
        self.assertEqual([parse_qs(urlsplit(url).query) for url in fake.calls[:8]], [
            {'day_range': ['14']}, {'day_range': ['14'], 'location': ['Exampleland']},
            *[{'day_range': ['14'], 'location': ['Exampleland'], 'query': [phrase]} for phrase in order]])
        self.assertEqual(fake.calls[8:], [f'{fake.origin}/view/{fake.uid(index)}' for index in range(6)])

    def test_view_status_and_exactly_one_uuid_matched_job_posting(self):
        other = FakeWorkable.uid(999)
        fake = FakeWorkable([{}, {'ld': {'validThrough': ago(1)}}, {'status': 404}, {'status': 410},
                             {'status': 502}, {'ld': {'id': other, 'identifier': {'value': other}}},
                             {'ld': {'identifier': {'value': other}}}, {'extra_posting': True},
                             {'ld': {'validThrough': 'unreadable'}}])
        self.search(fake)
        self.assertEqual([job['open'] for job, _ in self.saved()],
                         [True, False, False, False, None, None, None, None, None])

    def test_view_supplies_text_sections_and_on_site_address(self):
        fake = FakeWorkable([{'ld': {
            'title': 'Fictional Detail Analyst', 'hiringOrganization': {'name': 'Fictional Detail Studio'},
            'jobLocationType': None, 'applicantLocationRequirements': None,
            'jobLocation': {'address': {'addressLocality': 'Sample Borough', 'addressRegion': 'Sample District',
                                        'addressCountry': 'Exampleland'}},
            'description': '<p>Write &amp; review.</p>',
            'requirementsSection': '<ul><li>Check fictional records.</li></ul>',
            'benefitsSection': '<p>Fictional training.</p>'}},
            {'countries': ['Farland', 'Exampleland']}])
        self.search(fake)
        onsite, remote = [job for job, _ in self.saved()]
        self.assertEqual(onsite['title'], 'Fictional Detail Analyst')
        self.assertEqual(onsite['company'], 'Fictional Detail Studio')
        self.assertFalse(onsite['remote'])
        self.assertEqual(onsite['place_text'], 'Sample Borough, Sample District, Exampleland')
        self.assertEqual(onsite['description'].splitlines(),
                         ['Write & review.', 'Check fictional records.', 'Fictional training.'])
        self.assertTrue(remote['remote'])
        self.assertEqual(remote['applicant_countries'], ['Farland', 'Exampleland'])
        self.assertEqual(remote['place_text'], 'Farland, Exampleland')
        self.assertEqual(fake.calls[3:], [f'{fake.origin}/view/{fake.uid(index)}' for index in range(2)])

    def test_facts_require_open_date_and_place_and_never_land(self):
        fake = FakeWorkable([{'date': None}, {'date': ago(14)}, {'date': ago(15)},
                             {'countries': []}, {'ld': {'validThrough': ago(1)}}, {'date': ago(1) + ' junk'},
                             {'countries': ['Farland']}, {'date': ago(-1)}])
        out = self.search(fake)
        self.assertEqual(out['landing'], [])
        rows = self.saved()
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(landed == 0 for _, landed in rows))
        reasons = [job['fact_reason'].lower() for job, _ in rows]
        self.assertEqual(reasons[1], 'kept for review')
        for index, word in [(0, 'date'), (2, '14 days'), (3, 'place'), (4, 'open'),
                            (5, 'date'), (6, 'place'), (7, 'date')]:
            self.assertIn(word, reasons[index])
        self.assertEqual(self.queries()[0][2:4], (8, 1))
        self.assertEqual(rows[1][0]['description'], 'Write fictional reports.')

    def test_employer_copy_wins_and_board_still_uses_45_days(self):
        fake = FakeWorkable([{'title': 'Partner Operations Analyst', 'company': 'example cloud'},
                             {'title': 'Fictional Intern'}])
        profile = self.profile('Partner operations', 'incentive reporting', companies=COMPANIES[:1],
                               exclude_titles=[{'pattern': 'Intern', 'reason': 'Excluded title.'}])
        out = self.search(fake, profile)
        self.assertFalse(any('/view/' in url for url in fake.calls))
        copies = [(job, landed) for job, landed in self.saved() if job['title'] == 'Partner Operations Analyst']
        self.assertEqual(len(copies), 2)
        self.assertEqual([job['provider'] for job in out['landing']].count('workable_search'), 0)
        self.assertIn('employer', next(job['fact_reason'] for job, _ in copies if job['provider'] == 'workable_search').lower())
        board = {'provider': 'greenhouse', 'title': 'Example Analyst', 'company': 'Example Cloud',
                 'posted_on': ago(30), 'first_seen': ago(1), 'date_source': 'greenhouse',
                 'open': True, 'place_text': 'Exampleland', 'remote': False, 'description': ''}
        self.assertFalse(ds.too_old(board, TODAY))
        self.assertIn('14 days', ds.posting_facts(profile, board, TODAY))

    def test_workable_keeps_the_same_company_board_backlog(self):
        profile = self.profile('Partner operations', 'incentive reporting', companies=COMPANIES[:1])
        baseline = copy.deepcopy(profile)
        baseline['id'] = 'example-baseline'
        baseline['search'].pop('search_sources')
        self.run_search(baseline, mark_landed=True)
        expected = self.saved(baseline['id'])
        self.assertIn('101', {job['posting_id'] for job, _ in expected})
        self.assertNotIn('102', {job['posting_id'] for job, _ in expected})

        self.search(FakeWorkable(), profile)
        self.assertEqual([(job, landed) for job, landed in self.saved() if job['provider'] == 'greenhouse'],
                         expected)
        self.assertTrue(any(job['provider'] == 'workable_search' for job, _ in self.saved()))

    def test_403_stops_only_workable_and_old_backlog_upgrades(self):
        profile = self.profile('Partner operations', companies=COMPANIES[:1])
        old = copy.deepcopy(profile)
        old['search'].pop('search_sources')
        ds.run(old, FakeBoards(), self.backlog, TODAY)
        with ds.connect(self.backlog) as db, db:
            db.execute('DROP TABLE IF EXISTS search_runs')
        fake = FakeWorkable(error=(403, {'Retry-After': '2'}))
        out = self.search(fake, profile)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(out['boards'][-1]['http_status'], 403)
        self.assertTrue(out['landing'])
        self.assertEqual(self.queries(), [])
        self.assertIn('403', out['boards'][-1]['reason'])

    def test_profile_isolation_and_first_seen_clamps_later_date(self):
        home = self.profile()
        away = copy.deepcopy(home)
        away['id'] = 'example-away'
        away['work_rules'].update(home_country='Farland', authorized_countries=[])
        self.search(FakeWorkable(), home)
        other = FakeWorkable()
        self.search(other, away)
        self.assertIn('location=Farland', other.calls[1])
        self.assertIn('place', self.saved(away['id'])[0][0]['fact_reason'].lower())
        self.assertEqual(self.saved()[0][0]['fact_reason'], 'Kept for review')
        later = TODAY + timedelta(days=15)
        self.search(FakeWorkable([{'date': later.isoformat()}]), home, today=later)
        self.assertLessEqual(self.saved()[0][0]['posted_on'], TODAY.isoformat())
        self.assertIn('14 days', self.saved()[0][0]['fact_reason'])
        self.assertEqual({row[0] for row in self.queries()}, {home['id'], away['id']})


if __name__ == '__main__':
    unittest.main(verbosity=2)
