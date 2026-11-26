import importlib.util
import json
import sqlite3
import unittest
from contextlib import closing, redirect_stdout
from datetime import date, datetime, timedelta
from io import StringIO
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import openpyxl

def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReviewSearch(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name)
        self.queue = self.work / 'search-backlog.sqlite3'
        self.usage = self.work / 'ai-usage.sqlite3'
        self.shadow = load('ai_shadow')
        self.profile = load('test_search_profile').minimal()
        self.day = date(2026, 11, 25)
        self.profile['work_rules']['home_country'] = 'Fableland'
        self.profile['goals']['lanes'][0]['titles'] = ['Research Analyst']
        self.job = dict(company='Fable Workshop', title='Research Analyst', provider='careers_page',
                        posting_id='1', link='https://jobs.example.invalid/1', date_source='careers_page',
                        posted_on=self.day.isoformat(), place_text='Fableland', remote=False, open=True,
                        description='Requires 10 years of experience.', found_by='Employer website')

    def test_filter_checks_age_confirmation_and_approved_filters(self):
        fact = self.shadow.fact_reason
        for age, expected in ((14, ''), (15, 'older')):
            job = dict(self.job, posted_on=(self.day - timedelta(days=age)).isoformat())
            result = fact(self.profile, job, self.day)
            self.assertIn(expected, result) if expected else self.assertEqual(result, '')
        self.assertIn('date', fact(self.profile, dict(self.job, posted_on=''), self.day))
        self.assertIn('employer', fact(self.profile, dict(self.job, open=False, lead=True), self.day))
        self.assertEqual(fact(self.profile, self.job, self.day), '')
        self.assertIn('10', fact(self.profile, self.job, self.day,
                                 filters=[dict(kind='years_maximum', value=5, why='Prefer fewer years.')]))

    def test_answer_and_filter_suggestions_are_profile_scoped(self):
        owner = self.profile['id']
        self.shadow.enqueue(self.profile, self.day, self.queue, None, [('careers_page', '', [self.job], 1, '')])
        item, = self.shadow.review(self.queue, owner, self.day)['jobs']
        self.assertEqual(self.shadow.review(self.queue, 'another-profile', self.day)['jobs'], [])
        self.shadow.answer_review(self.queue, owner, [item['identity']], 'no', 'Wrong focus.')
        self.shadow.enqueue(self.profile, self.day, self.queue, None, [('careers_page', '', [self.job], 1, '')])
        self.assertEqual(self.shadow.review(self.queue, owner, self.day)['jobs'], [])

    def test_search_calls_are_counted_per_run_and_across_reloads(self):
        owner = self.profile['id']
        day = self.day.isoformat()
        self.assertTrue(self.shadow.reserve_search(self.usage, owner, day, 'morning'))
        shadow = load('ai_shadow')
        self.assertFalse(shadow.reserve_search(self.usage, owner, day, 'morning'))
        self.assertTrue(shadow.reserve_search(self.usage, owner, day, 'evening'))
        self.assertFalse(shadow.reserve_search(self.usage, owner, day, 'extra'))
        with closing(shadow.judge._usage(self.usage)) as db:
            self.assertEqual(shadow.judge.qualification_usage(db, owner, day), 0)
            self.assertEqual(db.execute('SELECT SUM(attempts) FROM ai_usage').fetchone()[0], 2)

    def test_search_runs_enabled_engines_even_when_plan_omits_them(self):
        engines = self.shadow.engines
        first = Mock(return_value=[self.job])
        second = Mock(return_value=[])
        registry = {'web_search': first, 'remoteok': second}
        plan = dict(queries=[dict(engine='web_search', words='research analyst', place='Fableland', why='Fits.')],
                    employers=[], filters=[])
        batches = engines.find(self.profile, plan, self.day, self.queue, {}, registry=registry)
        first.assert_called_once()
        second.assert_called_once()
        self.assertIn(self.job, batches[0][2])

    def test_provider_response_fixtures(self):
        engines = self.shadow.engines
        fixtures = dict(
            web_search={'web': {'results': [{'title': 'Research Analyst', 'url': self.job['link']}]}},
            adzuna={'results': [{'title': 'Research Analyst', 'redirect_url': self.job['link'],
                                 'company': {'display_name': 'Fable Workshop'}}]},
            jooble={'jobs': [{'title': 'Research Analyst', 'link': self.job['link'], 'company': 'Fable Workshop'}]},
            careerjet={'jobs': [{'title': 'Research Analyst', 'url': self.job['link'], 'company': 'Fable Workshop'}]},
            remotive={'jobs': [{'title': 'Research Analyst', 'url': self.job['link'], 'company_name': 'Fable Workshop'}]},
            remoteok=[{'legal': 'Fixture'}, {'position': 'Research Analyst', 'url': self.job['link'],
                                            'company': 'Fable Workshop'}],
            arbeitnow={'data': [{'title': 'Research Analyst', 'url': self.job['link'], 'company_name': 'Fable Workshop'}]},
        )
        query = dict(words='research analyst', place='Fableland')
        for name, fixture in fixtures.items():
            with self.subTest(name=name):
                module = engines.load(name)
                transport = Mock(return_value=fixture)
                self.assertEqual(module.read(transport, [query], {'key': 'fixture', 'id': 'fixture'})[0]['url'],
                                 self.job['link'])
                transport.assert_called_once()

    def test_blocked_careers_host_is_not_read_again(self):
        careers = self.shadow.engines.careers
        for response in ((403, '', ''), (429, '', ''), (200, 'text/html', '<h1>CAPTCHA</h1>'),
                         (200, 'text/html', '<h1>Sign in to view this job</h1>')):
            with self.subTest(response=response):
                fetch = Mock(return_value=response)
                result = careers.read(fetch, [self.job['link'], 'https://jobs.example.invalid/2'])
                fetch.assert_called_once()
                self.assertTrue(result[1])

    def test_linkedin_reads_local_alerts_only_and_is_off_without_path(self):
        engines = self.shadow.engines
        module = engines.load('linkedin')
        transport = Mock(side_effect=AssertionError('No network expected'))
        self.assertFalse(module.enabled({}))
        alert = self.work / 'alert.eml'
        alert.write_text('Subject: New job alert\nContent-Type: text/plain\n\n'
                         'Title: Research Analyst\nCompany: Fable Workshop\n'
                         'https://www.linkedin.com/jobs/view/123\n', encoding='utf-8')
        rows = module.read(transport, [], {'path': str(self.work)})
        self.assertEqual([row['url'] for row in rows], ['https://www.linkedin.com/jobs/view/123'])
        self.assertEqual((rows[0]['title'], rows[0]['company']), ('Research Analyst', 'Fable Workshop'))
        transport.assert_not_called()

    def test_web_finds_an_employer_outside_the_configured_boards(self):
        engines = self.shadow.engines
        settings = {name: {'enabled': False} for name in engines.NAMES if name not in ('web_search', 'careers_page')}
        settings['web_search'] = {'key': 'fixture'}
        plan = dict(queries=[dict(engine='web_search', words='Research Analyst', place='Fableland', why='Fits.')],
                    employers=[], filters=[])
        api = Mock(return_value={'web': {'results': [{'url': self.job['link'], 'title': self.job['title']}]}})
        data = {'@type': 'JobPosting', 'datePosted': self.day.isoformat(), 'title': self.job['title'],
                'hiringOrganization': {'name': self.job['company']}, 'description': self.job['description'],
                'jobLocation': {'address': {'addressCountry': 'Fableland'}}}
        page = Mock(return_value=(200, 'text/html', '<script type="application/ld+json">' + json.dumps(data) + '</script>'))
        batches = engines.find(self.profile, plan, self.day, self.queue, settings, api=api, page=page)
        self.shadow.enqueue(self.profile, self.day, self.queue, None, batches)
        self.assertEqual([job['company'] for job in self.shadow.review(self.queue, self.profile['id'], self.day)['jobs']],
                         ['Fable Workshop'])
        settings.pop('web_search')
        api.reset_mock()
        batches = engines.find(self.profile, plan, self.day, self.queue, settings, api=api, page=page)
        self.assertIn('key', next(row[4] for row in batches if row[0] == 'web_search'))
        api.assert_not_called()

    def test_discovered_boards_require_an_answer_and_expire_after_failures(self):
        engines = self.shadow.engines
        settings = {name: {'enabled': False} for name in engines.NAMES if name != 'careers_page'}
        plan = dict(queries=[], employers=[dict(name='Fable Workshop', why='A new employer.')], filters=[])
        owner = self.profile['id']
        denied = Mock(side_effect=OSError('Offline fixture'))
        engines.find(self.profile, plan, self.day, self.queue, settings, api=denied)
        self.assertEqual(engines.remembered_boards(self.queue, owner), [])

        def provider(method, url, body=None, **kwargs):
            if 'boards-api.greenhouse.io' in url:
                return {'jobs': [{'id': '1', 'title': self.job['title'], 'absolute_url': self.job['link'],
                                  'location': {'name': 'Fableland'}, 'first_published': self.day.isoformat()}]}
            raise OSError('Offline fixture')
        engines.find(self.profile, plan, self.day, self.queue, settings, api=provider)
        board, = engines.remembered_boards(self.queue, owner)
        self.assertEqual((board['board'], board['why']), ('fableworkshop', 'A new employer.'))
        plan['employers'] = []
        engines.find(self.profile, plan, self.day + timedelta(days=29), self.queue, settings, api=denied)
        self.assertEqual(len(engines.remembered_boards(self.queue, owner)), 1)
        engines.find(self.profile, plan, self.day + timedelta(days=30), self.queue, settings, api=denied)
        self.assertEqual(engines.remembered_boards(self.queue, owner), [])

    def test_unconfirmed_aggregator_lead_is_hidden(self):
        engines = self.shadow.engines
        settings = {name: {'enabled': False} for name in engines.NAMES if name != 'remoteok'}
        api = Mock(return_value=[{'position': self.job['title'], 'company': self.job['company'],
                                 'url': 'https://remoteok.com/fixture'}])
        batches = engines.find(self.profile, dict(queries=[], employers=[], filters=[]),
                               self.day, self.queue, settings, api=api)
        self.assertEqual(self.shadow.enqueue(self.profile, self.day, self.queue, None, batches), 0)
        with closing(sqlite3.connect(self.queue)) as db:
            self.assertEqual(db.execute('SELECT reason FROM review_hidden').fetchall(), [(engines.NOT_CONFIRMED,)])

    def test_aggregator_points_to_the_confirmed_employer_posting(self):
        engines = self.shadow.engines
        settings = {name: {'enabled': False} for name in engines.NAMES if name not in ('remoteok', 'configured_boards')}
        self.profile['search']['companies'] = [dict(provider='greenhouse', board='fable',
                                                    label=self.job['company'], found_by='Fixture')]
        feed = Mock(return_value=[{'position': self.job['title'], 'company': self.job['company'],
                                   'url': 'https://remoteok.com/fixture'}])
        board = Mock(return_value={'jobs': [{'id': '1', 'title': self.job['title'], 'absolute_url': self.job['link'],
                                            'location': {'name': 'Fableland'}, 'first_published': self.day.isoformat()}]})
        batches = engines.find(self.profile, dict(queries=[], employers=[], filters=[]), self.day, self.queue,
                               settings, api=feed, board_fetch=board)
        self.assertEqual(self.shadow.enqueue(self.profile, self.day, self.queue, None, batches), 1)
        item, = self.shadow.review(self.queue, self.profile['id'], self.day)['jobs']
        self.assertEqual((item['link'], item['found_by']), (self.job['link'], 'Remote OK'))
        feed.assert_called_once()
        board.assert_called_once()

    def test_public_employer_api_fixtures(self):
        engines = self.shadow.engines
        fixtures = {
            'smartrecruiters': {'content': [{'id': '1', 'name': 'Research Analyst',
                                             'releasedDate': self.day.isoformat(), 'location': {'city': 'Fableland'}}]},
            'workable': {'jobs': [{'shortcode': '1', 'title': 'Research Analyst', 'shortlink': self.job['link'],
                                   'published_on': self.day.isoformat(), 'location': {'country': 'Fableland'}}]},
            'amazon': {'jobs': [{'id': '1', 'title': 'Research Analyst', 'job_path': '/jobs/1',
                                 'posted_date': self.day.isoformat(), 'location': 'Fableland'}]},
        }
        queries = [dict(words='Research Analyst', place='Fableland')]
        for provider, fixture in fixtures.items():
            with self.subTest(provider=provider):
                transport = Mock(side_effect=[fixture, {'active': True}] if provider == 'smartrecruiters' else [fixture])
                jobs, count = engines.extra_rows(dict(provider=provider, board='fable', label='Fable Workshop'),
                                                 queries, transport, self.day)
                self.assertEqual(count, 1)
                self.assertEqual(jobs[0]['posted_on'], self.day.isoformat())
                self.assertTrue(jobs[0]['open'])

    def test_suggestion_waits_for_approval_and_undo_restores_the_list(self):
        suggestion = dict(kind='years_maximum', value='5', why='Prefer fewer years.')
        tracker = self.work / 'applications-tracker.sqlite3'
        self.shadow.run(self.profile, {}, self.day, 'fixture', self.queue, tracker, self.usage,
                        Mock(return_value=dict(queries=[], employers=[], filters=[suggestion])),
                        reader=Mock(return_value=[('careers_page', '', [self.job], 1, '')]))
        owner = self.profile['id']
        view = lambda: self.shadow.review(self.queue, owner, self.day, self.profile)
        self.assertEqual(view()['remaining'], 1)
        filter_id = view()['suggestions'][0]['id']
        self.shadow.filter_choice(self.queue, owner, filter_id, 'approve')
        self.assertEqual(view()['remaining'], 0)
        self.shadow.filter_choice(self.queue, owner, filter_id, 'undo')
        self.assertEqual(view()['remaining'], 1)

    def make_tracker(self, work, existing=False):
        work.mkdir(exist_ok=True)
        xlsx = work / 'applications-tracker.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Jobs'
        header = self.shadow.landing.HEADER
        ws.append(header)
        if existing:
            values = {'#': 1, 'Company': self.job['company'], 'Role': self.job['title'],
                      'Where': 'Fableland', 'Link': self.job['link'],
                      'My status': 'Tracked', 'Posted': self.day.isoformat()}
            ws.append([values.get(field) for field in header])
        wb.save(xlsx)
        wb.close()
        self.assertTrue(self.shadow.landing.store.migrate(xlsx, apply=True)['ok'])
        return xlsx, xlsx.with_suffix('.sqlite3')

    def test_bulk_answers_write_each_yes_once_and_leave_each_no_hidden(self):
        xlsx, tracker = self.make_tracker(self.work)
        jobs = [dict(self.job, title=f'Research Analyst {i}', posting_id=str(i),
                     link=f'https://example.invalid/role/{i}') for i in range(4)]
        batches = [('careers_page', '', jobs, len(jobs), '')]
        owner = self.profile['id']
        self.shadow.enqueue(self.profile, self.day, self.queue, tracker, batches)
        ids = [item['identity'] for item in self.shadow.review(self.queue, owner, self.day)['jobs']]
        for _ in range(2):
            self.shadow.answer_review(self.queue, owner, ids[:2], 'yes', xlsx=xlsx,
                                      now=datetime.combine(self.day, datetime.min.time()))
        self.shadow.answer_review(self.queue, owner, ids[2:], 'no', 'Wrong focus.',
                                  now=datetime.combine(self.day, datetime.min.time()))
        with closing(self.shadow.landing.store.Store(tracker)) as storage:
            self.assertEqual([tuple(row) for row in storage.db.execute('SELECT status,notes FROM jobs')],
                             [('Tracked', 'Being scored'), ('Tracked', 'Being scored')])
            self.assertEqual(len(self.shadow.judge.pending_jobs(storage, profile=self.profile, backlog=self.queue)), 2)
        self.assertEqual(self.shadow.enqueue(self.profile, self.day, self.queue, tracker, batches), 0)
        self.assertEqual(self.shadow.review(self.queue, owner, self.day)['jobs'], [])

    def test_failed_yes_can_finish_without_adding_twice(self):
        xlsx, tracker = self.make_tracker(self.work)
        owner = self.profile['id']
        self.shadow.enqueue(self.profile, self.day, self.queue, tracker, [('careers_page', '', [self.job], 1, '')])
        item, = self.shadow.review(self.queue, owner, self.day)['jobs']
        ids = [item['identity']]
        clock = datetime.combine(self.day, datetime.min.time())
        with patch.object(self.shadow, 'save_answers', side_effect=OSError('Temporary save failure')):
            with self.assertRaises(OSError):
                self.shadow.answer_review(self.queue, owner, ids, 'yes', xlsx=xlsx, now=clock)
        self.assertTrue(self.shadow.review(self.queue, owner, self.day)['jobs'][0]['pending_yes'])
        self.shadow.answer_review(self.queue, owner, ids, 'yes', xlsx=xlsx, now=clock)
        with closing(sqlite3.connect(tracker)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)
        self.assertEqual(self.shadow.review(self.queue, owner, self.day)['jobs'], [])

    def test_replay_never_fetches_and_does_not_write_the_tracker(self):
        xlsx, tracker = self.make_tracker(self.work / 'work', existing=True)
        (self.work / 'profile.json').write_text(json.dumps(self.profile), encoding='utf-8')
        queue = tracker.with_name('search-backlog.sqlite3')
        before = tracker.read_bytes()
        normal = [('careers_page', '', [self.job], 1, '')]
        self.assertEqual(self.shadow.enqueue(self.profile, self.day, queue, tracker, normal), 0)
        with patch.object(self.shadow.engines, 'find', side_effect=AssertionError('Replay searched the network')):
            self.assertEqual(self.shadow.replay(self.work, today=self.day, days=7), 1)
        self.assertEqual(tracker.read_bytes(), before)
        owner = self.profile['id']
        item, = self.shadow.review(queue, owner, self.day)['jobs']
        self.assertEqual(item['found_by'], 'Tracker replay')
        self.shadow.answer_review(queue, owner, [item['identity']], 'yes', xlsx=xlsx,
                                  now=datetime.combine(self.day, datetime.min.time()))
        with closing(sqlite3.connect(tracker)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)


    def test_replay_cli_records_and_counts_every_filtered_job(self):
        work = self.work / 'work'
        work.mkdir()
        tracker = work / 'applications-tracker.sqlite3'
        queue = work / 'search-backlog.sqlite3'
        (self.work / 'profile.json').write_text(json.dumps(self.profile), encoding='utf-8')
        today = date.today()
        recent = today.isoformat()
        old = (today - timedelta(days=15)).isoformat()
        with closing(sqlite3.connect(tracker)) as db, db:
            db.execute('CREATE TABLE jobs (id,company,role,link,status,posted_on,place)')
            db.executemany('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)', [
                (number, company, self.job['title'], f'https://jobs.example.invalid/{number}', status, posted, place)
                for number, (company, status, posted, place) in enumerate([
                    ('Ready Workshop', 'Tracked', recent, 'Fableland'),
                    ('Old Workshop', 'Tracked', old, 'Fableland'),
                    ('Skipped Workshop', 'Skipped', recent, 'Fableland'),
                    ('Far Workshop', 'Tracked', recent, 'Elsewhereland'),
                    ('Other Far Workshop', 'Tracked', recent, 'Elsewhereland'),
                ], 1)
            ])

        output = StringIO()
        with patch.object(sys, 'argv', ['ai_shadow.py', '--replay']), \
             patch.object(self.shadow.search.settings, 'data_root', return_value=self.work), redirect_stdout(output):
            self.shadow.main()

        age = 'The employer posting is older than 14 days.'
        skip = 'This job was skipped.'
        place = 'The place where you can work could not be confirmed.'
        with closing(sqlite3.connect(queue)) as db:
            hidden = db.execute('SELECT body,reason FROM review_hidden').fetchall()
            self.assertCountEqual([(json.loads(body)['company'], reason) for body, reason in hidden], [
                ('Old Workshop', age), ('Skipped Workshop', skip), ('Far Workshop', place), ('Other Far Workshop', place),
            ])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM review_items').fetchone()[0], 1)
        lines = output.getvalue().splitlines()
        self.assertEqual(lines[:2], ['5 jobs read.', '1 job ready to review.'])
        self.assertCountEqual(lines[2:], [f'1 - {age}', f'1 - {skip}', f'2 - {place}'])
        payload = self.shadow.review(queue, self.profile['id'], today)
        self.assertCountEqual([job['reason'] for job in payload['filtered_out']], [age, skip, place, place])


if __name__ == '__main__':
    unittest.main()
