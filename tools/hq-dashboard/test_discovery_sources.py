"""Offline discovery and employer confirmation with fictional postings."""
from datetime import date, timedelta
from email.message import Message
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote_plus, urlsplit


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixtures = load('test_daily_search')
ds = fixtures.ds
DAY = date(2026, 3, 10)
ROLE = 'Example Analyst'
COMPANY = 'Fable Widgets'
SOURCES = ['himalayas', 'jobicy']


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.backlog = Path(folder.name) / 'backlog.sqlite3'
        self.clock = 10000.0
        self.calls = []
        self.leads = [self.lead()]
        self.jobicy = [dict(id=1, jobTitle=ROLE, companyName=COMPANY, jobGeo='Farawaylands,  Exampleland',
                            jobDescription='Example duties.', url='https://jobicy.com/jobs/example',
                            pubDate=DAY.isoformat())]
        self.board_name = COMPANY
        self.board_rows = [self.posting()]
        self.himalayas_status = None
        self.jobicy_status = None
        self.cursors = False
        self.extra_boards = {}
        self.board_errors = {}

    def lead(self, company=COMPANY, title=ROLE, country='Exampleland', slug='fable'):
        return dict(title=title, companyName=company, companySlug=slug, locationRestrictions=[country],
                    description='<p>Aggregator text.</p>', pubDate=1773000000,
                    guid='https://himalayas.app/jobs/' + company.replace(' ', '-') + '-' + title.replace(' ', '-'))

    def posting(self, age=14, title=ROLE):
        return dict(id=3, title=title, location={'name': 'Exampleland'},
                    first_published='' if age is None else (DAY - timedelta(days=age)).isoformat(),
                    absolute_url='https://example.invalid/jobs/3', content='Employer text.')

    def sleep(self, seconds):
        self.clock += seconds

    def profile(self, sources=('himalayas',), **search):
        profile = fixtures.ops_profile(companies=search.pop('companies', []), search_sources=list(sources), **search)
        profile['goals']['lanes'] = [
            dict(id='one', titles=[ROLE, 'Example coordinator'], duties=['write reports']),
            dict(id='two', titles=['Example assistant'], duties=['read reports'])]
        profile['work_rules']['remote_regions'] = ['Farawaylands']
        profile.update(name='Fictional Private Name', email='owner@example.invalid',
                       employer_history=['Past Private Employer'])
        return profile

    def request(self, method, url, body=None, **options):
        self.assertEqual(method, 'GET')
        self.assertIsNone(body)
        target = urlsplit(url)
        self.calls.append((self.clock, target))
        params = parse_qs(target.query)
        if target.hostname == 'himalayas.app':
            self.assertEqual(target.path, '/jobs/api/search')
            if self.himalayas_status:
                error, self.himalayas_status = self.himalayas_status, None
                headers = Message()
                headers['Retry-After'] = '4'
                raise HTTPError(url, error, '', headers, None)
            return dict(totalCount=len(self.leads), limit=20,
                        jobs=self.leads if params['page'] == ['1'] else [])
        if target.hostname == 'jobicy.com':
            if self.jobicy_status:
                status, self.jobicy_status = self.jobicy_status, None
                raise HTTPError(url, status, '', None, None)
            if params.get('get') == ['locations']:
                return dict(locations=[dict(geoName='Farawaylands', geoSlug='farawaylands')])
            self.assertEqual(target.path, '/api/v2/remote-jobs')
            return dict(jobs=self.jobicy, nextCursor='next+page' if self.cursors else None,
                        hasMore=self.cursors and not params.get('cursor'))
        if (target.hostname, target.path) in self.board_errors:
            status = self.board_errors[target.hostname, target.path]
            raise HTTPError(url, status, '', None, None)
        if target.hostname == 'boards-api.greenhouse.io' and target.path.startswith('/v1/boards/fable/'):
            return dict(jobs=self.board_rows)
        if target.hostname == 'boards-api.greenhouse.io' and target.path == '/v1/boards/fable':
            return dict(name=self.board_name)
        if target.hostname == 'boards-api.greenhouse.io' and target.path == '/v1/boards/known/jobs':
            return dict(jobs=self.board_rows)
        if (target.hostname, target.path) in self.extra_boards:
            return self.extra_boards[target.hostname, target.path]
        raise HTTPError(url, 404, '', None, None)

    def search(self, profile=None, day=DAY, **options):
        def workable(method, url, **kwargs):
            if hasattr(self, 'workable_fixture'):
                return self.workable_fixture(method, url, **kwargs)
            return 'Found **0** matching jobs.'

        with mock.patch.object(ds.time, 'monotonic', side_effect=lambda: self.clock), \
             mock.patch.object(ds.time, 'time', side_effect=lambda: self.clock), \
             mock.patch.object(ds.time, 'sleep', side_effect=self.sleep):
            return ds.run(profile or self.profile(), self.request, self.backlog, day, mark_landed=True,
                          discovery_fetch=self.request, search_fetch=workable, **options)

    def saved(self, owner='example-ops'):
        with ds.connect(self.backlog) as db:
            return [(json.loads(body), landed) for body, landed in db.execute(
                'SELECT body, landed FROM search_backlog WHERE profile_id=?', (owner,))]

    def board_calls(self):
        return [(stamp, target) for stamp, target in self.calls
                if target.hostname not in ('himalayas.app', 'jobicy.com')]

    def test_switches_and_jobicy_geo_use_profile_only(self):
        out = self.search(self.profile(['jobicy']))
        self.assertTrue(ds.boards_read(out))
        self.assertFalse(any(url.hostname == 'himalayas.app' for _, url in self.calls))
        feed = [url for _, url in self.calls if url.path == '/api/v2/remote-jobs']
        self.assertEqual(parse_qs(feed[0].query), {'count': ['50'], 'geo': ['farawaylands']})
        for _, url in self.calls:
            for private in ('Fictional Private Name', 'owner@example.invalid', 'Past Private Employer'):
                self.assertNotIn(private, unquote_plus(url.geturl()))

    def test_unknown_geo_is_omitted_and_country_can_supply_geo(self):
        for name, expected in [('Elsewhereland', {}), ('Farawaylands', {'geo': ['farawaylands']})]:
            profile = self.profile(['jobicy'])
            profile['work_rules'].update(remote_regions=[], home_country=name)
            self.calls.clear()
            self.clock += 3600
            self.search(profile)
            feed = next(url for _, url in self.calls if url.path == '/api/v2/remote-jobs')
            self.assertEqual(parse_qs(feed.query), {'count': ['50'], **expected})

    def test_exact_employer_copy_uses_employer_facts_and_never_lands(self):
        out = self.search()
        self.assertEqual(out['landing'], [])
        [(job, landed)] = self.saved()
        self.assertEqual((job['provider'], job['posting_id'], job['found_by'], landed),
                         ('greenhouse', '3', 'himalayas', 0))
        self.assertEqual(job['fact_reason'], ds.KEPT_FOR_REVIEW)
        self.assertEqual(job['posted_on'], (DAY - timedelta(days=14)).isoformat())
        self.assertEqual(job['description'], 'Employer text.')
        self.assertEqual(job['link'], 'https://example.invalid/jobs/3')
        self.assertTrue(out['boards'][0]['search_only'])
        self.assertEqual(out['boards'][0]['kept'], 1)
        with ds.connect(self.backlog) as db:
            self.assertEqual(db.execute('SELECT source, query, kept FROM search_runs ORDER BY rowid').fetchall(),
                             [('himalayas', word, 1) for word in
                              [ROLE, 'Example assistant', 'Example coordinator', 'write reports', 'read reports']])

    def test_unknown_and_day_fifteen_stay_hidden(self):
        for age, reason in [(None, 'date could not be confirmed'), (15, 'older than 14 days')]:
            with self.subTest(age=age):
                profile = self.profile()
                profile['id'] = str(age)
                self.board_rows = [self.posting(age)]
                self.assertEqual(self.search(profile)['landing'], [])
                [(job, landed)] = self.saved(str(age))
                self.assertIn(reason, job['fact_reason'])
                self.assertEqual(landed, 0)

    def test_wrong_company_negative_cache_expires_and_is_profile_scoped(self):
        self.board_name = 'Other Widgets'
        self.search()
        self.assertTrue(self.board_calls())
        [(job, landed)] = self.saved()
        self.assertEqual(job['fact_reason'], "Not confirmed on the employer's site.")
        self.assertEqual((job['provider'], job['posted_on'], landed), ('himalayas', '', 0))
        self.assertEqual(job['link'], self.leads[0]['guid'])
        self.calls.clear()
        self.search(day=DAY + timedelta(days=13))
        self.assertEqual(self.board_calls(), [])
        other = self.profile()
        other['id'] = 'another-fictional-profile'
        self.assertEqual(self.saved(other['id']), [])
        self.search(other)
        self.assertTrue(self.board_calls())
        self.assertEqual(len(self.saved(other['id'])), 1)
        self.calls.clear()
        self.search(day=DAY + timedelta(days=14))
        self.assertTrue(self.board_calls())

    def test_exclusion_place_and_tracker_precede_board_reads(self):
        self.leads = [self.lead(title='Example Intern'), self.lead(company='Imaginary Cloud', country='Elsewhereland'),
                      self.lead(company='Sample Cloud'), self.lead(company='Fictional Cloud'),
                      self.lead(company=COMPANY)]
        profile = self.profile(exclude_titles=[{'pattern': 'intern', 'reason': 'Excluded title.'}])
        repeats = {(ds.job_key(company, ROLE)): reason for company, reason in [
            ('Sample Cloud', ds.SKIPPED), ('Fictional Cloud', ds.ARCHIVED), (COMPANY, ds.ON_TRACKER)]}
        with mock.patch.object(ds, 'tracker_repeats', return_value=(repeats, {})):
            self.search(profile)
        self.assertEqual(self.board_calls(), [])
        self.assertEqual(len(self.saved()), 5)

    def test_jobicy_filters_words_and_credits_unmatched_lead(self):
        self.board_rows = [self.posting(title='Example Manager')]
        self.jobicy.append(dict(self.jobicy[0], id=2, jobTitle='Unrelated Role', jobDescription='Unrelated text.'))
        out = self.search(self.profile(['jobicy']))
        [(job, _)] = self.saved()
        self.assertEqual(job['fact_reason'], "Not confirmed on the employer's site.")
        self.assertEqual((job['found_by'], job['link']), ('jobicy', 'https://jobicy.com/jobs/example'))
        self.assertEqual(out['boards'][0]['rows'], 1)
        self.calls.clear()
        self.assertTrue(ds.boards_read(self.search(self.profile(['jobicy']))))
        self.assertEqual(self.calls, [])

    def test_himalayas_hides_fuzzy_misses_and_follows_title_matches_first(self):
        duty_first = dict(self.lead('Duty First', title='Reporting Lead', slug='duty-first'),
                          description='We write reports.')
        title_first = self.lead('Title First', title='Senior Example - Analyst', slug='title-first')
        title_later = self.lead('Title Later', slug='title-later')
        self.leads = [duty_first, self.lead('Fuzzy Only', title='Example Analysis'), title_first, title_later,
                      self.lead('Title Later', title='Example Coordinator', slug='title-later'),
                      dict(self.lead('Duty Later', title='Reporting Lead', slug='duty-later'),
                           description='We read reports.')]
        out = self.search()
        first_attempts = [url.path for _, url in self.board_calls() if url.hostname == 'boards-api.greenhouse.io'
                          and '-' in url.path]
        self.assertEqual(first_attempts, [f'/v1/boards/{slug}/jobs' for slug in
                                         ('title-first', 'title-later', 'duty-first', 'duty-later')])
        self.assertFalse(any('/fable' in url.path for _, url in self.board_calls()))
        self.assertEqual(out['boards'][0]['hidden']['Does not match your search.'], 1)
        self.assertIn(('Fuzzy Only', 'Does not match your search.'),
                      [(job['company'], job['fact_reason']) for job, _ in self.saved()])

    def test_lever_and_ashby_require_posting_company_and_title(self):
        self.board_rows = []
        self.board_name = 'Other Widgets'
        lever = ('api.lever.co', '/v0/postings/fable')
        ashby = ('api.ashbyhq.com', '/posting-api/job-board/fable')
        self.extra_boards[lever] = [dict(id='wrong-company', text=ROLE, companyName='Other Fictional Widgets',
                                         descriptionPlain=f'We integrate with {COMPANY}.',
                                         categories=dict(location='Exampleland'), hostedUrl='https://example.invalid/lever')]
        self.extra_boards[ashby] = dict(jobs=[
            dict(id='wrong-role', title='Example Manager', descriptionPlain=f'Join {COMPANY}.',
                 location='Exampleland', jobUrl='https://example.invalid/manager', publishedAt=DAY.isoformat()),
            dict(id='correct', title='Example - Analyst', descriptionPlain=f'Join {COMPANY}.',
                 location='Exampleland', jobUrl='https://example.invalid/analyst', publishedAt=DAY.isoformat())])
        self.search()
        [(job, _)] = self.saved()
        self.assertEqual((job['provider'], job['posting_id'], job['fact_reason']),
                         ('ashby', 'correct', ds.KEPT_FOR_REVIEW))

    def test_workable_match_checks_the_detail_company_and_title(self):
        self.board_rows = []
        self.board_name = 'Other Widgets'
        for changes, keep in [({'hiringOrganization': {'name': 'Other Widgets'}}, False),
                              ({'title': 'Example Manager'}, False), ({}, True)]:
            with self.subTest(changes=changes):
                profile = self.profile()
                profile['id'] = str(changes)
                self.workable_fixture = fixtures.FakeWorkable([{'title': ROLE, 'company': COMPANY, 'ld': changes}])
                self.assertEqual(self.search(profile)['landing'], [])
                [(job, landed)] = self.saved(profile['id'])
                self.assertEqual(landed, 0)
                self.assertEqual(job['fact_reason'], ds.KEPT_FOR_REVIEW if keep
                                 else "Not confirmed on the employer's site.")
                self.assertEqual(job['provider'], 'workable_search' if keep else 'himalayas')
                query = parse_qs(urlsplit(self.workable_fixture.calls[0]).query)
                self.assertEqual(query['query'], [COMPANY])

    def test_workable_is_last_and_searches_once_for_both_titles(self):
        self.leads = [self.lead(), self.lead(title='Example Assistant')]
        self.board_name = 'Other Widgets'
        fake = fixtures.FakeWorkable([{'title': title, 'company': COMPANY} for title in (ROLE, 'Example Assistant')])

        def workable(method, url, **kwargs):
            self.calls.append((self.clock, urlsplit(url)))
            return fake(method, url, **kwargs)

        self.workable_fixture = workable
        self.search()
        self.assertEqual([(url.hostname, url.path) for _, url in self.board_calls()], [
            ('boards-api.greenhouse.io', '/v1/boards/fable/jobs'),
            ('boards-api.greenhouse.io', '/v1/boards/fable'),
            ('api.lever.co', '/v0/postings/fable'),
            ('api.ashbyhq.com', '/posting-api/job-board/fable'),
            *[(host, path.format(slug=slug)) for slug in ('fable-widgets', 'fablewidgets') for host, path in (
                ('boards-api.greenhouse.io', '/v1/boards/{slug}/jobs'),
                ('api.lever.co', '/v0/postings/{slug}'),
                ('api.ashbyhq.com', '/posting-api/job-board/{slug}'))],
            ('jobs.workable.com', '/jobs.md'),
            ('jobs.workable.com', f'/view/{fake.uid(0)}'),
            ('jobs.workable.com', f'/view/{fake.uid(1)}'),
        ])
        self.assertEqual(parse_qs(urlsplit(fake.calls[0]).query)['query'], [COMPANY])
        self.assertEqual({job['title'] for job, _ in self.saved() if job['fact_reason'] == ds.KEPT_FOR_REVIEW},
                         {ROLE, 'Example Assistant'})

    def test_company_cap_applies_even_with_requests_left(self):
        self.leads = [self.lead(company=f'Widgetry{i}', slug=f'widgetry{i}') for i in range(20)]
        for i in range(20):
            host, path = 'boards-api.greenhouse.io', f'/v1/boards/widgetry{i}'
            self.extra_boards[host, path] = dict(name=f'Widgetry{i}')
            self.extra_boards[host, path + '/jobs'] = dict(jobs=[self.posting()])
        out = self.search()
        self.assertEqual(len(self.board_calls()), 30)
        self.assertEqual((out['boards'][0]['read'], out['boards'][0]['kept']), ('complete', 15))

    def test_configured_board_is_reused_and_all_three_sources_keep_one(self):
        self.workable_fixture = fixtures.FakeWorkable([{'title': ROLE, 'company': COMPANY}])
        profile = self.profile(['workable_search', *SOURCES],
                               companies=[dict(label=COMPANY, provider='greenhouse', board='known')])
        out = self.search(profile)
        self.assertEqual(len(self.board_calls()), 1)
        kept = [job for job, _ in self.saved() if job['fact_reason'] == ds.KEPT_FOR_REVIEW]
        self.assertEqual([job['provider'] for job in kept], ['greenhouse'])
        baseline = self.profile([], companies=profile['search']['companies'])
        baseline['id'] = 'baseline'
        self.assertEqual(out['boards'][0], self.search(baseline)['boards'][0])

    def test_403_stops_one_source_and_retry_after_is_paced(self):
        self.himalayas_status = 403
        out = self.search(self.profile(SOURCES))
        self.assertEqual([row['read'] for row in out['boards']], ['failed', 'complete'])
        self.assertEqual(len([url for _, url in self.calls if url.hostname == 'himalayas.app']), 1)
        self.calls.clear()
        self.himalayas_status = 429
        self.search()
        times = [stamp for stamp, url in self.calls if url.hostname == 'himalayas.app']
        self.assertGreaterEqual(times[1] - times[0], 4)
        self.assertTrue(all(b - a >= 2 for a, b in zip(times[1:], times[2:])))
        self.calls.clear()
        self.clock += 3600
        self.jobicy_status = 403
        out = self.search(self.profile(SOURCES))
        self.assertEqual([row['read'] for row in out['boards']], ['complete', 'failed'])
        self.assertEqual(len([url for _, url in self.calls if url.hostname == 'jobicy.com']), 1)

    def test_positive_cache_reads_only_the_confirmed_board_for_a_new_title(self):
        self.board_rows = []
        self.search()
        self.assertEqual([url.path for _, url in self.board_calls()], ['/v1/boards/fable/jobs', '/v1/boards/fable'])
        self.calls.clear()
        role = 'Example Assistant'
        self.leads = [self.lead(title=role, slug='different-slug')]
        self.board_rows = [self.posting(age=1, title=role)]
        self.board_rows[0].update(id=4, absolute_url='https://example.invalid/jobs/4')
        self.search(day=DAY + timedelta(days=1))
        self.assertEqual([url.path for _, url in self.board_calls()], ['/v1/boards/fable/jobs'])
        self.assertIn(role, [job['title'] for job, _ in self.saved() if job['fact_reason'] == ds.KEPT_FOR_REVIEW])

    def test_posting_can_prove_lever_or_ashby_without_the_requested_title(self):
        self.board_name = 'Other Widgets'
        for provider, host, path, role_field, url_field in (
                ('lever', 'api.lever.co', '/v0/postings/fable', 'text', 'hostedUrl'),
                ('ashby', 'api.ashbyhq.com', '/posting-api/job-board/fable', 'title', 'jobUrl')):
            with self.subTest(provider=provider):
                self.calls.clear()
                self.extra_boards.clear()
                self.leads = [self.lead()]
                profile = dict(self.profile(), id=provider)
                posting = dict(id=1, companyName=COMPANY, location='Exampleland',
                               categories={'location': 'Exampleland'}, publishedAt=DAY.isoformat(),
                               createdAt=fixtures.ms(0), **{role_field: 'Example Manager',
                                                         url_field: 'https://example.invalid/jobs/1'})
                self.extra_boards[host, path] = [posting] if provider == 'lever' else {'jobs': [posting]}
                self.search(profile)
                self.assertEqual([url.path for _, url in self.board_calls()],
                                 ['/v1/boards/fable/jobs', '/v1/boards/fable', '/v0/postings/fable']
                                 + ([path] if provider == 'ashby' else []))
                self.assertEqual(self.saved(provider)[0][0]['fact_reason'], ds.discovery.NOT_CONFIRMED)
                self.calls.clear()
                later = DAY + timedelta(days=15)
                role = 'Example Assistant'
                self.leads = [self.lead(title=role, slug='different-slug')]
                posting.update({role_field: role, 'createdAt': fixtures.ms(-15), 'publishedAt': later.isoformat()})
                self.search(profile, day=later)
                self.assertEqual([url.path for _, url in self.board_calls()], [path])
                self.assertIn((provider, role, ds.KEPT_FOR_REVIEW),
                              [(job['provider'], job['title'], job['fact_reason']) for job, _ in self.saved(provider)])

    def test_gone_remembered_board_is_forgotten(self):
        board = ('boards-api.greenhouse.io', '/v1/boards/fable/jobs')
        for status in (404, 410):
            with self.subTest(status=status):
                profile = dict(self.profile(), id=f'gone-{status}')
                self.board_rows = []
                self.search(profile)
                self.board_errors[board] = status
                self.calls.clear()
                self.search(profile, day=DAY + timedelta(days=1))
                self.assertEqual([url.path for _, url in self.board_calls()], [board[1]])
                with ds.connect(self.backlog) as db:
                    self.assertIsNone(db.execute('SELECT board FROM discovery_boards WHERE profile_id=?',
                                                 (profile['id'],)).fetchone())
                del self.board_errors[board]
                self.calls.clear()
                self.search(profile, day=DAY + timedelta(days=2))
                self.assertEqual([url.path for _, url in self.board_calls()], [board[1], '/v1/boards/fable'])

    def test_employer_errors_stay_on_the_lead_and_allow_retry(self):
        board = ('boards-api.greenhouse.io', '/v1/boards/fable/jobs')
        for status in (429, 503, 'invalid'):
            with self.subTest(status=status):
                profile = dict(self.profile(), id=f'retry-{status}')
                if status == 'invalid':
                    self.board_rows = None
                else:
                    self.board_errors[board] = status
                out = self.search(profile)
                self.assertEqual((out['boards'][0]['read'], out['boards'][0]['reason']), ('complete', ''))
                [(job, _)] = self.saved(profile['id'])
                self.assertIn('An employer board', job['fact_reason'])
                self.assertIn(f'HTTP {status}' if status != 'invalid' else 'invalid', job['fact_reason'])
                with ds.connect(self.backlog) as db:
                    self.assertIsNone(db.execute('SELECT board FROM discovery_boards WHERE profile_id=?',
                                                 (profile['id'],)).fetchone())
                self.board_errors.clear()
                self.board_rows = [self.posting(age=1)]
                self.search(profile, day=DAY + timedelta(days=1))
                self.assertEqual(self.saved(profile['id'])[0][0]['fact_reason'], ds.KEPT_FOR_REVIEW)

    def test_request_caps_and_pacing_do_not_make_reads_partial(self):
        profile = self.profile(SOURCES)
        profile['goals']['lanes'][0]['titles'].extend(f'Fictional role {i}' for i in range(15))
        self.leads = [self.lead(company=f'Fable Widgets {i}', slug=f'fable{i}') for i in range(20)]
        self.cursors = True
        self.workable_fixture = lambda method, url, **kw: self.request(method, url, **kw)
        out = self.search(profile)
        self.assertEqual([row['read'] for row in out['boards']], ['complete', 'complete'])
        for host, count in [('himalayas.app', 12), ('jobicy.com', 3)]:
            times = [stamp for stamp, url in self.calls if url.hostname == host]
            self.assertEqual(len(times), count)
            self.assertTrue(all(b - a >= 2 for a, b in zip(times, times[1:])))
        self.assertEqual(len(self.board_calls()), 60)
        self.assertFalse(any('workday' in url.netloc for _, url in self.board_calls()))
        for host in {url.netloc for _, url in self.board_calls()}:
            times = [stamp for stamp, url in self.board_calls() if url.netloc == host]
            self.assertTrue(all(b - a >= 1 for a, b in zip(times, times[1:])))


def load_daily_run_fixtures():
    path = Path(__file__).resolve().parents[1] / 'tracker' / 'test_daily_run.py'
    spec = importlib.util.spec_from_file_location('discovery_daily_run_fixtures', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


daily_run_fixtures = load_daily_run_fixtures()


class DiscoveryDailyRunTests(daily_run_fixtures.Base):
    def test_only_failed_discovery_reads_warn_in_the_search_phase(self):
        profile = fixtures.ops_profile(companies=[], search_sources=SOURCES)
        (self.root / 'profile.json').write_text(json.dumps(profile), encoding='utf-8')
        search = self.dr.daily_search

        for reads_page, expected in ((True, 'done'), (False, 'partial')):
            with self.subTest(reads_page=reads_page):
                def feed(method, url, **kwargs):
                    target = urlsplit(url)
                    if target.hostname == 'jobicy.com':
                        return dict(locations=[]) if target.path == '/api/jobs' else dict(jobs=[], hasMore=False)
                    query = parse_qs(target.query)['q']
                    if reads_page and query == ['partner operations']:
                        return dict(totalCount=0, limit=20, jobs=[])
                    raise HTTPError(url, 503, '', None, None)

                with mock.patch.object(search.discovery, 'fetch_json', side_effect=feed), \
                     mock.patch.object(search.time, 'sleep'), \
                     mock.patch.object(search.discovery, 'reserve_jobicy', return_value=True):
                    out = self.run_daily(force=True)
                self.assertEqual(out['phases']['search']['status'], expected)
                self.assertIn('HTTP 503', out['phases']['search']['detail'])

    def test_panel_reports_hidden_reasons_without_landing_or_closing(self):
        profile = fixtures.ops_profile(companies=[], search_sources=SOURCES)
        profile['work_rules']['remote_regions'] = ['Farawaylands']
        (self.root / 'profile.json').write_text(json.dumps(profile), encoding='utf-8')

        def feed(method, url, **kwargs):
            target = urlsplit(url)
            if target.hostname == 'himalayas.app':
                row = dict(title='Partner Operations Assistant', companyName=COMPANY,
                           locationRestrictions=['Elsewhereland'], guid='https://himalayas.app/jobs/fictional-assistant')
                return dict(totalCount=1, limit=20, jobs=[row] if parse_qs(target.query)['page'] == ['1'] else [])
            return dict(locations=[]) if target.path == '/api/jobs' else dict(jobs=[], hasMore=False)

        before = self.jobs()
        search = self.dr.daily_search
        with mock.patch.object(search.discovery, 'fetch_json', side_effect=feed), \
             mock.patch.object(search.time, 'sleep'):
            out = self.run_daily()
        self.assertEqual(out['phases']['search']['status'], 'done')
        detail = out['phases']['search']['detail']
        self.assertIn('Himalayas found 1, kept 0 for review, and hid 1 (The place where you can work could not be confirmed).',
                      detail)
        self.assertIn('Jobicy found 0, kept 0 for review, and hid 0.', detail)
        self.assertEqual(self.jobs(), before)
        self.assertEqual(self.landed(), 0)
        self.assertIn('0 closed', out['phases']['recheck']['detail'])
        with search.connect(self.db_file) as db:
            self.assertEqual(db.execute('SELECT provider FROM board_reads ORDER BY id').fetchall(),
                             [('himalayas',), ('jobicy',)])


if __name__ == '__main__':
    unittest.main()
