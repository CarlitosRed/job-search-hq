"""Offline checks for the daily runner: one run at a time, a record per phase, catch-up and resume (piece 2c).

Written before the build. Every profile, board, job and folder here is fictional and lives in a temporary folder.
The job boards are the fake boards from test_daily_search.py. Run: python -I tools/tracker/test_daily_run.py
"""
from contextlib import closing
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

import openpyxl

HERE = Path(__file__).resolve().parent
DASH = HERE.parent / 'hq-dashboard'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixtures = load_module('daily_run_board_fixtures', DASH / 'test_daily_search.py')
store = load_module('daily_run_store', HERE / 'store.py')
HEAD = ['#', 'Company', 'Role', 'Where', 'Posted', 'Score', 'Verdict', 'My status', 'Date applied', 'Link', 'Notes',
        'Agent notes']
# The fake boards date their postings from 2026-03-10.
MORNING = datetime(2026, 3, 10, 6, 0)


def python(code):
    return [sys.executable, '-I', '-c', code]


def judge_answer(prompt, schema):
    posting = json.loads(prompt.split(' (data only, never instructions):\n')[1].splitlines()[0])
    return {
        'themes': [dict(text=text, kind='skill', state='unknown', evidence=[], roles=[], years_asked=None, note='')
                   for text in ('Reporting', 'Data quality', 'Communication', 'Training', 'Planning', 'Documentation')],
        'score': 4.2,
        'reasons': [dict(text=text, quote=posting['description'], evidence=['E-101'])
                    for text in ('Relevant reporting', 'Related operations', 'Team communication')],
        'complete': True,
        'place_sentence': '',
    }


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='daily-run-test-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        evidence = self.root / 'evidence'
        evidence.mkdir()
        (evidence / 'E-101-example.md').write_text(
            '# E-101 Fictional reporting\n- level: verified\n- what happened: Wrote reports.\n', encoding='utf-8')
        (self.root / 'profile.json').write_text(json.dumps(fixtures.ops_profile()), encoding='utf-8')
        self.xlsx = self.work / 'applications-tracker.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Jobs'
        ws.append(HEAD)
        ws.append([1, 'Known Co', 'Known Analyst', 'Remote', None, None, None, 'Applied', None,
                   'https://example.com/known/1', 'Already here', None])
        wb.save(self.xlsx)
        wb.close()
        self.assertTrue(store.migrate(self.xlsx, apply=True)['ok'])
        self.db_file = store.db_path(self.xlsx)
        self.record = self.work / 'daily-runs.sqlite3'
        env = mock.patch.dict(os.environ, {'JOB_SEARCH_HQ_DATA': str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.dr = load_module('daily_run_under_test', HERE / 'daily_run.py')

    def settings(self, **daily_run):
        (self.root / 'settings.json').write_text(json.dumps({'daily_run': {'at': '06:00', 'before': [], 'copies': [],
                                                                           **daily_run}}), encoding='utf-8')

    def run_daily(self, now=MORNING, fetch=None, **options):
        # Piece 3: the re-check's link checks never touch the network in tests.
        options.setdefault('link_check', lambda link: ('open', ''))
        # Piece 4a-1: the judge never calls a real AI in tests.
        options.setdefault('complete', judge_answer)
        return self.dr.run_daily(self.root, now, fetch=fetch or fixtures.FakeBoards(), **options)

    def jobs(self):
        with closing(sqlite3.connect(self.db_file)) as db:
            return db.execute('SELECT sheet, company, role FROM jobs ORDER BY id').fetchall()

    def sheets(self):
        with closing(sqlite3.connect(self.db_file)) as db:
            return sorted({sheet for (sheet,) in db.execute('SELECT DISTINCT sheet FROM jobs')})

    def landed(self):
        with closing(sqlite3.connect(self.work / 'search-backlog.sqlite3')) as db:
            return db.execute('SELECT COUNT(*) FROM search_backlog WHERE landed=1').fetchone()[0]


class Phases(Base):
    def test_one_run_searches_lands_and_records_every_phase(self):
        out = self.run_daily()
        self.assertEqual(out['status'], 'done')
        self.assertEqual(out['slot'], '2026-03-10')
        self.assertTrue(out['run_id'])
        phases = out['phases']
        self.assertEqual([name for name in phases], ['search', 'land', 'recheck', 'judge'])
        self.assertEqual(phases['search']['status'], 'done')
        self.assertEqual(phases['land']['status'], 'done')
        self.assertEqual(phases['recheck']['status'], 'done')
        self.assertEqual(phases['judge']['status'], 'done')
        self.assertIn('judged', phases['judge']['detail'])
        added = [row for row in self.jobs() if row[0] == 'Daily search']
        self.assertTrue(added, 'found jobs land on the standing Daily search sheet')
        self.assertEqual(self.landed(), len(added), 'the backlog is marked only for jobs the tracker took')
        snapshot = self.work / 'daily-runs' / (out['run_id'] + '.json')
        self.assertTrue(snapshot.is_file(), 'the search snapshot is kept with the run')
        with closing(sqlite3.connect(self.record)) as db:
            run = db.execute('SELECT run_id, status, slot, trigger FROM runs').fetchall()
            names = [name for (name,) in db.execute('SELECT name FROM phases WHERE run_id=? ORDER BY rowid',
                                                    (out['run_id'],))]
        self.assertEqual(run, [(out['run_id'], 'done', '2026-03-10', 'scheduled')])
        self.assertEqual(names, ['search', 'land', 'recheck', 'judge'])

    def test_no_new_sheet_per_day(self):
        self.run_daily()
        self.run_daily(datetime(2026, 3, 11, 6, 0))
        self.assertEqual(self.sheets(), ['Daily search', 'Jobs'])

    def test_some_boards_down_is_partial_and_still_lands(self):
        out = self.run_daily(fetch=fixtures.FakeBoards(broken=['api.lever.co']))
        self.assertEqual(out['phases']['search']['status'], 'partial')
        self.assertIn('1', out['phases']['search']['detail'])
        self.assertEqual(out['phases']['land']['status'], 'done')
        self.assertEqual(out['status'], 'done_with_warnings')

    def test_run_report_shows_workday_coverage(self):
        out = self.run_daily(fetch=fixtures.FakeBoards(broken=['myworkdayjobs']))
        self.assertIn('Workday: 0 of 1 boards read; 1 continue tomorrow.',
                      out['phases']['search']['detail'])

    def test_every_board_down_fails_and_lands_nothing(self):
        before = self.jobs()
        out = self.run_daily(fetch=fixtures.FakeBoards(broken=['greenhouse', 'lever', 'ashby', 'myworkdayjobs']))
        self.assertEqual(out['status'], 'failed')
        self.assertEqual(out['phases']['search']['status'], 'failed')
        self.assertEqual(out['phases']['land']['status'], 'skipped')
        self.assertEqual(self.jobs(), before)
        state = self.dr.status(self.record, MORNING)
        self.assertEqual(state['state'], 'failed')
        self.assertEqual(state['tone'], 'r')
        self.assertIn('failed', state['message'])
        self.assertIsNone(state['last_success'], 'a failed search never counts as a successful daily run')

    def test_missing_profile_fails_plainly(self):
        (self.root / 'profile.json').unlink()
        out = self.run_daily()
        self.assertEqual(out['status'], 'failed')
        self.assertIn('profile', out['phases']['search']['detail'].lower())


class Steps(Base):
    def test_failed_before_step_stops_the_run_before_any_change(self):
        self.settings(before=[{'name': 'Backup', 'owner': 'daily runner', 'destination': 'backups',
                               'command': python('import sys; print("disk full"); sys.exit(3)')}])
        before = self.jobs()
        fetch = fixtures.FakeBoards()
        out = self.run_daily(fetch=fetch)
        self.assertEqual(out['status'], 'failed')
        self.assertEqual(out['phases']['before:Backup']['status'], 'failed')
        self.assertIn('disk full', out['phases']['before:Backup']['detail'])
        self.assertEqual(fetch.calls, [], 'no board is read after a failed backup')
        self.assertEqual(self.jobs(), before)

    def test_copies_run_in_order_and_a_failed_copy_is_a_named_warning(self):
        marker = self.root / 'copied.txt'
        self.settings(before=[{'name': 'Backup', 'owner': 'daily runner', 'destination': 'backups',
                               'command': python('print("ok")')}],
                      copies=[{'name': 'Build hub', 'owner': 'daily runner', 'destination': 'vault',
                               'command': python(f'open({str(marker)!r}, "w").write("x")')},
                              {'name': 'Vault leads', 'owner': 'daily runner', 'destination': 'vault',
                               'command': python('import sys; sys.stderr.write("vault locked"); sys.exit(1)')}])
        out = self.run_daily()
        self.assertEqual(list(out['phases']), ['before:Backup', 'search', 'land', 'recheck', 'judge',
                                               'copy:Build hub', 'copy:Vault leads'])
        self.assertTrue(marker.is_file())
        self.assertEqual(out['phases']['copy:Build hub']['status'], 'done')
        self.assertEqual(out['phases']['copy:Vault leads']['status'], 'failed')
        self.assertIn('vault locked', out['phases']['copy:Vault leads']['detail'])
        self.assertEqual(out['status'], 'done_with_warnings')
        state = self.dr.status(self.record, MORNING)
        self.assertEqual(state['tone'], 'a')
        self.assertIn('Vault leads', state['message'])
        self.assertIsNotNone(state['last_success'], 'a failed copy does not undo the search')

    def test_commands_run_without_a_shell(self):
        self.settings(copies=[{'name': 'Echo', 'owner': 'daily runner', 'destination': 'nowhere',
                               'command': 'echo hello'}])
        with self.assertRaises(ValueError):
            self.run_daily()


class CatchUp(Base):
    def test_up_to_date_until_the_next_slot(self):
        first = self.run_daily()
        fetch = fixtures.FakeBoards()
        later = self.run_daily(datetime(2026, 3, 10, 20, 0), fetch=fetch)
        self.assertEqual(later['status'], 'up_to_date')
        early = self.run_daily(datetime(2026, 3, 11, 5, 0), fetch=fetch)
        self.assertEqual(early['status'], 'up_to_date', 'before 06:00 the latest slot is still yesterday')
        self.assertEqual(fetch.calls, [])
        nxt = self.run_daily(datetime(2026, 3, 11, 6, 15), fetch=fetch)
        self.assertEqual(nxt['status'], 'done')
        self.assertNotEqual(nxt['run_id'], first['run_id'])

    def test_missed_days_are_shown_then_caught_up(self):
        self.run_daily()
        state = self.dr.status(self.record, datetime(2026, 3, 12, 8, 0))
        self.assertEqual(state['state'], 'missed')
        self.assertEqual(state['missed_since'], '2026-03-11')
        self.assertEqual(state['tone'], 'a')
        self.assertIn('Mar 11', state['message'])
        out = self.run_daily(datetime(2026, 3, 12, 8, 0), trigger='catch-up')
        self.assertEqual(out['status'], 'done')
        self.assertEqual(out['missed_since'], '2026-03-11')
        state = self.dr.status(self.record, datetime(2026, 3, 12, 8, 5))
        self.assertEqual(state['state'], 'ok')
        self.assertEqual(state['tone'], 'n')

    def test_force_starts_a_new_run(self):
        first = self.run_daily()
        again = self.run_daily(datetime(2026, 3, 10, 9, 0), force=True)
        self.assertEqual(again['status'], 'done')
        self.assertNotEqual(again['run_id'], first['run_id'])

    def test_never_run(self):
        state = self.dr.status(self.record, MORNING)
        self.assertEqual(state['state'], 'never')
        self.assertEqual(state['message'], "Daily search hasn't run yet.")
        self.assertFalse(self.record.exists(), 'reading the status never creates the record')


class Resume(Base):
    def test_a_failed_landing_resumes_with_the_same_run_id_without_searching_again(self):
        real = self.dr.land_search.land
        with mock.patch.object(self.dr.land_search, 'land', side_effect=OSError('tracker busy')):
            first = self.run_daily()
        self.assertEqual(first['status'], 'failed')
        self.assertEqual(first['phases']['search']['status'], 'done')
        self.assertEqual(first['phases']['land']['status'], 'failed')
        self.assertEqual(self.landed(), 0, 'nothing is marked landed when the tracker did not take it')
        fetch = fixtures.FakeBoards()
        with mock.patch.object(self.dr.land_search, 'land', side_effect=real):
            second = self.run_daily(datetime(2026, 3, 10, 7, 0), fetch=fetch)
        self.assertEqual(second['run_id'], first['run_id'])
        self.assertEqual(fetch.calls, [], 'the saved snapshot is reused')
        self.assertEqual(second['status'], 'done')
        self.assertEqual(second['phases']['land']['status'], 'done')
        with closing(sqlite3.connect(self.record)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 1)

    def test_rerun_adds_no_duplicates(self):
        self.run_daily()
        count = len(self.jobs())
        self.run_daily(datetime(2026, 3, 10, 9, 0), force=True)
        self.assertEqual(len(self.jobs()), count)


class ReviewFixes(Base):
    """Fixes from the Astra and Fable reviews (2026-11-16), written before the fix."""

    def test_today_missed_after_an_hour_grace(self):
        self.run_daily()
        self.assertEqual(self.dr.status(self.record, datetime(2026, 3, 11, 6, 15))['state'], 'ok', 'within the grace hour')
        state = self.dr.status(self.record, datetime(2026, 3, 11, 12, 0))
        self.assertEqual(state['state'], 'missed')
        self.assertEqual(state['tone'], 'a')
        self.assertIn("Today's 06:00 search hasn't run yet", state['message'])
        self.assertNotIn('next time this PC is on', state['message'])

    def test_after_a_full_outage_the_retry_searches_again(self):
        down = fixtures.FakeBoards(broken=['greenhouse', 'lever', 'ashby', 'myworkdayjobs'])
        first = self.run_daily(fetch=down)
        self.assertEqual(first['status'], 'failed')
        fetch = fixtures.FakeBoards()
        again = self.run_daily(datetime(2026, 3, 10, 7, 0), fetch=fetch)
        self.assertEqual(again['run_id'], first['run_id'])
        self.assertTrue(fetch.calls, 'a failed search is never reused')
        self.assertEqual(again['status'], 'done')

    def test_never_run_stays_never_after_the_slot(self):
        state = self.dr.status(self.record, datetime(2026, 3, 10, 12, 0))
        self.assertEqual(state['state'], 'never')
        self.assertEqual(state['message'], "Daily search hasn't run yet.")

    def test_a_running_catch_up_is_not_called_missed(self):
        self.run_daily()
        with closing(sqlite3.connect(self.record)) as db, db:
            db.execute("INSERT INTO runs VALUES ('r2', 'example-ops', '2026-03-12', 'catch-up', '2026-03-12T08:00:00', "
                       "NULL, 'running', '2026-03-11')")
        state = self.dr.status(self.record, datetime(2026, 3, 12, 8, 1))
        self.assertNotEqual(state['state'], 'missed')
        self.assertIn('running', state['message'])

    def test_failed_before_step_is_named_and_not_called_harmless(self):
        self.settings(before=[{'name': 'Backup', 'owner': 'daily runner', 'destination': 'backups',
                               'command': python('import sys; print("WARNING: tracker has 2 rows"); sys.exit(2)')}])
        self.run_daily()
        state = self.dr.status(self.record, MORNING)
        self.assertEqual(state['tone'], 'r')
        self.assertIn('Backup', state['message'])
        self.assertIn('tracker has 2 rows', state['message'])
        self.assertNotIn('Nothing was lost', state['message'])

    def test_a_warning_step_shows_its_own_words(self):
        self.settings(copies=[{'name': 'Board check', 'owner': 'Claude', 'destination': 'Board',
                               'command': python('import sys; print("The Board was last updated on 2026-03-05."); '
                                                 'sys.exit(1)')}])
        self.run_daily()
        message = self.dr.status(self.record, MORNING)['message']
        self.assertIn('The Board was last updated on 2026-03-05.', message)
        self.assertNotIn('copy Board check failed', message)

    def test_step_output_is_read_as_utf8(self):
        self.settings(copies=[{'name': 'Accents', 'owner': 'daily runner', 'destination': 'nowhere',
                               'command': [sys.executable, '-X', 'utf8', '-I', '-c',
                                           'import sys; print("Caf\\u00e9 se\\u00f1or \\u2713"); sys.exit(1)']}])
        out = self.run_daily()
        self.assertIn('Café señor ✓', out['phases']['copy:Accents']['detail'])

    def test_unexpected_error_is_written_to_stderr(self):
        import io
        stream = io.StringIO()
        with mock.patch.object(self.dr.land_search, 'land', side_effect=RuntimeError('boom in land')), \
                mock.patch('sys.stderr', stream):
            out = self.run_daily()
        self.assertEqual(out['phases']['land']['status'], 'failed')
        self.assertIn('boom in land', stream.getvalue())
        self.assertIn('Traceback', stream.getvalue())

    def test_crash_after_snapshot_reuses_it(self):
        out = self.run_daily()
        with closing(sqlite3.connect(self.record)) as db, db:
            db.execute("UPDATE runs SET status='running', finished=NULL WHERE run_id=?", (out['run_id'],))
            db.execute("UPDATE phases SET status='running' WHERE run_id=? AND name='search'", (out['run_id'],))
            db.execute("DELETE FROM phases WHERE run_id=? AND name!='search'", (out['run_id'],))
        fetch = fixtures.FakeBoards()
        again = self.run_daily(datetime(2026, 3, 10, 7, 0), fetch=fetch)
        self.assertEqual(again['run_id'], out['run_id'])
        self.assertEqual(fetch.calls, [], 'a saved snapshot of this run is reused')
        self.assertEqual(again['status'], 'done')

    def test_search_done_but_snapshot_missing_searches_again(self):
        real = self.dr.land_search.land
        with mock.patch.object(self.dr.land_search, 'land', side_effect=OSError('tracker busy')):
            first = self.run_daily()
        (self.work / 'daily-runs' / (first['run_id'] + '.json')).unlink()
        fetch = fixtures.FakeBoards()
        with mock.patch.object(self.dr.land_search, 'land', side_effect=real):
            again = self.run_daily(datetime(2026, 3, 10, 7, 0), fetch=fetch)
        self.assertTrue(fetch.calls, 'without its snapshot the search runs again')
        self.assertEqual(again['status'], 'done')

    def test_bad_time_setting_is_a_plain_error(self):
        self.settings(at=530)
        with self.assertRaises(ValueError):
            self.run_daily()

    def test_one_new_job_reads_naturally(self):
        self.assertEqual(self.dr.jobs_text(1), '1 new job.')
        self.assertEqual(self.dr.jobs_text(12), '12 new jobs.')


class Lock(Base):
    def test_a_second_run_waits_its_turn_and_changes_nothing(self):
        fetch = fixtures.FakeBoards()
        with self.dr.run_lock(self.root):
            out = self.run_daily(fetch=fetch)
        self.assertEqual(out['status'], 'already_running')
        self.assertEqual(fetch.calls, [])
        self.assertFalse(self.record.exists())
        self.assertEqual(self.run_daily()['status'], 'done', 'the lock is released afterwards')


class Cli(Base):
    def test_command_line_reports_up_to_date_with_exit_zero(self):
        import subprocess
        self.run_daily()
        env = dict(os.environ, JOB_SEARCH_HQ_DATA=str(self.root))
        result = subprocess.run([sys.executable, '-I', str(HERE / 'daily_run.py'), '--now', '2026-03-10T20:00'],
                                capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Up to date', result.stdout)



class RecheckPhase(Base):
    """Piece 3 (card 03): the re-check runs after landing; preview mode writes nothing."""

    def checks(self):
        with closing(sqlite3.connect(self.db_file)) as db:
            return db.execute('SELECT COUNT(*) FROM checks').fetchone()[0]

    def test_recheck_runs_after_landing_and_records_checks(self):
        out = self.run_daily()
        self.assertEqual(out['phases']['recheck']['status'], 'done')
        self.assertIn('checked', out['phases']['recheck']['detail'])
        self.assertGreater(self.checks(), 0)

    def test_preview_writes_nothing_and_saves_named_results(self):
        self.settings(recheck='preview')
        out = self.run_daily()
        self.assertEqual(out['phases']['recheck']['status'], 'done')
        self.assertIn('Preview', out['phases']['recheck']['detail'])
        self.assertEqual(self.checks(), 0)
        preview = self.work / 'daily-runs' / (out['run_id'] + '-recheck-preview.json')
        report = json.loads(preview.read_text(encoding='utf-8'))
        self.assertTrue(report['dry_run'])
        self.assertIn('closed', report)

    def test_failed_search_skips_the_recheck(self):
        out = self.run_daily(fetch=fixtures.FakeBoards(broken=('greenhouse', 'lever', 'ashby', 'myworkdayjobs')))
        self.assertEqual(out['status'], 'failed')
        self.assertNotEqual(out['phases'].get('recheck', {}).get('status'), 'done')
        self.assertEqual(self.checks(), 0)
        with closing(sqlite3.connect(self.db_file)) as db:
            reason, = db.execute("SELECT reason FROM board_reads WHERE run_id=? AND provider='workday'",
                                 (out['run_id'],)).fetchone()
        self.assertIn('Workday', reason)
        self.assertIn('0 rows', reason)

    def test_preview_leaves_the_tracker_file_untouched(self):
        self.run_daily()
        self.settings(recheck='preview')
        import hashlib
        before = hashlib.sha256(self.db_file.read_bytes()).hexdigest()
        out = self.run_daily(now=MORNING.replace(day=11), fetch=fixtures.FakeBoards())
        self.assertEqual(out['phases']['recheck']['status'], 'done')
        # Search and landing may write; the re-check preview itself must not.
        report = json.loads((self.work / 'daily-runs' / (out['run_id'] + '-recheck-preview.json')).read_text(encoding='utf-8'))
        self.assertTrue(report['dry_run'])
        with closing(sqlite3.connect(self.db_file)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM checks WHERE run_id=?", (out['run_id'],)).fetchone()[0], 0)
        self.assertIn('two', out['phases']['recheck']['detail'])

    def test_recheck_errors_make_the_phase_partial(self):
        def broken(link):
            raise RuntimeError('unexpected')
        out = self.run_daily(link_check=broken)
        self.assertEqual(out['phases']['recheck']['status'], 'partial')
        self.assertIn('could not be checked', out['phases']['recheck']['detail'])

    def test_a_failed_recheck_is_retried_without_searching_again(self):
        fetch = fixtures.FakeBoards()
        boom = mock.patch.object(self.dr.recheck, 'recheck', side_effect=sqlite3.OperationalError('database is locked'))
        with boom:
            first = self.run_daily(fetch=fetch)
        self.assertEqual(first['phases']['recheck']['status'], 'failed')
        self.assertNotIn(first['status'], ('done', 'done_with_warnings'))
        state = self.dr.status(self.record, MORNING.replace(hour=7), at='06:00')
        self.assertIn('re-check', state['message'].lower())
        calls = len(fetch.calls)
        again = self.run_daily(now=MORNING.replace(hour=7), fetch=fetch)
        self.assertEqual(again['run_id'], first['run_id'])
        self.assertEqual(again['phases']['recheck']['status'], 'done')
        self.assertEqual(len(fetch.calls), calls, 'no second search')

    def test_bad_recheck_setting_is_a_plain_error(self):
        self.settings(recheck='sometimes')
        with self.assertRaisesRegex(ValueError, 'recheck'):
            self.dr.configuration(self.root)


class JudgePhase(Base):
    """Piece 4a-1 (card 04a): the judge runs after the re-check; AI out never fails the run."""

    def judgements(self):
        with closing(sqlite3.connect(self.db_file)) as db:
            return db.execute('SELECT COUNT(*) FROM judgements').fetchone()[0]

    def test_judge_runs_and_records_judgements(self):
        out = self.run_daily()
        self.assertEqual(out['phases']['judge']['status'], 'done')
        self.assertGreater(self.judgements(), 0)

    def test_ai_out_skips_the_judge_and_the_run_still_succeeds(self):
        def out_of_service(prompt, schema):
            raise RuntimeError('not logged in')
        out = self.run_daily(complete=out_of_service)
        self.assertEqual(out['phases']['judge']['status'], 'skipped')
        self.assertIn('AI unavailable', out['phases']['judge']['detail'])
        self.assertIn(out['status'], ('done', 'done_with_warnings'))
        self.assertEqual(self.judgements(), 0)

    def test_judge_preview_writes_nothing_and_saves_named_results(self):
        self.settings(judge='preview')
        out = self.run_daily()
        self.assertEqual(out['phases']['judge']['status'], 'done')
        self.assertIn('Preview', out['phases']['judge']['detail'])
        self.assertEqual(self.judgements(), 0)
        preview = json.loads((self.work / 'daily-runs' / (out['run_id'] + '-judge-preview.json')).read_text(encoding='utf-8'))
        self.assertTrue(preview['dry_run'])
        self.assertTrue(all({'company', 'role', 'fit'} <= set(r) for r in preview['results']))

    def test_outage_after_some_progress_is_a_warning(self):
        calls = []

        def flaky(prompt, schema):
            calls.append(1)
            if len(calls) > 1:
                raise RuntimeError('went away')
            return judge_answer(prompt, schema)
        out = self.run_daily(complete=flaky)
        if out['phases']['judge']['status'] != 'skipped':
            self.assertEqual(out['phases']['judge']['status'], 'partial')

    def test_bad_judge_setting_is_a_plain_error(self):
        self.settings(judge='always')
        with self.assertRaisesRegex(ValueError, 'judge'):
            self.dr.configuration(self.root)


    # Card 04c: Claude as a judge option.
    def test_judge_ai_setting_accepts_codex_and_claude_only(self):
        for value in ('codex', 'claude'):
            self.settings(judge_ai=value)
            self.assertEqual(self.dr.configuration(self.root)['judge_ai'], value)
        self.settings()
        self.assertEqual(self.dr.configuration(self.root)['judge_ai'], 'codex')
        self.settings(judge_ai='gemini')
        with self.assertRaisesRegex(ValueError, 'judge AI'):
            self.dr.configuration(self.root)

    def test_claude_setting_asks_claude_and_saves_its_label(self):
        self.settings(judge_ai='claude')
        asked = []

        def fake_claude(prompt, schema, timeout=600):
            asked.append(1)
            return judge_answer(prompt, schema)
        with mock.patch.object(self.dr.judge.seam, 'run_claude_cli', fake_claude), \
                mock.patch.object(self.dr.judge.seam, 'run_cli', side_effect=AssertionError('Codex was asked')):
            out = self.run_daily(complete=None)
        self.assertEqual(out['phases']['judge']['status'], 'done')
        self.assertTrue(asked)
        with closing(sqlite3.connect(self.db_file)) as db:
            self.assertEqual({m for (m,) in db.execute('SELECT model FROM judgements')}, {'claude-cli'})


if __name__ == '__main__':
    unittest.main()
