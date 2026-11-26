# Fictional fixtures and settings-aware expectations.
# Activation updates: Codex - Activation Builder, 2026-11-07.
"""Synthetic integration checks; no real workbook or AI calls."""
import hashlib
import http.client
import importlib.util
import json
import os
import re
from contextlib import closing
from pathlib import Path
import secrets
import socket
import sqlite3
import ssl
import subprocess
import tempfile
import threading
import time
import sys
import shutil
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlencode, urlsplit

import openpyxl

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('hq_pc', HERE / 'server.py')
hq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hq)


class StageHistory(unittest.TestCase):
    def test_replays_only_recorded_days_and_surviving_jobs(self):
        store = hq.daily_run.land_search.store
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.executescript("""
            CREATE TABLE jobs (id INTEGER PRIMARY KEY, sheet TEXT, row INTEGER, status TEXT, first_seen TEXT);
            CREATE TABLE moves (id INTEGER PRIMARY KEY, job_id INTEGER, from_status TEXT, to_status TEXT, at TEXT);
        """)
        db.executemany('INSERT INTO jobs VALUES (?,?,?,?,?)', [
            (1, 'Example jobs', 2, 'Applied', '2026-01-02'),
            (2, 'Example jobs', 3, 'Skipped', '2026-01-05'),
            (3, 'Example jobs', 4, 'Offer', None),
        ])
        db.executemany('INSERT INTO moves VALUES (?,?,?,?,?)', [
            (1, 1, 'Tracked', 'Review', '2026-01-05T22:00:00+00:00'),
            (2, 1, 'Review', 'Apply', '2026-01-06T02:00:00+00:00'),
            (3, 1, 'Apply', 'Applied', '2026-01-06T06:00:00+00:00'),
            (4, 99, 'Applied', 'Rejected', '2026-01-01T12:00:00+00:00'),
        ])
        job_keys = {'Example jobs|2', 'Example jobs|3', 'Example jobs|4'}
        history, activity = hq.stage_history(db, store.stages.STAGES, '-06:00', job_keys)
        stages = dict(tracked=0, review=0, apply=0, applied=0, interview=0, offer=0, rejected=0, skipped=0)
        self.assertEqual(history, [
            dict(stages, date='2026-01-02', total=1, tracked=1),
            dict(stages, date='2026-01-05', total=2, apply=1, skipped=1),
            dict(stages, date='2026-01-06', total=2, applied=1, skipped=1),
        ])
        self.assertEqual(activity, [
            dict(key='Example jobs|2', date='2026-01-06', status='Applied'),
            dict(key='Example jobs|2', date='2026-01-05', status='Apply'),
            dict(key='Example jobs|2', date='2026-01-05', status='Review'),
            dict(key='Example jobs|3', date='2026-01-05'),
            dict(key='Example jobs|2', date='2026-01-02'),
        ])
        utc, _ = hq.stage_history(db, store.stages.STAGES, '+00:00', job_keys)
        self.assertEqual(utc[1], dict(stages, date='2026-01-05', total=2, review=1, skipped=1))


class RequestLimits(unittest.TestCase):
    def start_server(self, deadline=1, max_workers=2):
        self.started = threading.Semaphore(0)
        self.finished = threading.Semaphore(0)
        self.login_calls = []
        app = SimpleNamespace(host='', origin='')

        def login(password):
            self.login_calls.append(password)
            return None, 401

        app.login = login
        started, finished = self.started, self.finished

        class Handler(hq.handler(app)):
            def setup(self):
                super().setup()
                started.release()

        class Server(hq.BoundedHTTPServer):
            def process_request_thread(self, request, client_address):
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    finished.release()

        self.server = Server(('127.0.0.1', 0), Handler,
                             request_deadline_seconds=deadline, max_workers=max_workers)
        self.port = self.server.server_port
        app.host = f'127.0.0.1:{self.port}'
        app.origin = f'http://{app.host}'
        self.app = app
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def cleanup():
            self.server.shutdown()
            self.server.server_close()
            thread.join(timeout=2)

        self.addCleanup(cleanup)

    def assert_login_page(self):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            c.request('GET', '/login', headers={'Host': self.app.host})
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            self.assertIn(b'Sign in', r.read())
        finally:
            c.close()

    def test_slow_request_line_headers_and_login_body_have_absolute_deadline(self):
        self.start_server()
        prefixes = (
            b'GET /login HTTP/1.1',
            b'GET /login HTTP/1.1\r\nX-Slow: ',
            (f'POST /api/login HTTP/1.1\r\nHost: {self.app.host}\r\n'
             f'Origin: {self.app.origin}\r\nContent-Type: application/x-www-form-urlencoded\r\n'
             'Content-Length: 100\r\n\r\npassword=').encode(),
        )
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                stop = threading.Event()
                sent = []
                with socket.create_connection(('127.0.0.1', self.port), timeout=3) as client:
                    began = time.monotonic()
                    client.sendall(prefix)
                    self.assertTrue(self.started.acquire(timeout=2))

                    def trickle():
                        while not stop.wait(0.05):
                            try:
                                client.sendall(b'x')
                                sent.append(1)
                            except OSError:
                                return

                    writer = threading.Thread(target=trickle, daemon=True)
                    writer.start()
                    try:
                        try:
                            self.assertEqual(client.recv(1), b'')
                        except ConnectionResetError:
                            pass
                        self.assertLess(time.monotonic() - began, 2.5)
                        self.assertGreaterEqual(len(sent), 3)
                    finally:
                        stop.set()
                        writer.join(timeout=1)
                self.assertTrue(self.finished.acquire(timeout=2))
        self.assertEqual(self.login_calls, [])
        self.assert_login_page()
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            c.request('POST', '/api/login', 'password=fictional', headers={
                'Host': self.app.host, 'Origin': self.app.origin,
                'Content-Type': 'application/x-www-form-urlencoded',
            })
            r = c.getresponse()
            self.assertEqual(r.status, 401)
            r.read()
            self.assertEqual(self.login_calls, ['fictional'])
        finally:
            c.close()

    def test_worker_cap_closes_extra_connection_and_releases_slots(self):
        self.start_server(deadline=5, max_workers=2)
        clients = []
        try:
            for _ in range(2):
                client = socket.create_connection(('127.0.0.1', self.port), timeout=2)
                clients.append(client)
                self.assertTrue(self.started.acquire(timeout=2))
            with socket.create_connection(('127.0.0.1', self.port), timeout=1) as extra:
                began = time.monotonic()
                try:
                    self.assertEqual(extra.recv(1), b'')
                except ConnectionResetError:
                    pass
                self.assertLess(time.monotonic() - began, 1)
            self.assertFalse(self.started.acquire(blocking=False))
            clients.pop().close()
            self.assertTrue(self.finished.acquire(timeout=2))
            # One slow connection remains open while the released slot serves normally.
            self.assert_login_page()
        finally:
            for client in clients:
                client.close()



class SaveNoticeUI(unittest.TestCase):
    # Offline UI regressions.
    def test_save_notice_lifecycle(self):
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as p:
            browser = p.webkit.launch()
            page = browser.new_page(reduced_motion='reduce')
            page.route('**/*', lambda route: route.abort())
            tracker = (HERE.parent / 'tracker' / 'index.html').read_text(encoding='utf-8')
            css = tracker.split('<style>', 1)[1].split('</style>', 1)[0]
            css += (HERE / 'hosted' / 'pc.css').read_text(encoding='utf-8')
            page.set_content('<style>' + css + '</style><header class="top"></header>')
            # Deterministic time without sleeping, network, workbook writes or a provider.
            page.add_script_tag(content="""
                let tick=0, timerID=0; const timers=new Map();
                window.setTimeout=(fn,ms=0)=>{timers.set(++timerID,{fn,at:tick+ms});return timerID};
                window.clearTimeout=id=>timers.delete(id);
                window.advance=ms=>{const end=tick+ms;while(true){
                    const next=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];
                    if(!next)break;tick=next[1].at;timers.delete(next[0]);next[1].fn();
                }tick=end};
                window.writes=[]; window.offline=false; window.dismissed=[];
            """)
            page.add_script_tag(content=(HERE / 'hosted' / 'pc-data.js').read_text(encoding='utf-8'))
            page.evaluate("""() => {
                pcFetch=async(path,options)=>{if(offline)throw Error('offline');
                    if(options){const {id}=JSON.parse(options.body);dismissed.push(id);
                        writes=writes.map(w=>w.id===id?{...w,status:'cancelled'}:w);return {}};
                    return {writes}};
                reloadJobs=async()=>{};
            }""")
            def poll(items):
                page.evaluate('(items)=>{writes=items;return pcWrites()}', items)

            saved = dict(id='fictional-1', label='<Example>', status='saved', message='Saved to your spreadsheet.')
            poll([])  # B5: the page opens first; only saves finished after that are announced.
            poll([saved])
            box = page.locator('#pc-saves')
            expect(box).to_be_visible()
            expect(box).to_have_attribute('role', 'status')
            expect(box).to_have_attribute('aria-live', 'polite')
            expect(box).to_have_class('banner n')
            expect(box).to_contain_text('<Example>: Saved to your spreadsheet.')
            self.assertEqual(box.locator('example').count(), 0, 'Untrusted labels must stay text')
            page.evaluate('advance(4000)')
            poll([saved])
            page.evaluate('advance(1000)')
            expect(box).to_be_hidden()
            poll([saved])
            expect(box).to_be_hidden()
            # A new message gets a fresh five seconds; pointer/focus pause expiry.
            waiting = dict(id='fictional-2', label='Example', status='waiting', message='Waiting for Excel')
            poll([waiting])
            page.evaluate('advance(4000)')
            applying = {**waiting, 'status': 'applying', 'message': 'Saving now'}
            poll([applying])
            page.evaluate('advance(1000)')
            expect(box).to_be_visible()
            box.dispatch_event('pointerenter')
            page.evaluate('advance(6000)')
            expect(box).to_be_visible()
            box.dispatch_event('pointerleave')
            page.evaluate('advance(5000)')
            expect(box).to_be_hidden()
            poll([waiting])
            box.locator('button').focus()
            page.evaluate('advance(6000)')
            expect(box).to_be_visible()
            page.evaluate('document.activeElement.blur();advance(5000)')
            expect(box).to_be_hidden()
            # An older failure must remain visible even alongside a newer waiting edit.
            for status in ('conflict', 'needs_check', 'failed'):
                error = dict(id='error-'+status, label='Example', status=status, message='Check spreadsheet')
                poll([waiting, error])
                expect(box).to_have_attribute('role', 'alert')
                expect(box).to_have_class('banner')
                page.evaluate('advance(10000)')
                expect(box).to_be_visible()
                expect(box).to_contain_text('1 edit waiting or saving')
                box.locator('button').click()
                self.assertEqual(page.evaluate('dismissed.at(-1)'), error['id'])
            poll([waiting, {**waiting, 'id': 'fictional-3'}])
            expect(box).to_contain_text('2 edits waiting or saving')
            self.assertEqual(page.locator('#pc-saves').count(), 1)
            page.evaluate('offline=true;pcWrites()')
            expect(box).to_contain_text('Server unreachable')
            page.evaluate('advance(10000)')
            expect(box).to_be_visible()
            box.get_by_role('button', name='Dismiss', exact=True).click()
            page.evaluate('pcWrites()')
            expect(box).to_be_hidden()
            page.evaluate('offline=false;writes=[];pcWrites()')
            expect(box).to_be_hidden()
            # Normal motion fades after the delay; reduced motion above hides immediately.
            page.mouse.move(0, 0)
            box.dispatch_event('pointerleave')
            page.evaluate('advance(0)')
            page.emulate_media(reduced_motion='no-preference')
            poll([saved])
            page.evaluate('advance(5000)')
            expect(box).to_have_class('banner n pc-save-fade')
            page.evaluate('advance(180)')
            expect(box).to_be_hidden()
            browser.close()


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Self-contained page fixture.
        fixture = tempfile.TemporaryDirectory(prefix='hq-pc-page-test-')
        cls.addClassCleanup(fixture.cleanup)
        dashboard = Path(fixture.name) / 'tools' / 'hq-dashboard'
        tracker = dashboard.parent / 'tracker'
        dashboard.mkdir(parents=True)
        tracker.mkdir()
        # Include the fixture's settings dependencies.
        for name in ('settings.example.json', 'settings.json'):
            source = HERE.parents[1] / name
            if source.is_file():
                shutil.copyfile(source, Path(fixture.name) / name)
        # Keep the server's existing HERE-based module loading in the fixture tree.
        for source, target in ((HERE, dashboard), (HERE.parent / 'tracker', tracker)):
            for path in [*source.glob('*.py'), *source.glob('*.json')]:  # stages.json: the store reads it (step 05a)
                shutil.copyfile(path, target / path.name)
        page_spec = importlib.util.spec_from_file_location('pc_page_fixture', HERE / 'port_tracker.py')
        page = importlib.util.module_from_spec(page_spec)
        page_spec.loader.exec_module(page)
        # Read tracked UI sources, but generate the real PC page only in temporary storage.
        page.HERE = dashboard
        with mock.patch.object(sys, 'argv', ['port_tracker.py', '--backend', 'pc']):
            page.main()
        paths = mock.patch.object(hq, 'HERE', dashboard)
        paths.start()
        cls.addClassCleanup(paths.stop)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hq-pc-test-')
        self.state = Path(self.tmp.name)
        self.password = secrets.token_urlsafe(24)
        salt = secrets.token_hex(16)
        (self.state / 'password.json').write_text(json.dumps({'salt': salt, 'hash': hq.password_hash(self.password, salt)}))
        self.book = self.state / 'fictional.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Fictional jobs'
        ws.append(['#', 'Company', 'Role', 'My status', 'Date applied', 'Notes', 'Link'])
        ws.append([1, 'Example Company', 'Example Engineer', 'Review', '', 'Example only', 'https://example.com/job'])
        wb.save(self.book)
        wb.close()
        self.app = hq.App(self.state, self.book, 'http://127.0.0.1')
        self.app.scan_log_dir = self.state
        self.app.cv_work = self.state
        self.server = hq.BoundedHTTPServer(('127.0.0.1', 0), hq.handler(self.app))
        self.port = self.server.server_port
        self.app.origin = f'http://127.0.0.1:{self.port}'
        self.app.host = f'127.0.0.1:{self.port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ''

    def tearDown(self):
        self.app.queue.stop()
        self.app.ai.stop()
        self.app.search.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, method, path, data=None, origin=True, host=None):
        headers = {'Host': host or self.app.host, 'Cookie': self.cookie}
        if origin:
            headers['Origin'] = origin if isinstance(origin,str) else self.app.origin
        if path == '/api/login':
            body = urlencode({'password': data or ''})
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        else:
            body = json.dumps(data) if data is not None else None
            headers['Content-Type'] = 'application/json'
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        c.request(method, path, body, headers)
        r = c.getresponse()
        result = r.status, dict(r.getheaders()), r.read()
        c.close()
        return result

    def login(self):
        status, headers, _ = self.request('POST', '/api/login', self.password)
        self.assertEqual(status, 303)
        self.cookie = headers['Set-Cookie'].split(';')[0]
        return headers

    def data(self):
        status, _, body = self.request('GET', '/api/data')
        self.assertEqual(status, 200)
        return json.loads(body)

    def edit(self, **extra):
        d = self.data()
        p = dict(sheet='Fictional jobs', row=2, version=d['version'], expect={'Company': 'Example Company', 'Role': 'Example Engineer'}, note='Saved fictional note',requestId=str(uuid.uuid4()))
        p.update(extra)
        return self.request('POST', '/api/update', p)

    def dismiss_latest(self):
        item = self.app.queue.recent()[0]
        self.assertEqual(self.request('POST','/api/writes/dismiss',{'id':item['id']})[0],200)

    def test_queue_survives_restart_and_retries_once(self):
        self.login()
        lock = self.book.with_name('~$' + self.book.name)
        lock.touch()
        request_id = str(uuid.uuid4())
        self.assertEqual(self.edit(note='Wait for Excel',requestId=request_id)[0],202)
        self.assertEqual(self.edit(note='Second edit blocked')[0],400)
        app2 = hq.App(self.state,self.book,self.app.origin)
        self.assertEqual(app2.queue.get(request_id)['status'],'waiting')
        lock.unlink()
        app2.queue.process_one()
        self.assertEqual(app2.queue.get(request_id)['status'],'saved')
        self.assertEqual(self.data()['jobs'][0]['Notes'],'Wait for Excel')
        version = self.app.tracker.version()
        app2.queue.process_one()
        self.assertEqual(self.app.tracker.version(),version)

    def test_queue_changed_workbook_requires_confirmation(self):
        self.login()
        lock = self.book.with_name('~$' + self.book.name)
        lock.touch()
        self.assertEqual(self.edit(note='Pending note')[0],202)
        lock.unlink()
        wb = openpyxl.load_workbook(self.book)
        wb.active.cell(2,6).value='Edited on PC'
        wb.save(self.book)
        wb.close()
        self.app.queue.process_one()
        self.assertEqual(self.app.queue.recent()[0]['status'],'conflict')
        self.assertEqual(self.data()['jobs'][0]['Notes'],'Edited on PC')
        self.dismiss_latest()

    def test_idempotent_add_and_interrupted_write(self):
        self.login()
        p={'sheet':'Fictional jobs','Company':'Another Example','Role':'Test','version':self.data()['version'],'requestId':str(uuid.uuid4())}
        self.assertEqual(self.request('POST','/api/add',p)[0],200)
        self.assertEqual(self.request('POST','/api/add',p)[0],200)
        self.assertEqual(len(self.data()['jobs']),2)
        p['Company']='Different payload'
        self.assertEqual(self.request('POST','/api/add',p)[0],400)
        with self.app.queue.connect() as db, db:
            db.execute("UPDATE edits SET status='applying' WHERE id=?",(p['requestId'],))
        app2=hq.App(self.state,self.book,self.app.origin)
        self.assertEqual(app2.queue.recent()[0]['status'],'needs_check')
        app2.queue.process_one()
        self.assertEqual(len(self.data()['jobs']),2)

    def test_data_carries_the_daily_run_banner(self):
        # Piece 2c: the run record sits next to the tracker; none yet means "hasn't run yet".
        self.login()
        banner = self.data()['daily_run']
        self.assertEqual(banner['state'], 'never')
        self.assertEqual(banner['message'], "Daily search hasn't run yet.")
        self.assertFalse((self.book.parent / 'daily-runs.sqlite3').exists())

    def test_data_reports_paused_daily_search_before_first_run(self):
        self.login()
        load = hq.hq_settings.load
        with mock.patch.object(hq.hq_settings, 'load', side_effect=lambda: dict(load(), daily_run={'enabled': False})):
            banner = self.data()['daily_run']
        self.assertEqual(banner['tone'], 'n')
        self.assertEqual(banner['message'], 'Daily search is paused.')
        self.assertIsNone(banner['last_success'])

    def test_data_handles_non_mapping_daily_run(self):
        self.login()
        load = hq.hq_settings.load
        for config in (None, False, 'paused', ['06:00']):
            with self.subTest(config=config), mock.patch.object(
                    hq.hq_settings, 'load', side_effect=lambda: dict(load(), daily_run=config)):
                self.assertEqual(self.data()['daily_run']['state'], 'never')

    def cv_trim_fixture(self, roles=4, bullets=7, tail=''):
        posting_id = str(uuid.uuid4())
        lines, claims, entries = ['## Experience'], [], {}
        for role in range(roles):
            title = f'Analyst | Fictional Studio {role} | 2024'
            lines.append('### ' + title)
            for bullet in range(bullets):
                key = f'E-{100 + role * bullets + bullet:03}'
                claim = (f'Checked sample {role}.{bullet} and prepared fictional records for the monthly reporting '
                         'exercise and comprehensive documentation reconciliation.')
                lines.append('- ' + claim)
                claims.append({'text': claim, 'evidence': [key]})
                entries[key] = {'text': claim, 'roles': [title]}
        content = '\n'.join(lines) + '\n' + tail
        record = {'role': 'Fictional analyst', 'reviewed': True, 'review': {'result': 'clear'},
                  'fit': {'cv_markdown': content, 'cv_claims': claims, 'sources': list(entries)}}
        self.app.ai.bank_loader = lambda: {'entries': entries}
        with self.app.ai.connect() as db, db:
            db.execute('INSERT INTO postings VALUES (?, ?)', (posting_id, json.dumps(record)))
        return posting_id, record

    def cv_trim_request(self, action, posting_id, **fields):
        status, _, body = self.request('POST', '/api/ai/cv-trim/' + action, {'id': posting_id, **fields})
        return status, json.loads(body)

    def test_cv_page_warning_is_only_for_long_drafts(self):
        self.login()
        short, _ = self.cv_trim_fixture(roles=1, bullets=1)
        long, _ = self.cv_trim_fixture()
        status, _, body = self.request('GET', '/api/ai/state')
        self.assertEqual(status, 200, body)
        fits = {posting['_id']: posting['fit'] for posting in json.loads(body)['postings']}
        self.assertNotIn('cv_page_warning', fits[short])
        self.assertRegex(fits[long]['cv_page_warning'], r'^About \d+\.\d pages\. A CV should fit on one\.$')

    def test_cv_trim_preview_is_read_only_and_apply_deletes_exactly_its_lines(self):
        self.login()
        posting_id, record = self.cv_trim_fixture()
        status, preview = self.cv_trim_request('preview', posting_id)
        self.assertEqual(status, 200, preview)
        self.assertTrue(preview['removed'])
        self.assertLessEqual(preview['pages'], 1)
        self.assertEqual(self.app.ai.posting(posting_id), record)
        status, reply = self.cv_trim_request('apply', posting_id, draftHash=preview['draftHash'],
                                             previewHash=preview['previewHash'])
        self.assertEqual(status, 200, reply)
        saved = self.app.ai.posting(posting_id)
        expected = record['fit']['cv_markdown'].splitlines()
        for line in preview['removed']:
            expected.remove(line)
        self.assertEqual(saved['fit']['cv_markdown'].splitlines(), expected)
        claims = [claim for claim in record['fit']['cv_claims'] if claim['text'] in saved['fit']['cv_markdown']]
        self.assertEqual(saved['fit']['cv_claims'], claims)
        self.assertEqual(saved['fit']['sources'], [claim['evidence'][0] for claim in claims])
        self.assertFalse(saved['reviewed'])
        self.assertIsNone(saved['review'])

    def test_cv_trim_apply_refuses_a_changed_draft(self):
        self.login()
        posting_id, record = self.cv_trim_fixture()
        status, preview = self.cv_trim_request('preview', posting_id)
        self.assertEqual(status, 200, preview)
        with self.app.ai.connect() as db, db, self.app.ai.edit_posting(db, posting_id) as latest:
            latest['fit']['cv_markdown'] += '\n'
        changed = self.app.ai.posting(posting_id)
        status, reply = self.cv_trim_request('apply', posting_id, draftHash=preview['draftHash'],
                                             previewHash=preview['previewHash'])
        self.assertEqual(status, 400, reply)
        self.assertIn('changed', reply['error'])
        self.assertEqual(self.app.ai.posting(posting_id), changed)

    def test_cv_trim_apply_refuses_a_draft_that_still_does_not_fit(self):
        self.login()
        posting_id, record = self.cv_trim_fixture(tail='## Training\n' + 'Fictional course.\n' * 72)
        status, preview = self.cv_trim_request('preview', posting_id)
        self.assertEqual(status, 200, preview)
        self.assertGreater(preview['pages'], 1)
        status, reply = self.cv_trim_request('apply', posting_id, draftHash=preview['draftHash'],
                                             previewHash=preview['previewHash'])
        self.assertEqual(status, 400, reply)
        self.assertIn('still', reply['error'])
        self.assertEqual(self.app.ai.posting(posting_id), record)

    def test_cv_trim_routes_require_sign_in(self):
        posting_id, record = self.cv_trim_fixture()
        for action in ('preview', 'apply'):
            with self.subTest(action=action):
                self.assertEqual(self.cv_trim_request(action, posting_id)[0], 401)
        self.assertEqual(self.app.ai.posting(posting_id), record)

    def test_cv_trim_refuses_tag_lines(self):
        self.login()
        posting_id, record = self.cv_trim_fixture(tail='[DRAFT]\n')
        status, reply = self.cv_trim_request('preview', posting_id)
        self.assertEqual(status, 400, reply)
        self.assertIn('came back with a marker line', reply['error'])
        self.assertEqual(self.app.ai.posting(posting_id), record)

    def test_cv_trim_system_exit_is_a_normal_error(self):
        self.login()
        posting_id, record = self.cv_trim_fixture()
        loader = mock.Mock(side_effect=SystemExit('The original lines could not be preserved.'))
        with mock.patch.dict(self.app.ai.preview_cv_trim.__globals__, _load_cv_onepage=loader):
            status, reply = self.cv_trim_request('preview', posting_id)
        self.assertEqual(status, 400, reply)
        self.assertEqual(reply['error'], 'The original lines could not be preserved.')
        self.assertEqual(self.app.ai.posting(posting_id), record)

    def test_cv_trim_does_not_apply_a_different_proposal(self):
        self.login()
        posting_id, record = self.cv_trim_fixture()
        status, preview = self.cv_trim_request('preview', posting_id)
        self.assertEqual(status, 200, preview)
        status, reply = self.cv_trim_request('apply', posting_id, draftHash=preview['draftHash'],
                                             previewHash='0' * 64)
        self.assertEqual(status, 400, reply)
        self.assertIn('proposed trim changed', reply['error'])
        self.assertEqual(self.app.ai.posting(posting_id), record)

    def test_cv_trim_preserves_evidence_for_kept_headings_and_cover_letter(self):
        self.login()
        posting_id, _ = self.cv_trim_fixture()
        tasks = self.app.ai
        bank = tasks.bank_loader()
        title = 'Coordinator | Fictional Studio | 2022'
        bank['entries']['E-901'] = {'text': 'Fictional position.', 'roles': [title]}
        bank['entries']['E-902'] = {'text': 'Fictional letter.'}
        letter_claims = [{'text': 'Fictional letter.', 'evidence': ['E-902']}]
        with tasks.connect() as db, db, tasks.edit_posting(db, posting_id) as record:
            fit = record['fit']
            fit['cv_markdown'] += '### ' + title + '\n'
            fit['sources'] += ['E-901', 'E-902']
            fit.update(cover_letter_markdown='Fictional letter.', letter_claims=letter_claims)
        status, preview = self.cv_trim_request('preview', posting_id)
        self.assertEqual(status, 200, preview)
        status, reply = self.cv_trim_request('apply', posting_id, draftHash=preview['draftHash'],
                                             previewHash=preview['previewHash'])
        self.assertEqual(status, 200, reply)
        fit = tasks.posting(posting_id)['fit']
        self.assertIn('E-901', fit['cv_sources'])
        self.assertNotIn('E-902', fit['cv_sources'])
        self.assertEqual(fit['letter_sources'], ['E-902'])
        self.assertEqual(fit['letter_claims'], letter_claims)

    def test_cv_trim_refuses_when_remaining_claims_no_longer_match_the_bank(self):
        self.login()
        posting_id, record = self.cv_trim_fixture()
        status, preview = self.cv_trim_request('preview', posting_id)
        self.assertEqual(status, 200, preview)
        for entry in self.app.ai.bank_loader()['entries'].values():
            entry['text'] = 'Different fictional evidence.'
        status, reply = self.cv_trim_request('apply', posting_id, draftHash=preview['draftHash'],
                                             previewHash=preview['previewHash'])
        self.assertEqual(status, 400, reply)
        self.assertIn('evidence', reply['error'])
        self.assertEqual(self.app.ai.posting(posting_id), record)

    def test_cv_trim_keeps_sources_for_generated_skills_and_fixed_lines(self):
        self.login()
        posting_id, _ = self.cv_trim_fixture()
        tasks = self.app.ai
        bank = tasks.bank_loader()
        bank['entries'].update({
            'E-901': {'text': 'Fictional spreadsheet practice.', 'skills': ['Spreadsheets']},
            'E-902': {'text': 'Fictional diploma.'},
            'E-903': {'text': 'Fictional course.'},
        })
        fixed = {'name': 'Education', 'lines': [
            {'from': 'text', 'entries': ['E-902', 'E-903'], 'join': ' | '}]}
        fixed_claim = {'text': 'Fictional diploma. | Fictional course.', 'kind': 'bullet',
                       'evidence': ['E-902', 'E-903']}
        with tasks.connect() as db, db, tasks.edit_posting(db, posting_id) as record:
            fit = record['fit']
            fit['cv_markdown'] += ('## Skills\n- **Tools:** Spreadsheets\n## Education\n'
                                   + fixed_claim['text'] + '\n')
            fit['sources'] += ['E-901', 'E-902', 'E-903']
            fit['cv_claims'].append(fixed_claim)
        settings = tasks.apply_cv_trim.__func__.__globals__['SETTINGS']
        with mock.patch.dict(settings, cv_fixed_sections=[fixed], cv_skills_placement='end',
                             cv_skill_groups={'Tools': ['Spreadsheets']}):
            status, preview = self.cv_trim_request('preview', posting_id)
            self.assertEqual(status, 200, preview)
            status, reply = self.cv_trim_request('apply', posting_id, draftHash=preview['draftHash'],
                                                 previewHash=preview['previewHash'])
        self.assertEqual(status, 200, reply)
        fit = tasks.posting(posting_id)['fit']
        self.assertEqual(fit['cv_sources'][-3:], ['E-901', 'E-902', 'E-903'])
        self.assertIn(fixed_claim, fit['cv_claims'])

    def test_link_and_draft_preserve_each_others_updates_in_both_orders(self):
        self.login()
        job_id = self.data()['jobs'][0]['_id']
        tasks = self.app.ai
        tasks.bank_loader = lambda: {'rubric_version': 'v3.2', 'rules': 'Fictional evidence only.',
                                     'entries': {'E-999': {'text': 'Fictional letter.'}}}
        letter = {'cover_letter_markdown': 'Fictional letter.', 'sources': ['E-999'],
                  'claims': [{'text': 'Fictional letter.', 'evidence': ['E-999']}]}
        for first in ('link', 'draft'):
            with self.subTest(first=first):
                posting_id = str(uuid.uuid4())
                record = {'jobId': '', 'text': 'Fictional posting.', 'reviewed': True, 'review': {},
                          'fit': {'cv_markdown': 'Fictional CV.'}}
                with tasks.connect() as db, db:
                    db.execute('INSERT INTO postings VALUES (?, ?)', (posting_id, json.dumps(record)))
                task_id = tasks.submit({'kind': 'letter', 'postingId': posting_id, 'withoutSentiment': True,
                                        'requestId': str(uuid.uuid4())})

                def link():
                    status, _, body = self.request('POST', '/api/ai/link', {'id': posting_id, 'jobId': job_id})
                    self.assertEqual(status, 200, body)

                if first == 'link':
                    def runner(*args):
                        link()
                        return letter

                    tasks.runner = runner
                    tasks.process_one()
                else:
                    tasks.runner = lambda *args: letter
                    app_data = self.app.data

                    def data_after_draft():
                        tasks.process_one()
                        return app_data()

                    with mock.patch.object(self.app, 'data', side_effect=data_after_draft):
                        link()
                task = next(task for task in tasks.list_tasks() if task['id'] == task_id)
                self.assertEqual(task['status'], 'done', task['message'])
                saved = tasks.posting(posting_id)
                self.assertEqual(saved['jobId'], job_id)
                self.assertEqual(saved['fit']['cover_letter_markdown'], 'Fictional letter.')
                self.assertEqual(saved['fit']['cv_markdown'], 'Fictional CV.')
                self.assertFalse(saved['reviewed'])

    def test_authentication_and_logout(self):
        self.assertEqual(self.request('GET', '/')[0], 303)
        self.assertEqual(self.request('GET', '/api/data')[0], 401)
        headers = self.login()
        for flag in ('Secure', 'HttpOnly', 'SameSite=Strict'):
            self.assertIn(flag, headers['Set-Cookie'])
        self.assertEqual(len(self.data()['jobs']), 1)
        self.assertEqual(self.request('GET', '/')[0], 200)
        self.assertEqual(self.request('POST', '/api/logout', {})[0], 200)
        self.assertEqual(self.request('GET', '/api/data')[0], 401)

    def test_host_origin_and_route_boundaries(self):
        self.login()
        self.assertEqual(self.request('GET', '/api/data', host='evil.example')[0], 404)
        self.assertEqual(self.request('POST', '/api/update', {}, origin=False)[0], 403)
        self.assertEqual(self.request('POST', '/api/update', {}, origin='null')[0],403)
        self.assertEqual(self.request('POST', '/api/update', {}, origin='https://evil.example')[0],403)
        self.assertEqual(self.request('GET','/login')[1]['Referrer-Policy'],'same-origin')
        for path in ('/server.py', '/../../work/applications-tracker.xlsx', '/.env', '/api/exec'):
            self.assertEqual(self.request('GET', path)[0], 404)

    def test_edits_stale_rows_and_excel_lock(self):
        self.login()
        self.assertEqual(self.edit()[0], 200)
        self.assertEqual(self.data()['jobs'][0]['Notes'], 'Saved fictional note')
        self.assertEqual(self.edit(version='stale')[0], 409)
        self.dismiss_latest()
        self.assertEqual(self.edit(expect={'Company': 'Wrong company', 'Role': 'Example Engineer'})[0], 409)
        self.dismiss_latest()
        lock = self.book.with_name('~$' + self.book.name)
        lock.touch()
        self.assertEqual(self.edit(note='Do not save')[0], 202)
        lock.unlink()
        self.assertEqual(self.data()['jobs'][0]['Notes'], 'Saved fictional note')

    def test_formula_and_long_text_rejected(self):
        self.login()
        self.assertEqual(self.edit(note='=1+1')[0], 400)
        self.assertEqual(self.edit(note='x' * 32768)[0], 400)
        self.assertEqual(self.edit(note='😀' * 17000)[0], 400)

    def test_add_formula_after_whitespace_rejected(self):
        self.login()
        expected = None
        for note in ('=1+1', ' \t=1+1', '\u2003\u00a0=1+1', '\x01\t=1+1', '\u200b\ufeff=1+1'):
            with self.subTest(note=repr(note)):
                p = {'sheet': 'Fictional jobs', 'Company': 'Second Example', 'Role': 'Test role',
                     'Notes': note, 'version': self.data()['version'], 'requestId': str(uuid.uuid4())}
                status, _, raw = self.request('POST', '/api/add', p)
                self.assertEqual(status, 400)
                body = json.loads(raw)
                self.assertEqual(body['write']['status'], 'failed')
                # Compare the response body except per-request identifiers and timestamps.
                for key in ('id', 'created', 'updated'):
                    body['write'].pop(key)
                expected = body if expected is None else expected
                self.assertEqual(body, expected)
                self.assertEqual(len(self.data()['jobs']), 1)

    def test_tracker_formula_text_saved_as_string(self):
        tracker = self.app.tracker
        for note in ('=1+1', ' \t=1+1', '\u2003=1+1', '\u200b=1+1', ' Ordinary note '):
            with self.subTest(note=repr(note)):
                row, _ = tracker.add({'sheet': 'Fictional jobs', 'Company': 'Second Example',
                                      'Role': 'Test role', 'Notes': note})
                tracker.update({'sheet': 'Fictional jobs', 'row': 2, 'note': note})
                wb = openpyxl.load_workbook(self.book)
                try:
                    self.assertEqual(wb.active.cell(row, 6).value, note.strip())
                    self.assertEqual(wb.active.cell(row, 6).data_type, 's')
                    self.assertEqual(wb.active.cell(2, 6).value, note)
                    self.assertEqual(wb.active.cell(2, 6).data_type, 's')
                finally:
                    wb.close()

    def test_login_oversize_rejected_before_body_read(self):
        # Send headers only: waiting for the declared body must fail this test.
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            c.putrequest('POST', '/api/login', skip_host=True)
            c.putheader('Host', self.app.host)
            c.putheader('Origin', self.app.origin)
            c.putheader('Content-Type', 'application/x-www-form-urlencoded')
            c.putheader('Content-Length', '4097')
            c.endheaders()
            r = c.getresponse()
            self.assertEqual(r.status, 413)
            self.assertEqual(r.read(), b'')
        finally:
            c.close()

    def test_add_and_read_external_save(self):
        self.login()
        p = {'sheet': 'Fictional jobs', 'Company': 'Second Example', 'Role': 'Test role', 'version': self.data()['version'],'requestId':str(uuid.uuid4())}
        self.assertEqual(self.request('POST', '/api/add', p)[0], 200)
        self.assertEqual(len(self.data()['jobs']), 2)
        wb = openpyxl.load_workbook(self.book)
        wb.active.cell(2, 6).value = 'Fictional scan result'
        wb.save(self.book)
        wb.close()
        self.assertEqual(self.data()['jobs'][0]['Notes'], 'Fictional scan result')

    def test_lockout_survives_restart(self):
        for _ in range(5):
            self.assertEqual(self.request('POST', '/api/login', 'wrong')[0], 401)
        self.assertEqual(self.request('POST', '/api/login', self.password)[0], 429)
        app2 = hq.App(self.state, self.book, self.app.origin)
        self.assertEqual(app2.login(self.password)[1], 429)

    def test_local_unlock_clears_lockout_without_restart(self):
        for _ in range(5):
            self.assertEqual(self.request('POST', '/api/login', 'wrong')[0], 401)
        self.assertEqual(self.app.login(self.password)[1], 429)
        hq.unlock(self.state)
        self.assertEqual(self.app.login(self.password)[1], 200)

    def test_health_requires_completion_marker(self):
        self.assertEqual(self.app.health()['scan']['state'],'unknown')
        log=self.state/hq.load_component('hq_settings').load()['scan_log_glob'].replace('*','2026-11-04')
        log.write_text('=== example start ===\n')
        self.assertEqual(self.app.health()['scan']['state'],'unconfirmed')
        log.write_text('=== 2026-11-04T11:38:14+00:00 end (exit 0) ===\n')
        self.assertEqual(self.app.health()['scan']['state'],'finished')
        log.write_text('=== 2026-11-04T11:38:14+00:00 end (exit 1) ===\n')
        self.assertEqual(self.app.health()['scan']['state'],'failed')

    def test_public_search_auth_contract_and_explicit_workbook_save(self):
        # No network or real workbook.
        source={'id':'example','label':'Example Discovery','provider':'greenhouse','board':'example'}
        self.app.search.sources=(source,)
        self.app.search.fetcher=lambda _: {'jobs':[{'title':'AI Workflow Engineer','location':{'name':'Remote'},'absolute_url':'https://example.org/jobs/new'}]}
        payload={'query':'AI','location':'','remote':True,'requestId':str(uuid.uuid4())}
        self.assertEqual(self.request('GET','/api/search/state')[0],401)
        self.assertEqual(self.request('POST','/api/search/submit',payload)[0],401)
        self.login()
        self.assertEqual(self.request('POST','/api/search/submit',payload,origin='https://other.example')[0],403)
        status,_,raw=self.request('POST','/api/search/submit',payload)
        self.assertEqual(status,202)
        key=json.loads(raw)['id']
        self.assertEqual(self.request('POST','/api/search/submit',payload)[0],202)
        self.app.search.process_one()
        state=json.loads(self.request('GET','/api/search/state')[2])
        item=state['searches'][0]
        self.assertEqual((item['id'],item['status']),(key,'completed'))
        self.assertEqual(item['results'][0]['link'],'https://example.org/jobs/new')
        self.assertEqual(len(self.data()['jobs']),1,'Discovery must not silently add a tracker row')
        result=item['results'][0]
        add={'sheet':'Fictional jobs','Company':result['company'],'Role':result['role'],'Link':result['link'],
             'My status':'Tracked','version':self.data()['version'],'requestId':str(uuid.uuid4())}
        self.assertEqual(self.request('POST','/api/add',add)[0],200)
        self.assertEqual(len(self.data()['jobs']),2)
        self.assertEqual(self.request('POST','/api/search/submit',{**payload,'query':'AI','url':'http://localhost/private'})[0],400)

    def test_other_process_writer_lock_queues_edit(self):
        self.login()
        ready=self.state/'lock-ready'
        code='''import importlib.util,sys
from pathlib import Path
s=importlib.util.spec_from_file_location('fixture_lock',sys.argv[1])
m=importlib.util.module_from_spec(s)
s.loader.exec_module(m)
with m.workbook_lock(sys.argv[2]):
    Path(sys.argv[3]).touch()
    sys.stdin.readline()
'''
        child=subprocess.Popen([sys.executable,'-I','-c',code,str(HERE.parent/'tracker'/'workbook_lock.py'),str(self.book),str(ready)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+5
            while not ready.exists() and time.monotonic()<deadline and child.poll() is None:
                time.sleep(.05)
            self.assertTrue(ready.exists(),'Lock fixture failed to start')
            self.assertEqual(self.edit(note='After scan lock')[0],202)
            child.communicate('\n',timeout=5)
            self.assertEqual(child.returncode,0)
            self.app.queue.process_one()
            self.assertEqual(self.data()['jobs'][0]['Notes'],'After scan lock')
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)

    def test_scan_launcher_coordinates_without_real_scan(self):
        root=self.state/'scan-fixture'
        (root/'work'/'logs').mkdir(parents=True)
        book=root/'work'/'applications-tracker.xlsx'
        shutil.copyfile(self.book,book)
        # Synthetic launcher only; no ignored local script or provider is needed.
        #
        launcher=r'''$repo = 'C:\Fictional\JobSearchHQ'
$book = [IO.Path]::GetFullPath((Join-Path $repo 'work\applications-tracker.xlsx')).ToLowerInvariant()
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $hash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($book))).Replace('-', '').ToLowerInvariant()
} finally { $sha.Dispose() }
$mutex = New-Object Threading.Mutex($false, ('Global\JobSearchHQ-Workbook-' + $hash.Substring(0, 24)))
$owned = $false
try {
    try { $owned = $mutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $owned = $true }
    if (-not $owned) { exit 1 }
    $result = claude
    $code = $global:LASTEXITCODE
    $log = Join-Path $repo 'work\logs\scan-fictional.log'
    @($result, "=== fictional end (exit $code) ===") | Set-Content -LiteralPath $log -Encoding UTF8
    exit $code
} finally {
    if ($owned) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
'''
        old="$repo = 'C:\\Fictional\\JobSearchHQ'"
        self.assertEqual(launcher.count(old),1)
        launcher=launcher.replace(old,"$repo = '"+str(root).replace("'","''")+"'")
        # A function shadows the provider executable. This fixture never invokes AI.
        fake='''function global:claude {
    $global:LASTEXITCODE = 0
    '{"result":"Synthetic scan only","subtype":"success","total_cost_usd":0,"num_turns":0,"duration_ms":1,"is_error":false}'
}
'''
        script=self.state/'synthetic-scan.ps1'
        script.write_text(fake+launcher,encoding='utf-8-sig')
        command=['powershell','-NoProfile','-File',str(script)]
        with self.app.tracker.workbook_lock(book):
            blocked=subprocess.run(command,capture_output=True,text=True,timeout=15)
        self.assertEqual(blocked.returncode,1,blocked.stderr)
        self.assertFalse(list((root/'work'/'logs').glob('*.log')))
        run=subprocess.run(command,capture_output=True,text=True,timeout=15)
        self.assertEqual(run.returncode,0,run.stderr)
        logs=list((root/'work'/'logs').glob('*.log'))
        self.assertEqual(len(logs),1)
        self.assertIn('end (exit 0)',logs[0].read_text(encoding='utf-8-sig'))

    # Documents tab: fictional files only, in the temporary work folder. Claude · HQ Builder (Opus), 2026-11-05.
    REPORT = ('# Fictional shortlist\nIntro with **bold** and [a link](https://example.com/x).\n'
              '<script>window.PWNED=1</script><img src=x onerror="window.PWNED=2">\n\n'
              '| Company | Score |\n|---|---|\n| Example Company | 4.1 |\n\n- First fictional point\n')

    def make_documents(self):
        import docx
        for kind in ('CV', 'CL'):
            d = docx.Document()
            d.add_paragraph(f'Fictional {kind} for Example Company')
            d.save(self.state / (self.app.files.SETTINGS['document_prefix'] + f'{kind}_Example_Engineer.docx'))
        (self.state / 'report-fictional-shortlist-2026-11-05.md').write_bytes(self.REPORT.encode('utf-8'))
        return {d['kind']: d for d in self.app.files.documents(self.state)}, self.app.files.reports(self.state)[0]

    def test_documents_allowlist(self):
        docs, report = self.make_documents()
        for path in ('/api/docs', '/api/docs/' + docs['cl']['id'], '/api/reports/' + report['id'], '/api/reports/' + report['id'] + '/md'):
            self.assertEqual(self.request('GET', path)[0], 401, path)
        self.login()
        listing = json.loads(self.request('GET', '/api/docs')[2])
        self.assertEqual([d['pair'] for d in listing['cvs']], ['Example_Engineer'])
        self.assertEqual([d['pair'] for d in listing['letters']], ['Example_Engineer'])
        self.assertEqual([r['name'] for r in listing['reports']], ['report-fictional-shortlist-2026-11-05.md'])
        self.assertEqual([d['kind'] for d in json.loads(self.request('GET', '/api/cvs')[2])['files']], ['cv'])
        status, headers, body = self.request('GET', '/api/docs/' + docs['cl']['id'])
        self.assertEqual(status, 200)
        self.assertIn((self.app.files.SETTINGS['document_prefix'] + 'CL_Example_Engineer.docx'), headers['Content-Disposition'])
        self.assertEqual(self.request('GET', '/api/cvs/' + docs['cl']['id'])[0], 404, 'the CV route serves CVs only')
        view = json.loads(self.request('GET', '/api/reports/' + report['id'])[2])
        self.assertIn('<script>window.PWNED=1</script>', json.dumps(view['blocks']).replace('\\u003c', '<'))
        self.assertEqual(self.request('GET', '/api/reports/' + report['id'] + '/md')[2].decode(), self.REPORT)
        status, headers, body = self.request('GET', '/api/reports/' + report['id'] + '/docx')
        self.assertEqual((status, headers['Content-Type']), (200, self.app.files.MIME))
        # Unknown ids, traversal, wrong format, files outside the patterns, oversize and links are refused.
        (self.state / 'notes.md').write_text('private', encoding='utf-8')
        (self.state / (self.app.files.SETTINGS['document_prefix'] + 'CL_Huge.docx')).write_bytes(b'0' * (5 * 1024 * 1024 + 1))
        (self.state / 'report-huge.md').write_text('x' * (1024 * 1024 + 1), encoding='utf-8')
        (self.state / 'sub').mkdir()
        (self.state / 'sub' / 'report-nested.md').write_text('nested', encoding='utf-8')
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(self.state / 'report-junction.md'), str(self.state / 'sub')], capture_output=True)
        for path in ('/api/docs/' + '0' * 24, '/api/docs/..%2F..%2Fserver.py', '/api/docs/../../server.py', '/api/reports/' + '0' * 24,
                     '/api/reports/' + report['id'] + '/exe', '/api/reports/..%2Fnotes.md', '/api/docs/' + hashlib.sha256(b'notes.md').hexdigest()[:24],
                     '/api/reports/' + hashlib.sha256(b'report-huge.md').hexdigest()[:24],
                     '/api/docs/' + hashlib.sha256(((self.app.files.SETTINGS['document_prefix'] + 'CL_Huge.docx').encode())).hexdigest()[:24]):
            self.assertEqual(self.request('GET', path)[0], 404, path)
        names = {d['name'] for d in self.app.files.documents(self.state)} | {r['name'] for r in self.app.files.reports(self.state)}
        self.assertEqual(names, {(self.app.files.SETTINGS['document_prefix'] + 'CV_Example_Engineer.docx'), (self.app.files.SETTINGS['document_prefix'] + 'CL_Example_Engineer.docx'),
                                 'report-fictional-shortlist-2026-11-05.md'})
        # A symbolic link is refused. Windows needs a privilege to create one, so the link check is simulated if needed.
        link = self.state / (self.app.files.SETTINGS['document_prefix'] + 'CL_Linked.docx')
        try:
            link.symlink_to(self.state / (self.app.files.SETTINGS['document_prefix'] + 'CL_Example_Engineer.docx'))
        except OSError:
            from unittest import mock
            shutil.copyfile(self.state / (self.app.files.SETTINGS['document_prefix'] + 'CL_Example_Engineer.docx'), link)
            real = Path.is_symlink
            with mock.patch.object(Path, 'is_symlink', lambda p: p.name == link.name or real(p)):
                self.assertNotIn(link.name, {d['name'] for d in self.app.files.documents(self.state)})
        else:
            self.assertNotIn(link.name, {d['name'] for d in self.app.files.documents(self.state)})

    def serve_tls(self):
        # Real local HTTPS and cookie transport; no public service or real data.
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        cert, key = self.state / 'test-cert.pem', self.state / 'test-key.pem'
        openssl = Path('C:/Program Files/Git/usr/bin/openssl.exe')
        subprocess.run([str(openssl),'req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost'],
                       check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        # B6: bound TLS handshakes in worker threads so idle preconnects cannot stall accept().
        class TLSServer(hq.BoundedHTTPServer):
            def finish_request(self, request, client_address):
                request.settimeout(10)
                request.do_handshake()
                super().finish_request(request, client_address)

            def handle_error(self, request, client_address):
                if isinstance(sys.exc_info()[1], (ssl.SSLError, TimeoutError, ConnectionError, OSError)):
                    return
                super().handle_error(request, client_address)

        self.server = TLSServer(('127.0.0.1',0),hq.handler(self.app))
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert,key)
        self.server.socket = tls.wrap_socket(self.server.socket,server_side=True,do_handshake_on_connect=False)
        self.port = self.server.server_port
        self.app.origin = f'https://127.0.0.1:{self.port}'
        self.app.host = f'127.0.0.1:{self.port}'
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def test_tls_server_survives_idle_connection(self):
        # B6: a browser may open a connection and send nothing (a preconnect). The test HTTPS server must keep
        # answering other requests meanwhile; before the fix the TLS handshake ran on the one accept thread.
        import socket, http.client
        self.serve_tls()
        idle = socket.create_connection(('127.0.0.1', self.port))
        try:
            time.sleep(0.3)
            result = {}
            def fetch():
                try:
                    conn = http.client.HTTPSConnection('127.0.0.1', self.port, timeout=5,
                                                       context=ssl._create_unverified_context())
                    conn.request('GET', '/login', headers={'Host': self.app.host})
                    result['status'] = conn.getresponse().status
                    conn.close()
                except Exception as error:
                    result['error'] = repr(error)
            worker = threading.Thread(target=fetch, daemon=True)
            worker.start()
            worker.join(8)
            self.assertEqual(result.get('status'), 200, 'Sign-in page did not load while another connection was idle: '+str(result))
        finally:
            idle.close()

    def browser_login(self, page):
        page.goto(self.app.origin)
        page.fill('#password', self.password)
        page.click('button')
        try:
            page.wait_for_selector('#sync', timeout=5000)
        except Exception:
            self.fail('Browser sign-in did not finish: '+page.url+' '+page.locator('body').inner_text()[:300])

    def test_tab_geometry_is_stable(self):
        # Top tabs keep position and size in every view, including Documents, at desktop, computer, tablet and phone widths.
        from playwright.sync_api import sync_playwright, expect
        self.serve_tls()
        rects = "() => [...document.querySelectorAll('.seg button')].map(b => {const r = b.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y + scrollY), Math.round(r.width), Math.round(r.height)]})"
        with sync_playwright() as p:
            browser = p.webkit.launch()
            for width in (1440, 1280, 1024, 375):
                context = browser.new_context(viewport={'width': width, 'height': 900}, ignore_https_errors=True)
                page = context.new_page()
                self.browser_login(page)
                expect(page.locator('#sync')).to_contain_text('1 jobs', timeout=10000)
                self.assertEqual(page.locator('.seg [data-v="review"]').inner_text().strip(), 'Documents')
                first = None
                for view in ('overview', 'board', 'list', 'contacts', 'search', 'review', 'overview'):
                    page.click(f'.seg [data-v="{view}"]')
                    page.wait_for_timeout(150)
                    now = page.evaluate(rects)
                    first = first or now
                    self.assertEqual(now, first, f'tabs moved at {width}px in {view}')
                    if width == 375:
                        self.assertTrue(page.locator('.seg').evaluate('(s) => s.scrollWidth <= s.clientWidth'),
                                        f'phone tabs overflow in {view}, allowing viewShown to scroll them')
                    if view not in ('search', 'review'):
                        page.locator('#q').focus()
                        page.wait_for_timeout(300)
                        self.assertEqual(page.evaluate(rects), first, f'focused search shifted tabs at {width}px')
                        self.assertTrue(page.locator('#appSearch').evaluate("""search => {
                            const a = search.getBoundingClientRect();
                            return [...document.querySelectorAll('.seg button')].every(tab => {
                                const b = tab.getBoundingClientRect();
                                return a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top;
                            });
                        }"""), f'focused search covers tabs at {width}px in {view}')
                        page.locator('#q').blur()
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width,
                                         f'page overflows at {width}px in {view}')
                context.close()
            browser.close()

    def test_board_stages_browser(self):
        """Step 01a: eight stages in order, Rejected only after Applied, moves save, same page on the phone."""
        from playwright.sync_api import sync_playwright, expect
        ids = ['tracked', 'review', 'apply', 'skipped', 'applied', 'interview', 'offer', 'rejected']
        wb = openpyxl.load_workbook(self.book)
        wb.active.append([2, 'Second Company', 'Second Engineer', 'Applied', '2026-01-03', '', 'https://example.com/job2'])
        wb.save(self.book)
        wb.close()
        self.serve_tls()

        def status(company):
            return next(j['My status'] for j in self.app.data()['jobs'] if j['Company'] == company)

        def wait_status(company, wanted):
            for _ in range(100):
                if status(company) == wanted:
                    return
                time.sleep(0.1)
            self.assertEqual(status(company), wanted)

        with sync_playwright() as p:
            browser = p.webkit.launch()
            # Check the initial Applied card on the phone before the desktop moves it to Rejected.
            for name, options in (('phone', p.devices['iPhone 15']), ('desktop', {'viewport': {'width': 1440, 'height': 900}})):
                context = browser.new_context(**options, ignore_https_errors=True)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                self.browser_login(page)
                expect(page.locator('#sync')).to_contain_text('2 jobs', timeout=10000)
                page.click('.seg [data-v="board"]')
                page.locator('.jobviews [data-jv="all"]').click()  # Exercise every stage and move destination.
                self.assertEqual(page.eval_on_selector_all('.board-cols > .col[data-stage]', 'cs => cs.map(c => c.dataset.stage)'), ids, name)
                dot = "(stage) => getComputedStyle(document.querySelector(`.col[data-stage='${stage}'] .col-h .dot`)).backgroundColor"
                colors = {stage: page.evaluate(dot, stage) for stage in ids}
                gray = {colors[s] for s in ids if s not in ('offer', 'rejected')}
                self.assertEqual(len(gray), 1, colors)
                self.assertNotIn(colors['offer'], gray | {colors['rejected']})
                self.assertNotIn(colors['rejected'], gray)
                self.assertIn('green', page.evaluate("STAGES.find(s => s.id === 'offer').c"))
                self.assertIn('red', page.evaluate("STAGES.find(s => s.id === 'rejected').c"))
                menu = lambda stage: page.eval_on_selector_all(f'.col[data-stage="{stage}"] .bcard-move-item', 'xs => xs.map(x => x.dataset.moveto)')
                self.assertNotIn('rejected', menu('review'))
                self.assertIn('skipped', menu('review'))
                self.assertIn('rejected', menu('applied'))
                self.assertNotIn('skipped', menu('applied'))
                result = page.evaluate('trackerSelfTest()')
                self.assertTrue(result['ok'], result['lines'] if 'lines' in result else result)
                if name == 'desktop':
                    def move(stage, target):
                        # A save re-renders the board and closes an open menu, so retry the open-and-pick a few times.
                        for attempt in range(5):
                            try:
                                page.click(f'.col[data-stage="{stage}"] [data-act="open-move"]', timeout=3000)
                                page.click(f'.col[data-stage="{stage}"] .bcard-move-item[data-moveto="{target}"]', timeout=3000)
                                return
                            except Exception:
                                if attempt == 4:
                                    raise
                                page.wait_for_timeout(300)
                    move('review', 'apply')
                    expect(page.locator('.col[data-stage="apply"]')).to_contain_text('Example Company')
                    wait_status('Example Company', 'Apply')
                    move('apply', 'review')
                    expect(page.locator('.col[data-stage="review"]')).to_contain_text('Example Company')
                    wait_status('Example Company', 'Review')
                    move('applied', 'rejected')
                    wait_status('Second Company', 'Rejected')
                # The Overview keeps its flow chart (Sankey): one node per stage, the possible steps as links.
                page.click('.seg [data-v="overview"]')
                keys = page.eval_on_selector_all('#sk .nd[data-key]', 'ns => ns.map(n => n.dataset.key)')
                self.assertEqual(sorted(keys), sorted(ids), name)
                links = page.eval_on_selector_all('#sk [data-link]', 'ls => ls.map(l => l.dataset.link)')
                self.assertEqual(sorted(links), sorted(['Tracked to Review', 'Review to Apply', 'Apply to Skipped', 'Apply to Applied',
                                                       'Applied to Interview', 'Applied to Rejected', 'Interview to Offer']), name)
                self.assertTrue(page.eval_on_selector_all('#sk path[data-link]', "ps => ps.every(p => (p.getAttribute('d') || '').includes('C'))"),
                                'flow links are curves')
                self.assertEqual(errors, [], name)
                context.close()
            browser.close()

    def test_board_horizontal_scroll_browser(self):
        from playwright.sync_api import sync_playwright, expect

        self.serve_tls()
        stages = ['tracked', 'review', 'apply', 'skipped', 'applied', 'interview', 'offer', 'rejected']
        with sync_playwright() as p:
            browser = p.webkit.launch()
            for width in (1440, 375):
                with self.subTest(width=width):
                    phone = width == 375
                    context = browser.new_context(viewport={'width': width, 'height': 900},
                                                  is_mobile=phone, has_touch=phone, ignore_https_errors=True)
                    page = context.new_page()
                    self.browser_login(page)
                    expect(page.locator('#sync')).to_contain_text('1 job')
                    page.click('.seg [data-v="board"]')
                    expect(page.locator('.jobviews [data-jv="todo"]')).to_have_attribute('aria-pressed', 'true')
                    board = page.locator('.board-cols')
                    columns = board.locator(':scope > [data-stage]').evaluate_all(
                        'cs => cs.map(c => [c.dataset.stage, c.getBoundingClientRect().width])')
                    self.assertEqual([stage for stage, _ in columns], stages)
                    for _, column_width in columns:
                        self.assertAlmostEqual(column_width, width * .84 if phone else 280, delta=1)
                    if phone:
                        self.assertEqual(board.evaluate('b => getComputedStyle(b).scrollSnapType'), 'x mandatory')
                    self.assertTrue(board.evaluate('b => b.scrollWidth > b.clientWidth && b.scrollLeft === 0'))
                    heading_x = page.locator('.board-title').bounding_box()['x']
                    filters_x = page.locator('.jobviews').bounding_box()['x']
                    board.evaluate('b => { b.scrollLeft = b.scrollWidth; }')
                    page.wait_for_function("""() => {
                        const b = document.querySelector('.board-cols');
                        return b.scrollLeft > 0 && b.scrollWidth - b.clientWidth - b.scrollLeft < 2;
                    }""")
                    expect(board.locator('[data-stage="rejected"]')).to_be_in_viewport()
                    self.assertEqual(page.locator('.board-title').bounding_box()['x'], heading_x)
                    self.assertEqual(page.locator('.jobviews').bounding_box()['x'], filters_x)
                    self.assertTrue(page.evaluate("""() => {
                        window.scrollTo(1000, window.scrollY);
                        const root = document.scrollingElement;
                        return window.scrollX === 0 && root.scrollWidth === root.clientWidth;
                    }"""))
                    context.close()
            browser.close()

    def test_board_drag_scrolls_to_offscreen_rejected_and_saves(self):
        from playwright.sync_api import sync_playwright, expect

        wb = openpyxl.load_workbook(self.book)
        wb.active.cell(2, 4).value = 'Applied'
        wb.active.cell(2, 5).value = '2026-01-03'
        wb.save(self.book)
        wb.close()
        self.serve_tls()
        with sync_playwright() as p:
            browser = p.webkit.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, ignore_https_errors=True)
            page = context.new_page()
            self.browser_login(page)
            expect(page.locator('#sync')).to_contain_text('1 job')
            page.click('.seg [data-v="board"]')
            page.locator('.jobviews [data-jv="all"]').click()

            board = page.locator('.board-cols')
            source = board.locator('[data-stage="applied"] .bcard-top')
            rejected = board.locator('[data-stage="rejected"]')
            expect(source).to_be_in_viewport()
            expect(rejected).not_to_be_in_viewport()
            self.assertEqual(board.evaluate('b => b.scrollLeft'), 0)
            bounds = board.bounding_box()
            start = source.bounding_box()
            x, y = start['x'] + 30, start['y'] + 12
            # Hold within the tracker's 48px edge zone, away from the scrollbar and viewport edge.
            edge_x = bounds['x'] + bounds['width'] - 32
            page.mouse.move(x, y)
            page.mouse.down()
            try:
                page.mouse.move(x + 12, y, steps=4)
                expect(page.locator('body.is-dragging')).to_have_count(1)
                edge_y = bounds['y'] + 24
                page.mouse.move(edge_x, edge_y, steps=8)
                page.mouse.move(edge_x, edge_y)
                deadline = time.monotonic() + 15
                while not board.evaluate("""board => {
                    const target = board.querySelector('[data-stage="rejected"]').getBoundingClientRect();
                    return board.scrollLeft > 0 && target.right <= board.getBoundingClientRect().right;
                }"""):
                    if time.monotonic() >= deadline:
                        self.fail('Dragging did not scroll the Rejected column fully into the board viewport.')
                    time.sleep(0.05)
                target = rejected.locator('.col-empty').bounding_box()
                drop_x, drop_y = target['x'] + target['width'] / 2, target['y'] + target['height'] / 2
                page.mouse.move(drop_x, drop_y, steps=4)
                page.mouse.move(drop_x, drop_y)
                expect(board.locator('[data-stage="rejected"].over')).to_have_count(1)
            finally:
                page.mouse.up()

            expect(rejected.locator('.bcard')).to_contain_text('Example Company')
            for _ in range(100):
                saved = self.app.data()['jobs'][0]['My status']
                if saved == 'Rejected':
                    break
                time.sleep(0.1)
            self.assertEqual(saved, 'Rejected')
            context.close()
            browser.close()

    def test_overview_stale_boards_are_collapsed_counted_and_escaped(self):
        from playwright.sync_api import sync_playwright, expect

        self.serve_tls()
        app_data = self.app.data
        origin = urlsplit(self.app.origin)
        samples = [{'board': f'Example <i>Board</i> {n}', 'since': '2026-01-01',
                    'reason': 'Incomplete <b>results</b>'} for n in range(1, 30)]
        boards = []

        def guard(route):
            url = urlsplit(route.request.url)
            if (url.scheme, url.netloc) == (origin.scheme, origin.netloc):
                route.continue_()
            else:
                route.abort()

        with mock.patch.object(self.app, 'data', side_effect=lambda: dict(app_data(), stale_boards=boards)):
            with sync_playwright() as p:
                browser = p.webkit.launch()
                context = browser.new_context(ignore_https_errors=True)
                context.route('**/*', guard)
                page = context.new_page()
                self.browser_login(page)
                for count in (0, 1, 29):
                    with self.subTest(count=count):
                        boards = samples[:count]
                        page.reload()
                        expect(page.locator('#sync')).to_contain_text('1 jobs')
                        page.click('.seg [data-v="overview"]')
                        details = page.locator('#main details').filter(has_text='not fully read')
                        if not count:
                            expect(details).to_have_count(0)
                            continue
                        summary = details.locator('summary')
                        expect(summary).to_have_text(f"{count} job board{'s' if count != 1 else ''} not fully read")
                        rows = details.locator('li')
                        expect(rows.first).to_be_hidden()
                        summary.click()
                        expect(rows).to_have_count(count)
                        expect(rows.first).to_be_visible()
                        expect(rows.first).to_have_text('Example <i>Board</i> 1: not fully read since Jan 1 (Incomplete <b>results</b>)')
                        expect(rows.locator('i, b')).to_have_count(0)
                browser.close()

    def test_overview_empty_history_and_same_origin_browser(self):
        from playwright.sync_api import sync_playwright, expect

        self.serve_tls()
        app_data = self.app.data

        def data_with_notices():
            return dict(app_data(), daily_run={'state': 'failed', 'tone': 'n', 'message': 'Example scan failed.'},
                        stale_boards=[{'board': 'Example Board', 'since': '2026-01-01', 'reason': 'Incomplete results'}])

        self.enterContext(mock.patch.object(self.app, 'data', side_effect=data_with_notices))
        outside = []
        origin = urlsplit(self.app.origin)

        def guard(route):
            url = urlsplit(route.request.url)
            if (url.scheme, url.netloc) != (origin.scheme, origin.netloc):
                outside.append(route.request.url)
                route.abort()
            else:
                route.continue_()

        with sync_playwright() as p:
            browser = p.webkit.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, ignore_https_errors=True)
            context.add_init_script("""window.blockedExternal = [];
                document.addEventListener('securitypolicyviolation', e => {
                    if (e.blockedURI.startsWith('http') && new URL(e.blockedURI).origin !== location.origin)
                        window.blockedExternal.push(e.blockedURI);
                });""")
            context.route('**/*', guard)
            page = context.new_page()
            self.browser_login(page)
            expect(page.locator('#sync')).to_contain_text('1 jobs')
            page.click('.seg [data-v="overview"]')
            expect(page.locator('#main [role="status"]')).to_have_count(2)
            expect(page.locator('#main .banner[role="status"]')).to_have_text('Example scan failed.')
            chart_status = page.locator('#main .chart-carousel [role="status"]')
            expect(chart_status).to_have_text('')
            expect(page.locator('#main .banner', has_text='Example Board')).to_have_text(
                'Example Board: not fully read since Jan 1 (Incomplete results)')
            expect(page.locator('#sk')).to_be_visible()
            expect(page.locator('.sk-empty')).to_contain_text('No applications yet.')
            page.get_by_role('button', name='Next chart').click()
            expect(chart_status).to_have_text('Activity over time')
            expect(page.get_by_text('History starts once stage changes are recorded.')).to_be_visible()
            expect(page.locator('#activity-chart')).to_have_count(0)
            expect(page.locator('.ov-activity-feed')).to_have_count(0)
            page.click('.seg [data-v="search"]')
            expect(page.get_by_role('button', name='Search now')).to_be_visible()
            guide = page.locator('.bento-guide-card')
            expect(guide).to_contain_text('select Search now')
            expect(guide).to_contain_text('select Add to tracker for each result you want to keep')
            expect(page.locator('.search-aside')).not_to_contain_text('crawler')
            self.assertEqual(page.locator('link[rel="preconnect"], link[rel="dns-prefetch"]').evaluate_all(
                'links => links.map(link => link.href).filter(href => new URL(href).origin !== location.origin)'), [])
            self.assertEqual(page.evaluate('window.blockedExternal'), [])
            self.assertEqual(outside, [])
            context.close()
            browser.close()

    def test_overview_donut_tracked_and_empty_browser(self):
        from playwright.sync_api import sync_playwright, expect

        wb = openpyxl.load_workbook(self.book)
        ws = wb.active
        ws.cell(2, 4).value = 'Tracked'
        ws.append([2, 'Second Company', 'Example Analyst', 'Tracked'])
        ws.append([3, 'Third Company', 'Example Analyst', 'Tracked'])
        wb.save(self.book)
        wb.close()
        self.serve_tls()
        with sync_playwright() as p:
            browser = p.webkit.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, ignore_https_errors=True)
            page = context.new_page()
            self.browser_login(page)
            expect(page.locator('#sync')).to_contain_text('3 jobs')
            page.click('.seg [data-v="overview"]')
            progress = page.get_by_role('region', name='Application progress')
            expect(progress.locator('.ov-donut-center b')).to_have_text('3')
            expect(progress.locator('.ov-legend-row[data-stage="tracked"]')).to_have_text('Tracked 100.0%')
            expect(progress.locator('.ov-donut-slice')).to_have_count(1)
            expect(progress.locator('.ov-donut-slice title')).to_have_text('Tracked: 3 (100.0%)')
            expect(progress).not_to_contain_text('No jobs tracked yet')

            wb = openpyxl.load_workbook(self.book)
            wb.active.delete_rows(2, 3)
            wb.save(self.book)
            wb.close()
            page.reload()
            expect(page.locator('#sync')).to_contain_text('0 jobs')
            page.click('.seg [data-v="overview"]')
            expect(progress.locator('.ov-donut-center b')).to_have_text('0')
            expect(progress.get_by_text('No jobs tracked yet')).to_be_visible()
            expect(progress.locator('.ov-donut-slice')).to_have_count(0)
            context.close()
            browser.close()

    def test_shortcuts_popover_one_toggle_per_activation_browser(self):
        from playwright.sync_api import sync_playwright, expect

        self.serve_tls()
        with sync_playwright() as p:
            browser = p.webkit.launch()
            for native in (True, False):
                with self.subTest(native=native):
                    context = browser.new_context(viewport={'width': 1440, 'height': 900}, ignore_https_errors=True)
                    if native:
                        context.add_init_script("""window.scriptPopoverToggles = 0;
                            const toggle = HTMLElement.prototype.togglePopover;
                            HTMLElement.prototype.togglePopover = function(...args) {
                                if (this.id === 'help') window.scriptPopoverToggles++;
                                return toggle.apply(this, args);
                            };""")
                    else:
                        context.add_init_script("Object.defineProperty(HTMLElement.prototype, 'showPopover', {value: undefined});")
                    page = context.new_page()
                    self.browser_login(page)
                    trigger = page.get_by_role('button', name='Keyboard shortcuts')
                    help_panel = page.locator('#help')
                    expect(help_panel).to_be_hidden()
                    trigger.click()
                    expect(help_panel).to_be_visible()
                    if native:
                        self.assertTrue(help_panel.evaluate('el => el.matches(":popover-open")'))
                    else:
                        expect(trigger).to_have_attribute('aria-expanded', 'true')
                    trigger.press('Enter')
                    expect(help_panel).to_be_hidden()
                    if native:
                        self.assertEqual(page.evaluate('window.scriptPopoverToggles'), 0)
                    else:
                        expect(trigger).to_have_attribute('aria-expanded', 'false')
                    context.close()
            browser.close()

    def test_phone_browser(self):
        from playwright.sync_api import sync_playwright, expect
        docs, report = self.make_documents()
        import docx
        noted = docx.Document()
        noted.add_paragraph('Sentiment: fictional note left in the file')
        noted.save(self.state / (self.app.files.SETTINGS['document_prefix'] + 'CL_Other_Noted.docx'))
        self.serve_tls()
        with sync_playwright() as p:
            browser = p.webkit.launch()
            # Exercise native browser Origin handling; never inject a passing header.
            context = browser.new_context(**p.devices['iPhone 15'],ignore_https_errors=True,accept_downloads=True)
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            self.browser_login(page)
            self.assertEqual(errors, [])
            expect(page.locator('#sync')).to_contain_text('1 jobs', timeout=10000)
            for view in ('board', 'list', 'contacts', 'search', 'review', 'overview'):
                page.click(f'.seg [data-v="{view}"]')
            self.assertEqual(errors, [])
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 394)
            lock = self.book.with_name('~$' + self.book.name)
            lock.touch()
            # Exercise the PC adapter and pending banner over the real HTTPS connection.
            page.evaluate("() => post('/api/update',{id:D.jobs[0]._id,sheet:D.jobs[0]._sheet,note:'Phone queued note'})")
            expect(page.locator('#pc-saves')).to_contain_text('Waiting for Excel')
            self.assertEqual(self.app.queue.recent()[0]['status'],'waiting')
            lock.unlink()
            self.app.queue.process_one()
            page.evaluate('() => pcWrites()')
            expect(page.locator('#pc-saves')).to_contain_text('Saved to your spreadsheet')
            # Success remains observable, then clears without a repeating-poll revival.
            expect(page.locator('#pc-saves')).to_have_attribute('role', 'status')
            expect(page.locator('#pc-saves')).to_have_attribute('aria-live', 'polite')
            expect(page.locator('#pc-saves')).to_be_hidden(timeout=7000)
            page.evaluate('() => pcWrites()')
            expect(page.locator('#pc-saves')).to_be_hidden()
            # Render the fictional Link column in the drawer without requesting that URL.
            page.evaluate('() => openJob(D.jobs[0]._id)')
            posting = page.locator('#drawer a[href="https://example.com/job"]')
            expect(posting).to_have_attribute('target', '_blank')
            expect(posting).to_have_attribute('rel', 'noopener noreferrer')
            page.evaluate('() => closeDrawers()')
            wb = openpyxl.load_workbook(self.book)
            self.assertEqual(wb.active.cell(2,6).value,'Phone queued note')
            wb.close()
            # Entire UI path uses a fake provider and fictional evidence, never a live call.
            self.app.ai.bank_loader=lambda:{'rubric_version':'v3.2','rules':'Fictional only','entries':{'E-999':{'level':'stated','text':'Fictional skill.'}}}
            def fake_runner(prompt,kind,folder,images):
                if kind=='analyze':return {'is_job_posting':True,'readable':True,'company':'Example Company','role':'Example Role','link':'','posting_text':'',
                                          'rubric_version':'v3.2','analysis':__import__('lead_policy_fixtures').fictional_analysis(),'summary':'Fictional result only','location_ok':'unclear','matches':[],'gaps':[],'flags':[]}
                if kind=='letter':return {'cover_letter_markdown':'Dear Example Company team,\n\nFictional skill.\n\nKind regards,\nAlex Example',
                                          'sources':['E-999'],'flags':[],'claims':[{'text':'Fictional skill.','evidence':['E-999']}]}
                return {'cv_markdown':'## Fictional CV\nFictional skill.','sources':['E-999'],'flags':[],
                        'claims':[{'text':'Fictional skill.','evidence':['E-999']}]}
            self.app.ai.runner=fake_runner
            page.click('.seg [data-v="review"]')
            page.fill('#r-text','Fictional job posting with example requirements. '*8)
            page.click('#r-go')
            expect(page.locator('#r-op')).to_contain_text('Waiting for Codex')
            self.app.ai.process_one()
            expect(page.locator('#result')).to_contain_text('Fictional result only',timeout=10000)
            page.get_by_role('button',name='Create CV',exact=True).click()
            expect(page.locator('#result')).to_contain_text('Waiting for Codex')
            self.app.ai.process_one()
            expect(page.locator('#result')).to_contain_text('Fictional CV',timeout=10000)
            expect(page.locator('#result')).to_contain_text('awaiting separate truth review')
            page.get_by_role('button',name='Create cover letter',exact=True).click()
            expect(page.locator('#r-sent')).to_be_visible()
            self.assertEqual(len(self.app.ai.list_tasks()),2,'Opening sentiment must not create a letter')
            # Documents tab: three sections, a CV/letter pair, a cover letter download and a safely rendered report.
            page.click('.seg [data-v="review"]')
            for box in ('#cvs', '#cls'):
                expect(page.locator(box)).to_contain_text('Example Engineer')
            expect(page.locator('#cvs')).to_contain_text('Has a cover letter')
            expect(page.locator('#reports')).to_contain_text('Fictional shortlist 2026-11-05')
            with page.expect_download() as download:
                page.locator('#cls').get_by_role('button', name='Download Example Engineer cover letter').click()
            self.assertEqual(download.value.suggested_filename, (self.app.files.SETTINGS['document_prefix'] + 'CL_Example_Engineer.docx'))
            expect(page.locator('#cls li', has_text='Other Noted')).to_contain_text('Has notes')
            expect(page.locator('#cls li', has_text='Example Engineer')).not_to_contain_text('Has notes')
            # A CV downloads through /api/cvs (served by every HQ version); a failed download keeps the page in place.
            with page.expect_request('**/api/cvs/' + docs['cv']['id']), page.expect_download() as download:
                page.locator('#cvs').get_by_role('button', name='Download Example Engineer CV').click()
            self.assertEqual(download.value.suggested_filename, (self.app.files.SETTINGS['document_prefix'] + 'CV_Example_Engineer.docx'))
            (self.state / (self.app.files.SETTINGS['document_prefix'] + 'CL_Other_Noted.docx')).unlink()
            url = page.url
            page.locator('#cls').get_by_role('button', name='Download Other Noted cover letter').click()
            expect(page.locator('#tmsg')).to_contain_text('not available')
            self.assertEqual(page.url, url)
            page.locator('#reports').get_by_role('button', name='Read', exact=True).click()
            view = page.locator('#report-view')
            expect(view).to_be_visible()
            expect(view.locator('h3').nth(1)).to_have_text('Fictional shortlist')
            expect(view).to_contain_text('Company: Example Company')
            expect(view).to_contain_text('<script>window.PWNED=1</script>')
            self.assertEqual(view.locator('script, img, a, iframe').count(), 0)
            self.assertIsNone(page.evaluate('window.PWNED'))
            with page.expect_download() as download:
                view.get_by_role('button', name='Download .docx').click()
            self.assertEqual(download.value.suggested_filename, 'report-fictional-shortlist-2026-11-05.docx')
            import docx
            self.assertIn('Company: Example Company', '\n'.join(x.text for x in docx.Document(download.value.path()).paragraphs))
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 394)
            # A job's detail links its CV and letter (company in the file name) and starts a letter on the website.
            page.click('.seg [data-v="list"]')
            page.evaluate('() => openJob(D.jobs[0]._id)')
            drawer = page.locator('#drawer')
            # Step 05a: with no document record (Excel mode) a file is never offered as this job's CV.
            expect(drawer).to_contain_text('Older files for Example Company (made outside the site)')
            expect(drawer.get_by_role('button', name='Download CV (.docx)')).to_have_count(0)
            expect(drawer.get_by_role('button', name='Download Example Engineer CV (made outside the site)')).to_be_visible()
            with page.expect_download() as download:
                drawer.get_by_role('button', name='Download Example Engineer cover letter (made outside the site)').click()
            self.assertEqual(download.value.suggested_filename, (self.app.files.SETTINGS['document_prefix'] + 'CL_Example_Engineer.docx'))
            drawer.get_by_role('button', name='Write a cover letter', exact=True).click()
            expect(page.locator('#v-review')).to_be_visible()
            self.assertEqual([page.input_value(f) for f in ('#r-company', '#r-role', '#r-link')], ['Example Company', 'Example Engineer', 'https://example.com/job'])
            expect(page.locator('#r-hint')).to_contain_text('Writing a cover letter for Example Company')
            page.fill('#r-text', 'Fictional job posting with example requirements. '*8)
            # The worker must see this submission, and the sentiment form from
            # the earlier result must not count as completion of this analysis.
            with page.expect_response(lambda r: r.url.endswith('/api/ai/submit') and r.request.method == 'POST') as submitted:
                page.click('#r-go')
            self.assertTrue(submitted.value.ok)
            self.assertEqual(submitted.value.request.post_data_json['posting']['jobId'], self.app.data()['jobs'][0]['_id'])
            self.app.ai.process_one()
            expect(page.locator('#r-go')).to_be_enabled(timeout=10000)
            expect(page.locator('#r-sent')).to_be_visible(timeout=10000)
            self.assertEqual([t['kind'] for t in self.app.ai.list_tasks()].count('letter'), 0, 'A letter waits for the sentiment choice')
            self.assertIn(self.app.data()['jobs'][0]['_id'], [x.get('jobId') for x in self.app.ai.postings()])
            page.get_by_role('button', name='Confident', exact=True).click()
            page.get_by_role('button', name='Write the letter', exact=True).click()
            expect(page.locator('#result')).to_contain_text('Waiting for Codex')
            letter_task = next(t for t in self.app.ai.list_tasks() if t['kind'] == 'letter')
            self.assertEqual(letter_task['status'], 'queued')
            self.app.ai.process_one()
            expect(page.locator('#result')).to_contain_text('Dear Example Company team', timeout=10000)
            # B6: the export reloads the document list, which moves the footer (and Sign out) on a phone; wait for it.
            with page.expect_response(lambda r: r.url.endswith('/api/docs') and r.request.method == 'GET') as docs_reloaded:
                with page.expect_download() as download:
                    page.locator('#result [data-pc-doc="cl"]').click()
            self.assertTrue(docs_reloaded.value.ok)
            letter = docx.Document(download.value.path())
            self.assertEqual({x.style.name for x in letter.paragraphs}, {'Normal'})
            self.assertNotIn('Sentiment', '\n'.join(x.text for x in letter.paragraphs))
            self.assertNotIn('DRAFT', download.value.suggested_filename)
            self.assertEqual(errors, [])
            # B6 diagnostics: on failure, report every request, response, navigation and console line after the click.
            trail, started = [], time.monotonic()
            def note(kind, text):
                trail.append(f'{time.monotonic()-started:6.2f}s {kind} {text}')
            page.on('request', lambda r: note('request', r.method + ' ' + r.url))
            page.on('response', lambda r: note('response', str(r.status) + ' ' + r.url))
            page.on('requestfailed', lambda r: note('failed', r.url + ' ' + str(r.failure)))
            page.on('framenavigated', lambda f: note('navigated', f.url) if f == page.main_frame else None)
            page.on('console', lambda m: note('console', m.type + ' ' + m.text[:200]))
            page.on('pageerror', lambda e: note('pageerror', str(e)[:200]))
            sign_out = page.get_by_role('button', name='Sign out', exact=True)
            sign_out.scroll_into_view_if_needed()
            boxes = []
            for _ in range(50):  # the list redraw lands just after its response; tap once the button holds still
                boxes.append(sign_out.bounding_box())
                if len(boxes) > 2 and boxes[-1] == boxes[-2] == boxes[-3]:
                    break
                page.wait_for_timeout(100)
            sign_out.click()
            note('clicked', 'Sign out')
            try:
                page.wait_for_selector('#password', timeout=15000)
            except Exception:
                workers = self.server._workers._value
                try:
                    state = page.evaluate('() => [document.readyState, location.href, document.title, document.body ? document.body.innerHTML.length : -1]')
                except Exception as error:
                    state = repr(error)
                self.fail('Sign out did not reach the sign-in page: ' + page.url + ' ' + page.locator('body').inner_text()[:300]
                          + f'\nfree server workers: {workers}; threads: {threading.active_count()}; page: {state}\n' + '\n'.join(trail[-60:]))
            browser.close()

    # Step 01b: interview rounds
    def round_request(self, **extra):
        d = self.data()
        p = dict(sheet='Fictional jobs', row=2, version=d['version'], action='add',
                 expect={'Company': 'Example Company', 'Role': 'Example Engineer'}, requestId=str(uuid.uuid4()))
        p.update(extra)
        return self.request('POST', '/api/rounds', p)

    def login_and_move_to_interview(self):
        self.login()
        for stage in ('Apply', 'Applied', 'Interview'):
            self.assertEqual(self.edit(status=stage, note=None)[0], 200)

    def test_rounds_api(self):
        """Step 01b, Excel only: a round is refused with a plain message; moves still work."""
        self.login_and_move_to_interview()
        status, _, body = self.round_request()
        self.assertEqual(status, 400)
        self.assertIn('database', json.loads(body)['error'])
        self.dismiss_latest()
        self.assertEqual(self.data()['jobs'][0]['_rounds'], [])

    def test_rounds_browser(self):
        self.skipTest('Rounds need the database; the browser check runs in IntegrationSQLite.')


class TrackerLauncherDocs(unittest.TestCase):
    def test_plain_launcher_documents_fit_and_star_limitations(self):
        readme = (HERE.parent / 'tracker' / 'README.md').read_text(encoding='utf-8')
        launch = next(line for line in readme.splitlines() if 'open-tracker.bat' in line)
        instructions = readme.split(launch, 1)[1].strip().split('\n- ', 1)[0]
        self.assertIn('Not scored yet', instructions)
        self.assertIn('star is disabled', instructions)
        self.assertIn('tools/hq-dashboard/server.py', instructions)


class IntegrationSQLite(Integration):
    """The same server checks once the tracker lives in SQLite (step 00b).

    Excel is then only a mirror: an open workbook no longer blocks a save, and an
    edit typed into Excel is kept as a copy instead of changing the site.
    """

    def setUp(self):
        super().setUp()
        store_spec = importlib.util.spec_from_file_location('tracker_store_fixture', HERE.parent / 'tracker' / 'store.py')
        store = importlib.util.module_from_spec(store_spec)
        store_spec.loader.exec_module(store)
        self.assertTrue(store.migrate(self.book, apply=True)['ok'])

    def test_store_is_sqlite(self):
        self.login()
        self.assertEqual(self.data()['store'], 'sqlite')

    def test_history_excludes_a_stored_row_the_tracker_hides(self):
        store = self.app.tracker.store
        with closing(store.Store(store.db_path(self.book))) as storage, storage.db:
            db = storage.db
            sheet = 'Fictional jobs'
            head = store.decode(db.execute('SELECT header FROM layouts WHERE sheet=?', (sheet,)).fetchone()[0])
            storage.put_row('jobs', sheet, 3, head,
                            [3, '', 'Example Engineer', 'Applied', '', 'Partial row', ''])
            visible_id, hidden_id = (storage.job_ref(f'{sheet}|{row}') for row in (2, 3))
            self.assertIsNotNone(hidden_id)
            db.execute("UPDATE jobs SET first_seen='2026-01-02' WHERE id=?", (visible_id,))
            db.execute("UPDATE jobs SET first_seen='2026-01-03' WHERE id=?", (hidden_id,))
            db.execute("INSERT INTO moves(job_id, from_status, to_status, at) VALUES (?, 'Review', 'Applied', '2026-01-04')",
                       (hidden_id,))

        self.login()
        data = self.data()
        self.assertEqual([job['_id'] for job in data['jobs']], [f'{sheet}|2'])
        self.assertEqual([(day['date'], day['total'], day['review']) for day in data['history']],
                         [('2026-01-02', 1, 1)])
        self.assertEqual(data['activity'], [{'key': f'{sheet}|2', 'date': '2026-01-02'}])

    def test_page_data_reads_an_older_schema_without_upgrading(self):
        path = self.app.tracker_db_path
        backups = path.parent / '.tracker-backups'

        def snapshot():
            with closing(sqlite3.connect(path)) as db:
                schema = db.execute('SELECT * FROM sqlite_master ORDER BY name').fetchall()
                meta = db.execute('SELECT * FROM meta ORDER BY key').fetchall()
            return schema, meta, backups.exists(), sorted(backups.iterdir()) if backups.exists() else []

        with closing(sqlite3.connect(path)) as db, db:
            db.execute('DROP TABLE board_reads')
            db.execute('DROP TABLE judgements')
            db.execute('DROP TABLE choice_signals')
            for table, columns in {
                'checks': ('run_id', 'posting_row', 'kind', 'observation', 'detail', 'at'),
                'postings': ('state', 'closed_on', 'close_reason', 'title', 'place'),
                'jobs': ('starred',), 'old_jobs': ('starred',),
            }.items():
                for column in columns:
                    db.execute(f'ALTER TABLE {table} DROP COLUMN {column}')
            db.execute("UPDATE meta SET value=3 WHERE key='schema_version'")
            db.execute("UPDATE jobs SET first_seen='2026-01-02'")
        before = snapshot()
        self.login()
        data = self.data()
        self.assertEqual(data['store'], 'sqlite')
        self.assertEqual(data['version'], 'db-1')
        self.assertEqual(data['jobs'][0]['posting']['state'], 'unknown')
        self.assertIsNone(data['jobs'][0]['judgement'])
        self.assertFalse(data['jobs'][0]['starred'])
        self.assertEqual(data['stale_boards'], [])
        self.assertEqual([(day['date'], day['total']) for day in data['history']], [('2026-01-02', 1)])
        self.assertEqual(snapshot(), before)

    def test_unscored_legacy_judgement_is_hidden_on_schema_seven(self):
        path = self.app.tracker_db_path
        with closing(sqlite3.connect(path)) as db, db:
            if 'score' in {row[1] for row in db.execute('PRAGMA table_info(judgements)')}:
                db.execute('ALTER TABLE judgements DROP COLUMN score')
            db.execute("UPDATE meta SET value=7 WHERE key='schema_version'")
            db.execute("INSERT INTO judgements(job_id, current, fit) SELECT id, 1, 'Good' FROM jobs")
        before = path.read_bytes()
        self.login()
        self.assertIsNone(self.data()['jobs'][0]['judgement'])
        self.assertEqual(path.read_bytes(), before)

    def test_score_page_data_uses_current_qualification_and_evidence_titles(self):
        evidence = self.state / 'evidence'
        evidence.mkdir()
        (evidence / 'E-701-example.md').write_text(
            '# E-701 — Example SQL reporting\n\n- level: verified\n', encoding='utf-8')
        with closing(sqlite3.connect(self.app.tracker_db_path)) as db, db:
            job_id = db.execute('SELECT id FROM jobs').fetchone()[0]
            for pass_name, current, fit, score, error in (
                ('qualification', 0, 'Skip', 3.1, None),
                ('qualification', 1, 'Worth applying', 4.2, None),
                ('want', 1, 'Apply now', 4.9, None),
                ('qualification', 0, None, None, 'Temporary failure'),
            ):
                db.execute('''INSERT INTO judgements(job_id, pass, current, fit, score, error, why, requirements)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                           (job_id, pass_name, current, fit, score, error,
                            json.dumps([dict(text='Relevant reporting', quote='Write reports.', evidence=['E-701']),
                                        dict(text='Related analysis', quote='Analyze results.', evidence=['E-701'])]),
                            json.dumps([dict(text='Write reports', state='supported', evidence=['E-701']),
                                        dict(text='Use E-702', state='unknown', evidence=['E-702'])])))
        self.enterContext(mock.patch.object(hq.hq_settings, 'data_root', return_value=self.state))
        self.login()
        data = self.data()
        judge = data['jobs'][0]['judgement']
        self.assertEqual((judge['score'], judge['action']), (4.2, 'Worth applying'))
        self.assertEqual(judge['reasons'], [
            dict(text='Relevant reporting', quote='Write reports.', evidenceTitles=['Example SQL reporting']),
            dict(text='Related analysis', quote='Analyze results.', evidenceTitles=['Example SQL reporting']),
        ])
        self.assertEqual(judge['notes'], ['The latest check failed. Showing the last judgement.'])
        self.assertEqual(judge['requirements'], [
            dict(text='Write reports', state='supported', evidenceTitles=['Example SQL reporting']),
            dict(text='Use evidence', state='unknown', evidenceTitles=[]),
        ])
        self.assertNotRegex(json.dumps(judge), r'\bE-\d+\b')
        version = json.loads(self.request('GET', '/api/version')[2])
        self.assertEqual(version['fitVersion'], data['fitVersion'])

    def test_star_and_unstar_are_idempotent(self):
        self.login()
        start = self.data()
        base = dict(sheet='Fictional jobs', row=2, expect={'Company': 'Example Company', 'Role': 'Example Engineer'})

        def save(**fields):
            payload = dict(base, version=self.data()['version'], requestId=str(uuid.uuid4()), **fields)
            first = self.request('POST', '/api/star', payload)
            self.assertEqual(first[0], 200, first[2])
            self.assertEqual(self.request('POST', '/api/star', payload)[2], first[2])
            return json.loads(first[2])

        star = save(starred=True)
        self.assertTrue(self.data()['jobs'][0]['starred'])
        self.assertEqual(self.data()['fitVersion'], start['fitVersion'])
        self.assertIsNone(save(starred=True)['choice'])
        unstar = save(starred=False)
        self.assertFalse(self.data()['jobs'][0]['starred'])
        restar = save(starred=True)
        self.assertTrue(self.data()['jobs'][0]['starred'])
        save(starred=False)
        self.assertFalse(self.data()['jobs'][0]['starred'])
        with closing(sqlite3.connect(self.app.tracker_db_path)) as db:
            events = db.execute('''SELECT kind, value, active, supersedes, origin, owner, idempotency_key
                                   FROM choice_signals ORDER BY id''').fetchall()
        self.assertEqual([event[:6] for event in events], [
            ('star', '1', 0, None, 'site', 'owner'),
            ('star', '0', 0, star['choice'], 'site', 'owner'),
            ('star', '1', 0, unstar['choice'], 'site', 'owner'),
            ('star', '0', 1, restar['choice'], 'site', 'owner'),
        ])
        self.assertEqual(len({event[-1] for event in events}), 4)

    def test_site_moves_record_choices_without_teaching_rejection(self):
        self.login()
        version, request_id = self.data()['version'], str(uuid.uuid4())
        skipped = self.edit(status='Skipped', reason='Example schedule', version=version, requestId=request_id)
        self.assertEqual(skipped[0], 200, skipped[2])
        self.assertEqual(self.edit(status='Skipped', reason='Example schedule', version=version,
                                   requestId=request_id)[2], skipped[2])
        self.assertEqual(self.edit(status='Review')[0], 200)
        self.assertEqual(self.data()['jobs'][0]['My status'], 'Review')
        self.assertEqual(self.edit(status='Applied', date='2026-01-01')[0], 200)
        self.assertEqual(self.edit(status='Rejected')[0], 200)
        with closing(sqlite3.connect(self.app.tracker_db_path)) as db:
            events = db.execute('SELECT kind, value, active, supersedes, reason FROM choice_signals ORDER BY id').fetchall()
            stages = db.execute('SELECT to_status FROM moves ORDER BY id').fetchall()
        self.assertEqual(events, [
            ('skip', 'Skipped', 1, None, 'Example schedule'),
            ('move', 'Review', 1, None, None),
            ('apply', 'Applied', 1, None, None),
        ])
        self.assertEqual(stages, [('Skipped',), ('Review',), ('Applied',), ('Rejected',)])

    def test_legacy_undo_parameters_do_not_change_choice_recording(self):
        self.login()
        skipped = self.edit(status='Skipped')
        self.assertEqual(skipped[0], 200, skipped[2])
        returned = self.edit(status='Review', undoChoice=json.loads(skipped[2])['choice'])
        self.assertEqual(returned[0], 200, returned[2])
        self.assertEqual(self.data()['jobs'][0]['My status'], 'Review')
        self.assertEqual(self.edit(status='Apply', undoStage=True)[0], 200)

        def star(starred, **extra):
            return self.request('POST', '/api/star', dict(
                sheet='Fictional jobs', row=2, expect={'Company': 'Example Company', 'Role': 'Example Engineer'},
                version=self.data()['version'], requestId=str(uuid.uuid4()), starred=starred, **extra))

        starred = star(True)
        self.assertEqual(starred[0], 200, starred[2])
        removed = star(False, undoChoice=json.loads(starred[2])['choice'])
        self.assertEqual(removed[0], 200, removed[2])
        self.assertFalse(self.data()['jobs'][0]['starred'])
        with closing(sqlite3.connect(self.app.tracker_db_path)) as db:
            self.assertEqual(db.execute('SELECT kind, value FROM choice_signals ORDER BY id').fetchall(), [
                ('skip', 'Skipped'), ('move', 'Review'), ('move', 'Apply'), ('star', '1'), ('star', '0'),
            ])

    def test_move_and_star_offer_no_undo_controls_browser(self):
        from playwright.sync_api import expect, sync_playwright

        self.serve_tls()
        with sync_playwright() as p:
            browser = p.webkit.launch()
            page = browser.new_page(viewport={'width': 1366, 'height': 900}, ignore_https_errors=True)
            self.browser_login(page)
            page.locator('.seg [data-v="board"]').click()
            page.locator('.bcard', has_text='Example Engineer').click()
            drawer = page.locator('#drawer')
            with self.app.tracker.workbook_lock(self.book):
                drawer.locator('select[data-status]').select_option('Skipped')
                expect(page.locator('#pc-saves')).to_contain_text('Waiting for Excel or the job scan')
                waiting = self.app.queue.recent()[0]
                self.assertEqual(waiting['status'], 'waiting')
                self.assertEqual(self.app.data()['jobs'][0]['My status'], 'Review')
            self.app.queue.process_one()
            self.assertEqual(self.app.queue.get(waiting['id'])['status'], 'saved')
            expect(page.locator('#tmsg')).to_contain_text('→ Skipped', timeout=10000)
            expect(page.get_by_role('button', name=re.compile('undo', re.I))).to_have_count(0)
            self.assertEqual(self.app.data()['jobs'][0]['My status'], 'Skipped')

            star = drawer.get_by_role('button', name='Star this job')
            star.click()
            expect(page.locator('#tmsg')).to_have_text('Job starred')
            expect(star).to_have_attribute('aria-pressed', 'true')
            expect(page.get_by_role('button', name=re.compile('undo', re.I))).to_have_count(0)
            drawer.get_by_role('button', name='Close details').click()
            page.locator('.seg [data-v="overview"]').click()
            expect(page.locator('.ov-activity-feed li', has_text='Example Company moved to Skipped')
                   .locator('time')).to_have_attribute('datetime', re.compile(r'^\d{4}-\d{2}-\d{2}$'))
            browser.close()

    def test_starred_lower_score_leads_list_and_overview_browser(self):
        from playwright.sync_api import expect, sync_playwright

        self.login()
        code, _, body = self.request('POST', '/api/add', {
            'sheet': 'Fictional jobs', 'Company': 'Example Higher Score', 'Role': 'Example Analyst',
            'version': self.data()['version'], 'requestId': str(uuid.uuid4()),
        })
        self.assertEqual(code, 200, body)
        with closing(sqlite3.connect(self.app.tracker_db_path)) as db, db:
            db.execute("""INSERT INTO judgements(job_id, current, fit, score)
                          SELECT id, 1, CASE WHEN company='Example Higher Score' THEN 'Apply now' ELSE 'Skip' END,
                          CASE WHEN company='Example Higher Score' THEN 4.8 ELSE 3.2 END FROM jobs""")
        self.serve_tls()
        with sync_playwright() as p:
            browser = p.webkit.launch()
            page = browser.new_page(viewport={'width': 1366, 'height': 900}, ignore_https_errors=True)
            self.browser_login(page)
            page.locator('.seg [data-v="list"]').click()
            expect(page.locator('.lcard .fit-chip')).to_have_text(['4.8 · Apply now', '3.2 · Skip'])
            page.locator('.seg [data-v="board"]').click()
            page.locator('.bcard', has_text='Example Engineer').click()
            drawer = page.locator('#drawer')
            star = drawer.get_by_role('button', name='Star this job')
            star.click()
            expect(star).to_have_attribute('aria-pressed', 'true')
            drawer.get_by_role('button', name='Close details').click()

            # A star still takes priority over score.
            page.locator('.seg [data-v="list"]').click()
            expect(page.locator('.lcard .fit-chip')).to_have_text(['3.2 · Skip', '4.8 · Apply now'])
            page.locator('.seg [data-v="overview"]').click()
            expect(page.locator('.ov-job-list .fit-chip')).to_have_text(['3.2 · Skip', '4.8 · Apply now'])
            browser.close()

    def test_legacy_verify_shows_source_warning_and_keeps_owner_notes_browser(self):
        from playwright.sync_api import expect, sync_playwright

        with self.app.tracker.editing_workbook() as wb:
            sheet = wb['Fictional jobs']
            sheet.cell(1, 8).value = 'Verdict'
            sheet.cell(2, 8).value = 'Verify'
            sheet.cell(2, 6).value = (
                'Lane: Verify\nWhy it fits: Example.\n'
                'Next step: Review the original posting and remaining application checks.\n\n'
                'Owner note: confirm the schedule.'
            )
        self.serve_tls()
        with sync_playwright() as p:
            browser = p.webkit.launch()
            page = browser.new_page(viewport={'width': 1366, 'height': 900}, ignore_https_errors=True)
            self.browser_login(page)
            page.locator('.seg [data-v="board"]').click()
            page.locator('.bcard', has_text='Example Engineer').click()
            drawer = page.locator('#drawer')
            expect(drawer.locator('.judge-note')).to_have_text('Check: source unclear')
            notes = drawer.locator('[data-note]')
            expect(notes).not_to_have_value(re.compile(r'Lane: Verify'))
            expect(notes).to_have_value(re.compile(r'Owner note: confirm the schedule\.'))
            browser.close()

    def test_posting_review_uses_evidence_titles_and_legacy_source_labels_browser(self):
        from playwright.sync_api import expect, sync_playwright

        self.serve_tls()
        with sync_playwright() as p:
            browser = p.webkit.launch()
            page = browser.new_page(ignore_https_errors=True)
            self.browser_login(page)
            page.locator('.seg [data-v="review"]').click()
            page.evaluate("""() => {
                EVIDENCE_TITLES = {'E-701': 'Example SQL reporting'};
                renderResult({_id: 'fixture', company: 'Example Company', fit: {
                    location_ok: 'See E-701', cv_edits: ['Used E-701'],
                    sources: ['L:Example Company']
                }});
            }""")
            result = page.locator('#result')
            expect(result).not_to_contain_text('E-701')
            expect(result).to_contain_text('Location: See Example SQL reporting')
            result.get_by_text('What was tailored in the CV', exact=True).click()
            expect(result.get_by_text('Used Example SQL reporting', exact=True)).to_be_visible()
            expect(result.get_by_text('Evidence used:', exact=False)).to_contain_text('Example Company (legacy CV)')
            browser.close()

    def test_score_requirements_cards_sort_and_star_browser(self):
        from playwright.sync_api import expect, sync_playwright

        evidence = self.state / 'evidence'
        evidence.mkdir()
        (evidence / 'E-701-example.md').write_text(
            '# E-701 — Example SQL reporting\n\n- level: verified\n', encoding='utf-8')
        self.enterContext(mock.patch.object(hq.hq_settings, 'data_root', return_value=self.state))
        self.login()
        for role in ('Example Newer', 'Example Older', 'Example Pending'):
            status, _, body = self.request('POST', '/api/add', {
                'sheet': 'Fictional jobs', 'Company': 'Example Company', 'Role': role,
                'version': self.data()['version'], 'requestId': str(uuid.uuid4()),
            })
            self.assertEqual(status, 200, body)
        with closing(sqlite3.connect(self.app.tracker_db_path)) as db, db:
            for role, posted in (('Example Engineer', '2026-01-05'), ('Example Newer', '2026-01-03'),
                                 ('Example Older', '2026-01-02'), ('Example Pending', '2026-01-04')):
                db.execute('UPDATE jobs SET posted_on=? WHERE role=?', (posted, role))
            requirements = [
                dict(text='Write SQL reports', state='supported', evidence=['E-701'], years_asked=5, years_have=6.5),
                dict(text='Operate a reporting tool', state='adjacent', evidence=['E-701']),
                dict(text='Configure a training sandbox', state='gap', evidence=[]),
                dict(text='Verify the E-702 requirement', state='unknown', evidence=[]),
            ]
            db.execute("""INSERT INTO judgements(job_id, current, fit, score, why, requirements)
                          SELECT id, 1, 'Only with a reason',
                          CASE WHEN role='Example Engineer' THEN 3.7 ELSE 3.8 END, ?, ?
                          FROM jobs WHERE role!='Example Pending'""",
                       (json.dumps([
                            dict(text='Reporting experience', quote='Write SQL reports.', evidence=['E-701']),
                            dict(text='Reporting tools', quote='Operate a reporting tool.', evidence=['E-701']),
                        ]), json.dumps(requirements)))
        self.serve_tls()
        with sync_playwright() as p:
            browser = p.webkit.launch()
            for width in (1366, 375):
                context = browser.new_context(viewport={'width': width, 'height': 900}, ignore_https_errors=True,
                                              has_touch=width == 375)
                page = context.new_page()
                page.goto(self.app.origin + '/login')
                page.locator('#password').fill(self.password)
                page.get_by_role('button', name='Sign in').click()
                page.locator('.seg [data-v="list"]').click()
                roles = page.locator('.lcard .ro')
                unstarred = ['Example Newer', 'Example Older', 'Example Engineer', 'Example Pending']
                expect(roles).to_have_text(unstarred)
                expect(page.locator('.lcard .fit-chip')).to_have_text(['3.8 · Only with a reason', '3.8 · Only with a reason',
                                                                      '3.7 · Only with a reason', 'Not scored yet'])
                page.locator('.seg [data-v="board"]').click()
                card = page.locator('.bcard', has_text='Example Engineer')
                expect(card.locator('.fit-chip')).to_have_text('3.7 · Only with a reason')
                card.click()
                drawer = page.locator('#drawer')
                expect(drawer).to_be_in_viewport()
                fit = drawer.locator('.judge-fit')
                expect(fit.locator('.fit-chip')).to_have_text('3.7 · Only with a reason')
                expect(fit.locator('.judge-why q')).to_have_text(['Write SQL reports.', 'Operate a reporting tool.'])
                expect(fit.locator('.judge-why small')).to_have_text(['Example SQL reporting', 'Example SQL reporting'])
                details = fit.locator('details')
                expect(details.locator('li').first).to_be_hidden()
                details.locator('summary').click()
                expect(details.locator('li .chip')).to_have_text(['Supported', 'Close', 'Missing', 'Unclear'])
                expect(details.locator('.judge-evidence')).to_have_text(['Example SQL reporting', 'Example SQL reporting'])
                expect(details.get_by_text('Years: 6.5 of 5 asked', exact=True)).to_be_visible()
                expect(drawer).not_to_contain_text(re.compile(r'\bE-\d+\b'))
                star = fit.get_by_role('button', name='Star this job')
                expect(star).to_have_attribute('aria-pressed', 'false')
                star.click()
                expect(star).to_have_attribute('aria-pressed', 'true')
                expect(details.locator('.judge-evidence').first).to_be_visible()
                drawer.get_by_role('button', name='Close details').click()
                page.locator('.seg [data-v="list"]').click()
                expect(roles).to_have_text(['Example Engineer', 'Example Newer', 'Example Older', 'Example Pending'])
                page.locator('.seg [data-v="board"]').click()
                card.click()
                star.click()
                expect(star).to_have_attribute('aria-pressed', 'false')
                drawer.get_by_role('button', name='Close details').click()
                page.locator('.seg [data-v="list"]').click()
                expect(roles).to_have_text(unstarred)
                page.reload()
                expect(roles).to_have_text(unstarred)
                expect(page.locator('.ring, .bcard-fit-badge, .gd-match-card')).to_have_count(0)
                self.assertFalse(next(job['starred'] for job in self.app.data()['jobs'] if job['Role'] == 'Example Engineer'))
                context.close()
            browser.close()

    def test_overview_charts_fit_and_dated_activity_browser(self):
        from playwright.sync_api import sync_playwright, expect

        store = self.app.tracker.store
        store.db_path(self.book).unlink()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Fictional jobs'
        ws.append(('Company', 'Role', 'My status', 'Verdict', 'Score'))
        ws.append(('Example Company', 'Example Engineer', 'Review', 'Long shot', 1.0))
        ws.append(('Sample Company', 'Example Analyst', 'Applied', 'Good fit', 2.0))
        ws.append(('Training Company', 'Example Analyst', 'Apply', 'Stretch', 4.9))
        ws.append(('Demo Company', 'Example Analyst', 'Tracked', 'Long shot', 5.0))
        ws.append(('Example Undated', 'Example Analyst', 'Offer', 'Strong', 5.0))
        wb.save(self.book)
        wb.close()
        self.assertTrue(store.migrate(self.book, apply=True)['ok'])
        with closing(store.Store(store.db_path(self.book))) as storage, storage.db:
            db = storage.db
            job_id = db.execute("SELECT id FROM jobs WHERE company='Example Company'").fetchone()[0]
            db.execute("UPDATE jobs SET first_seen=NULL")
            db.execute("UPDATE jobs SET first_seen='2026-01-01' WHERE id=?", (job_id,))
            db.execute("UPDATE jobs SET first_seen='2026-01-20' WHERE company='Sample Company'")
            db.executemany('''INSERT INTO judgements(job_id, current, fit, score)
                              SELECT id, 1, ?, ? FROM jobs WHERE company=?''', [
                ('Apply now', 4.8, 'Example Company'), ('Worth applying', 4.2, 'Sample Company'),
                ('Only with a reason', 3.8, 'Training Company'), ('Skip', 3.1, 'Demo Company'),
            ])
            db.execute("""INSERT INTO moves(job_id, from_status, to_status, at, who)
                          VALUES (?, 'Tracked', 'Review', '2026-01-19T12:00:00+00:00', 'site')""", (job_id,))
            db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")

        settings = dict(hq.hq_settings.load(), timezone_offset='+00:00')
        self.enterContext(mock.patch.object(hq.hq_settings, 'load', return_value=settings))
        self.serve_tls()
        with sync_playwright() as p:
            browser = p.webkit.launch()
            for width in (1366, 375):
                context = browser.new_context(viewport={'width': width, 'height': 900}, ignore_https_errors=True,
                                              has_touch=width == 375,
                                              reduced_motion='reduce' if width == 375 else 'no-preference')
                page = context.new_page()
                self.browser_login(page)
                expect(page.locator('#sync')).to_contain_text('5 jobs')
                page.click('.seg [data-v="overview"]')
                tiles = page.locator('.ov-tiles .tile')
                expect(tiles).to_have_count(9)
                if width == 1366:
                    self.assertEqual(len(set(tiles.evaluate_all('ts => ts.map(t => t.offsetTop)'))), 1)
                sankey = page.locator('#sk')
                chart = page.locator('#activity-chart')
                carousel = page.get_by_role('region', name='Overview charts')
                next_chart = carousel.get_by_role('button', name='Next chart')
                previous = carousel.get_by_role('button', name='Previous chart')
                summary_dot = carousel.get_by_role('button', name='Show Job search summary')
                activity_dot = carousel.get_by_role('button', name='Show Activity over time')
                expect(sankey).to_be_visible()
                expect(chart).to_be_hidden()
                expect(summary_dot).to_have_attribute('aria-current', 'true')
                for stage in ('Tracked', 'Review', 'Apply'):
                    node = sankey.locator(f'.nd[data-key="{stage.lower()}"]')
                    expect(node).to_have_attribute('data-n', '1')
                    expect(node.locator('text')).to_contain_text(stage)
                    expect(node.locator('text')).to_have_css('opacity', '1')
                    expect(node.locator('.nd-dot')).to_have_css('opacity', '1')
                    self.assertTrue(node.locator('.nd-dot').evaluate(
                        'dot => getComputedStyle(dot).fill === getComputedStyle(dot).color'))
                    self.assertTrue(node.locator('text').evaluate("""label => {
                        const text = label.getBoundingClientRect(), svg = label.ownerSVGElement.getBoundingClientRect();
                        return text.width > 0 && text.height > 0 && text.left >= svg.left && text.right <= svg.right
                            && text.top >= svg.top && text.bottom <= svg.bottom;
                    }"""), f'{stage} label fits inside the Sankey at {width}px')
                expect(page.locator('#chart-summary .sk-note')).to_contain_text('Previous stages are unknown.')
                self.assertTrue(carousel.evaluate("""c => {
                    const button = c.querySelector('#chart-activity .ov-time-btn');
                    button.focus();
                    return document.activeElement !== button;
                }"""))
                if width == 375:
                    expect(sankey).to_have_class('compact')
                    self.assertEqual(page.locator('.ov-wrap').evaluate("""overview =>
                        [overview, ...overview.querySelectorAll('*')]
                            .filter(el => el.getBoundingClientRect().right > window.innerWidth)
                            .map(el => `${el.tagName}.${el.getAttribute('class') || ''}: ${el.getBoundingClientRect().right}`)
                    """), [], 'Overview elements must fit within the 375px viewport')
                next_chart.click()
                expect(chart).to_be_visible()
                expect(sankey).to_be_hidden()
                expect(activity_dot).to_have_attribute('aria-current', 'true')
                expect(carousel.get_by_role('heading', name='Job search summary')).to_have_count(0)
                if width == 375:
                    expect(page.locator('#chart-activity')).to_have_css('animation-name', 'none')
                previous.click()
                expect(sankey).to_be_visible()
                expect(sankey.locator('.nd[data-key="apply"] text')).to_have_css('opacity', '1')
                activity_dot.click()
                expect(chart).to_be_visible()
                # Moving away from a chart while one of its controls has focus keeps focus in the carousel.
                carousel.get_by_role('button', name='1W', exact=True).press('ArrowLeft')
                expect(sankey).to_be_visible()
                expect(summary_dot).to_be_focused()
                summary_dot.press('ArrowRight')
                expect(chart).to_be_visible()
                page.reload()
                expect(page.locator('.seg [data-v="list"]')).to_have_attribute('aria-selected', 'true')
                page.click('.seg [data-v="overview"]')
                expect(chart).to_be_visible()
                expect(activity_dot).to_have_attribute('aria-current', 'true')
                expect(chart.locator('path[data-metric]')).to_have_count(9)
                activity_slide = carousel.locator('#chart-activity')

                def expect_dates(start):
                    dates = activity_slide.locator('.rc-x-axis .rc-x-lbl')
                    expect(dates).to_have_count(2)
                    expect(dates.nth(0)).to_have_attribute('datetime', start)
                    expect(dates.nth(1)).to_have_attribute('datetime', '2026-01-20')

                expect_dates('2026-01-19')
                page.get_by_role('button', name='4W', exact=True).click()
                expect_dates('2026-01-01')
                expect(page.locator('.rc-legend-pill[data-metric="all"] b')).to_have_text('2')
                actions = page.get_by_role('region', name='Score actions')
                expect(actions.locator('.ov-donut-slice title')).to_have_text([
                    'Apply now: 1 (20.0%)', 'Worth applying: 1 (20.0%)', 'Only with a reason: 1 (20.0%)',
                    'Skip: 1 (20.0%)', 'Not scored yet: 1 (20.0%)',
                ])
                opportunities = page.locator('.ov-job-list')
                expect(opportunities.locator('.t b')).to_have_text(
                    ['Example Company', 'Sample Company', 'Training Company', 'Demo Company'])
                expect(opportunities.locator('.fit-chip')).to_have_text(['4.8 · Apply now', '4.2 · Worth applying', '3.8 · Only with a reason', '3.1 · Skip'])
                recent = page.locator('.ov-activity-feed')
                expect(recent.locator('time')).to_have_text(['2026-01-20', '2026-01-19', '2026-01-01'])
                expect(recent).to_contain_text('Added Sample Company (Example Analyst)')
                expect(recent).to_contain_text('Example Company moved to Review')
                expect(recent).to_contain_text('Added Example Company (Example Engineer)')
                expect(recent).not_to_contain_text('Example Undated')
                if width == 375:
                    drawer = page.locator('#drawer')
                    close = drawer.get_by_role('button', name='Close details')
                    for entry, company in (
                        (opportunities.get_by_role('button', name='Demo Company', exact=False), 'Demo Company'),
                        (page.locator('.ov-attention-list').get_by_role('button', name='Training Company', exact=False),
                         'Training Company'),
                        (recent.get_by_role('button', name='Added Sample Company', exact=False), 'Sample Company'),
                    ):
                        entry.tap()
                        expect(drawer).to_be_in_viewport()
                        expect(drawer.locator('.detail')).to_contain_text(company)
                        close.tap()
                        expect(page.locator('#drawer.on')).to_have_count(0)
                    entry = recent.get_by_role('button', name='Example Company moved to Review', exact=False)
                    entry.focus()
                    expect(entry).to_be_focused()
                    entry.press('Enter')
                    expect(drawer).to_be_in_viewport()
                    expect(drawer.locator('.detail')).to_contain_text('Example Company')
                    # A tracker refresh replaces the original activity button.
                    page.evaluate('() => render()')
                    close.tap()
                    expect(drawer).to_be_hidden()
                    expect(entry).to_be_focused()
                page.click('.seg [data-v="list"]')
                page.locator('.jobviews [data-jv="all"]').click()
                page.locator('#sort').select_option('fit')
                expect(page.locator('.lcard .co')).to_have_count(5)
                expect(page.locator('.lcard .fit-chip')).to_have_text(['4.8 · Apply now', '4.2 · Worth applying', '3.8 · Only with a reason', '3.1 · Skip', 'Not scored yet'])
                page.locator('.lcard', has_text='Example Undated').click()
                match = page.locator('.judge-fit:visible')
                expect(match.locator('.fit-chip')).to_have_text('Not scored yet')
                expect(match.locator('.judge-requirements')).to_have_count(0)
                expect(page.locator('.ring, .bcard-fit-badge, .gd-match-card')).to_have_count(0)
                context.close()
            browser.close()

    def test_queue_survives_restart_and_retries_once(self):
        self.login()
        lock = self.book.with_name('~$' + self.book.name)
        lock.touch()
        try:
            self.assertEqual(self.edit(note='Saved while Excel is open')[0], 200)
            self.assertEqual(self.data()['jobs'][0]['Notes'], 'Saved while Excel is open')
        finally:
            lock.unlink()

    def test_queue_changed_workbook_requires_confirmation(self):
        self.login()
        self.assertEqual(self.edit(note='Site note')[0], 200)
        wb = openpyxl.load_workbook(self.book)
        wb.active.cell(2, 6).value = 'Edited on PC'
        wb.save(self.book)
        wb.close()
        self.assertEqual(self.data()['jobs'][0]['Notes'], 'Site note')
        self.assertEqual(self.edit(note='Second site note')[0], 200)
        self.assertIn('Excel', self.data()['mirrorWarning'])

    def test_edits_stale_rows_and_excel_lock(self):
        self.login()
        self.assertEqual(self.edit()[0], 200)
        self.assertEqual(self.data()['jobs'][0]['Notes'], 'Saved fictional note')
        self.assertEqual(self.edit(version='stale')[0], 409)
        self.dismiss_latest()
        self.assertEqual(self.edit(expect={'Company': 'Wrong company', 'Role': 'Example Engineer'})[0], 409)
        self.dismiss_latest()

    def test_add_and_read_external_save(self):
        self.login()
        p = {'sheet': 'Fictional jobs', 'Company': 'Second Example', 'Role': 'Test role', 'version': self.data()['version'], 'requestId': str(uuid.uuid4())}
        self.assertEqual(self.request('POST', '/api/add', p)[0], 200)
        self.assertEqual(len(self.data()['jobs']), 2)
        wb = openpyxl.load_workbook(self.book)
        self.assertEqual(wb.active.cell(3, 2).value, 'Second Example')
        wb.close()

    def test_tab_geometry_is_stable(self):
        self.skipTest('Browser checks run in Excel mode; the page does not depend on the store.')

    def test_phone_browser(self):
        self.skipTest('Browser checks run in Excel mode; the page does not depend on the store.')

    def test_board_stages_browser(self):
        self.skipTest('Browser checks run in Excel mode; the page does not depend on the store.')

    def test_board_horizontal_scroll_browser(self):
        self.skipTest('Board layout is checked in Excel mode.')

    def test_board_drag_scrolls_to_offscreen_rejected_and_saves(self):
        self.skipTest('Board drag scrolling is checked in Excel mode.')

    def test_overview_empty_history_and_same_origin_browser(self):
        self.skipTest('The no-history Overview browser check runs in Excel mode.')

    def test_overview_donut_tracked_and_empty_browser(self):
        self.skipTest('The tracked-only and empty Overview fixtures use Excel.')

    def posting(self, cv, job_id=None):
        key = str(uuid.uuid4())
        record = {'company': 'Example Company', 'role': 'Example Engineer', 'jobId': job_id or self.app.data()['jobs'][0]['_id'],
                  'fit': {'cv_markdown': cv, 'sources': ['E-999']}, 'reviewed': False, 'status': 'draft'}
        with self.app.ai.connect() as db, db:
            db.execute('INSERT INTO postings VALUES(?,?)', (key, json.dumps(record)))
        return key

    def test_export_records_and_blocks(self):
        """Step 05a: a site CV is saved in work/ with its record; a CV that reads like internal notes is refused."""
        self.login()
        prefix = self.app.files.SETTINGS['document_prefix']
        bad = self.posting('# Alex Example\n## Experience\n- Shipped PR #77 (github.com/example-user/review-tool/pull/77).')
        status, _, body = self.request('POST', '/api/ai/export', {'id': bad, 'kind': 'cv'})
        self.assertEqual(status, 400)
        self.assertIn('#77', json.loads(body)['error'])
        self.assertEqual(list(self.state.glob('*.docx')), [])
        good = self.posting('# Alex Example\n## Experience\n- Reviewed 21 merged PRs for a fictional team.')
        status, headers, body = self.request('POST', '/api/ai/export', {'id': good, 'kind': 'cv'})
        self.assertEqual(status, 200)
        name = prefix + 'CV_Example_Company_Example_Engineer.docx'
        self.assertIn(name, headers['Content-Disposition'])
        self.assertEqual((self.state / name).read_bytes(), body)
        self.assertFalse((self.state / 'generated-documents').exists())
        listing = json.loads(self.request('GET', '/api/docs')[2])
        cv = next(d for d in listing['cvs'] if d['name'] == name)
        self.assertEqual((cv['made_by'], cv['job_id']), ('site', self.app.data()['jobs'][0]['_id']))
        self.assertIsInstance(listing['evidence_notes'], list)

    def test_documents_drawer_browser(self):
        """Step 05a: the job's site-made CV is listed first; a file made outside the site is labelled and never the job's CV."""
        from playwright.sync_api import sync_playwright, expect
        import docx
        store_spec = importlib.util.spec_from_file_location('tracker_store_drawer', HERE.parent / 'tracker' / 'store.py')
        store = importlib.util.module_from_spec(store_spec)
        store_spec.loader.exec_module(store)
        prefix = self.app.files.SETTINGS['document_prefix']
        outside = self.state / (prefix + 'CV_Example_Company_Example_Engineer.docx')
        d = docx.Document()
        d.add_paragraph('Fictional CV made outside the site')
        d.save(outside)
        self.assertEqual(store.backfill_documents(self.book, self.state, prefix=prefix, apply=True)['added'], 1)
        job = self.app.data()['jobs'][0]
        record = {'company': 'Example Company', 'role': 'Example Engineer', 'jobId': job['_id'],
                  'fit': {'cv_markdown': '# Alex Example\n## Experience\n- Reviewed 21 merged PRs for a fictional team.', 'sources': ['E-999']}}
        site = self.app.files.export_draft(self.app.state, str(uuid.uuid4()), 'cv', record, work=self.state,
                                           store_path=store.db_path(self.book))
        self.assertEqual(site.name, prefix + 'CV_Example_Company_Example_Engineer_2.docx')
        self.serve_tls()
        shots = os.environ.get('HQ_SHOT_DIR')
        first = """() => {
            const d = document.querySelector('#drawer'), b = [...d.querySelectorAll('button')];
            const mine = b.find(x => x.textContent.includes('Download CV (.docx)'));
            const older = b.find(x => (x.getAttribute('aria-label') || '').includes('(made outside the site)'));
            return !!(mine && older && (mine.compareDocumentPosition(older) & Node.DOCUMENT_POSITION_FOLLOWING));
        }"""
        with sync_playwright() as p:
            browser = p.webkit.launch()
            for name, options in (('desktop', {'viewport': {'width': 1440, 'height': 900}}), ('phone', p.devices['iPhone 15'])):
                context = browser.new_context(**options, ignore_https_errors=True, accept_downloads=True)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                self.browser_login(page)
                expect(page.locator('#sync')).to_contain_text('1 job', timeout=10000)
                self.assertNotIn('Review fit + draft CV', page.content())
                page.click('.seg [data-v="review"]')
                expect(page.locator('#cvs li', has_text='Example Company Example Engineer 2')).to_contain_text('Made by the site')
                expect(page.locator('#cvs li', has_text='Example Company Example Engineer').filter(has_not_text='Engineer 2')).to_contain_text('Made outside the site')
                page.click('.seg [data-v="board"]')  # on a wide screen the list selects a card instead of opening the drawer
                page.evaluate('(id) => openJob(id)', job['_id'])
                drawer = page.locator('#drawer')
                expect(drawer).to_contain_text('CV for this role, made by the site on')
                expect(drawer).to_contain_text('Older files for Example Company (made outside the site)')
                mine = drawer.get_by_role('button', name='Download CV (.docx)')
                expect(mine).to_have_count(1)
                older = drawer.get_by_role('button', name='Download Example Company Example Engineer CV (made outside the site)')
                expect(older).to_have_count(1)
                self.assertTrue(page.evaluate(first), 'the site file is listed first')
                expect(drawer.get_by_role('button', name='Check fit and create CV')).to_be_visible()
                if shots:
                    page.wait_for_timeout(1000)
                    page.screenshot(path=str(Path(shots) / f'05a-drawer-{name}.png'), full_page=False)
                with page.expect_download() as download:
                    mine.click()
                self.assertEqual(download.value.suggested_filename, site.name)
                with page.expect_download() as download:
                    older.click()
                self.assertEqual(download.value.suggested_filename, outside.name)
                self.assertEqual(errors, [], name)
                context.close()
            browser.close()


    def test_rounds_api(self):
        """Step 01b: rounds are added and edited through the write queue, with the same checks as other edits."""
        self.login()
        self.assertEqual(self.round_request()[0], 400)  # still in Review
        self.dismiss_latest()
        for stage in ('Apply', 'Applied', 'Interview'):
            self.assertEqual(self.edit(status=stage, note=None)[0], 200)
        job = self.data()['jobs'][0]
        self.assertEqual([r['round'] for r in job['_rounds']], [1])
        status, _, body = self.round_request(type='Recruiter call', note='Fictional note', date='2026-02-10')
        self.assertEqual(status, 200, body)
        rounds = self.data()['jobs'][0]['_rounds']
        self.assertEqual([(r['round'], r['date'], r['type'], r['note']) for r in rounds][1],
                         (2, '2026-02-10', 'Recruiter call', 'Fictional note'))
        status, _, body = self.round_request(action='edit', id=rounds[1]['id'], type='Panel', note='', date='2026-02-11')
        self.assertEqual(status, 200, body)
        self.assertEqual(self.data()['jobs'][0]['_rounds'][1]['type'], 'Panel')
        self.assertEqual(self.round_request(version='stale')[0], 409)
        self.dismiss_latest()
        self.assertEqual(self.round_request(expect={'Company': 'Wrong company', 'Role': 'Example Engineer'})[0], 409)
        self.dismiss_latest()
        self.assertEqual(self.round_request(type='=1+1')[0], 400)
        self.dismiss_latest()
        self.assertEqual(len(self.data()['jobs'][0]['_rounds']), 2)

    def test_rounds_browser(self):
        """Step 01b: the Interview card shows "Round N of ?"; the drawer lists, adds and edits rounds (desktop, then phone)."""
        from playwright.sync_api import sync_playwright, expect
        self.login_and_move_to_interview()
        self.serve_tls()
        shots = os.environ.get('HQ_SHOT_DIR')
        with sync_playwright() as p:
            browser = p.webkit.launch()
            for name, options in (('desktop', {'viewport': {'width': 1440, 'height': 900}}), ('phone', p.devices['iPhone 15'])):
                context = browser.new_context(**options, ignore_https_errors=True)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                self.browser_login(page)
                expect(page.locator('#sync')).to_contain_text('1 job', timeout=10000)
                page.click('.seg [data-v="board"]')
                wanted = 1 if name == 'desktop' else 2
                page.locator('.jobviews [data-jv="progress"]').click()  # Interview belongs to In progress.
                expect(page.locator('.bcard-round').first).to_have_text(f'Round {wanted} of ?')
                job = self.app.data()['jobs'][0]
                page.evaluate('(id) => openJob(id)', job['_id'])
                drawer = page.locator('#drawer')
                expect(drawer.locator('.rounds')).to_contain_text('Interview rounds')
                if name == 'desktop':
                    expect(drawer.locator('.round')).to_have_count(1)
                    drawer.get_by_role('button', name='Add round').click()
                    expect(drawer.locator('.round')).to_have_count(2, timeout=10000)
                    second = drawer.locator('.round[data-round="2"]')
                    second.locator('input[data-field="type"]').fill('Recruiter call')
                    second.locator('input[data-field="note"]').fill('Fictional note')
                    second.get_by_role('button', name='Save round 2').click()
                    rounds = []
                    for _ in range(100):
                        rounds = self.app.data()['jobs'][0]['_rounds']
                        if len(rounds) == 2 and rounds[1]['type'] == 'Recruiter call':
                            break
                        time.sleep(0.1)
                    self.assertEqual([(r['round'], r['type'], r['note']) for r in rounds],
                                     [(1, '', ''), (2, 'Recruiter call', 'Fictional note')])
                    page.evaluate('closeDrawers()')
                    expect(page.locator('.bcard-round').first).to_have_text('Round 2 of ?')
                else:
                    expect(drawer.locator('.round')).to_have_count(2)
                    expect(drawer.locator('.round[data-round="2"] input[data-field="type"]')).to_have_value('Recruiter call')
                    expect(drawer.get_by_role('button', name='Add round')).to_be_visible()
                if shots:
                    page.wait_for_timeout(1000)
                    page.screenshot(path=str(Path(shots) / f'01b-board-{name}.png'), full_page=False)
                    page.evaluate('(id) => openJob(id)', job['_id'])
                    page.locator('#drawer .rounds').scroll_into_view_if_needed()
                    page.wait_for_timeout(1000)
                    page.screenshot(path=str(Path(shots) / f'01b-drawer-{name}.png'), full_page=False)
                self.assertEqual(errors, [], name)
                context.close()
            browser.close()

    def test_round_saved_while_add_round_is_finishing(self):
        """Bug B4: a Save round click while the Add round write is still reloading must not be dropped."""
        from playwright.sync_api import sync_playwright, expect
        self.login_and_move_to_interview()
        self.serve_tls()
        with sync_playwright() as p:
            browser = p.webkit.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, ignore_https_errors=True)
            page = context.new_page()
            self.browser_login(page)
            expect(page.locator('#sync')).to_contain_text('1 job', timeout=10000)
            page.click('.seg [data-v="board"]')
            page.locator('.jobviews [data-jv="progress"]').click()  # Interview belongs to In progress.
            job = self.app.data()['jobs'][0]
            page.evaluate('(id) => openJob(id)', job['_id'])
            drawer = page.locator('#drawer')
            expect(drawer.locator('.round')).to_have_count(1, timeout=10000)
            # Hold back the reload that follows Add round, as a slow computer does.
            page.evaluate("""() => { const f = window.fetch; let armed = true; window.HELD = [];
                window.fetch = (u, o) => { if (armed && String(u).includes('/api/data')) { armed = false;
                    return new Promise(r => window.HELD.push(r)).then(() => f(u, o)); } return f(u, o); }; }""")
            drawer.get_by_role('button', name='Add round').click()
            for _ in range(100):
                if len(self.app.data()['jobs'][0]['_rounds']) == 2:
                    break
                time.sleep(0.05)
            for _ in range(100):
                if page.evaluate('() => HELD.length') == 1:
                    break
                time.sleep(0.05)
            # Another refresh (the page's own poll) draws round 2 while the first reload is still held.
            page.evaluate('() => reloadJobs(true)')
            expect(drawer.locator('.round')).to_have_count(2, timeout=10000)
            second = drawer.locator('.round[data-round="2"]')
            second.locator('input[data-field="type"]').fill('Recruiter call')
            second.locator('input[data-field="note"]').fill('Fictional note')
            self.assertTrue(page.evaluate('() => PENDING.size > 0 && HELD.length === 1'), 'precondition: the Add round write is still finishing')
            second.get_by_role('button', name='Save round 2').click()
            page.evaluate('() => HELD.forEach(r => r())')
            rounds = []
            for _ in range(150):
                rounds = self.app.data()['jobs'][0]['_rounds']
                if len(rounds) == 2 and rounds[1]['type'] == 'Recruiter call':
                    break
                time.sleep(0.1)
            self.assertEqual([(r['type'], r['note']) for r in rounds], [('', ''), ('Recruiter call', 'Fictional note')],
                             'the Save round click was dropped while Add round was finishing')
            browser.close()

    def test_view_buttons_stay_put_after_sign_in(self):
        """Bug B5: a save made before sign-in must not push the view buttons down after the page appears."""
        from playwright.sync_api import sync_playwright, expect
        self.login_and_move_to_interview()  # leaves finished saves in the queue
        self.serve_tls()
        with sync_playwright() as p:
            browser = p.webkit.launch()
            context = browser.new_context(**p.devices['iPhone 15'], ignore_https_errors=True)
            # Hold the first save-notice request until the test releases it, as a slow computer does.
            context.add_init_script("""(() => { const f = window.fetch; let armed = true; window.HELD = [];
                window.fetch = (u, o) => { if (armed && String(u).includes('/api/writes')) { armed = false;
                    return new Promise(r => window.HELD.push(r)).then(() => f(u, o)); } return f(u, o); }; })()""")
            page = context.new_page()
            self.browser_login(page)
            expect(page.locator('#sync')).to_contain_text('1 job', timeout=10000)
            page.click('.seg [data-v="board"]')
            button = page.locator('.jobviews [data-jv="progress"]')
            before = button.bounding_box()['y']
            page.evaluate('() => HELD.forEach(r => r())')
            time.sleep(1.5)
            self.assertEqual(button.bounding_box()['y'], before, 'the view buttons moved after sign-in')
            browser.close()

    def test_sign_out_reaches_sign_in_when_request_fails(self):
        """Bug B4: Sign out always ends on the sign-in page, even if the sign-out request fails."""
        from playwright.sync_api import sync_playwright, expect
        self.serve_tls()
        with sync_playwright() as p:
            browser = p.webkit.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, ignore_https_errors=True)
            page = context.new_page()
            self.browser_login(page)
            page.route('**/api/logout', lambda route: route.abort())
            page.get_by_role('button', name='Sign out', exact=True).click()
            expect(page.locator('#password')).to_be_visible(timeout=5000)
            # A sign-out request that never answers must not keep the person on the page either.
            page = browser.new_context(viewport={'width': 1440, 'height': 900}, ignore_https_errors=True).new_page()
            self.browser_login(page)
            page.route('**/api/logout', lambda route: None)
            page.get_by_role('button', name='Sign out', exact=True).click()
            expect(page.locator('#password')).to_be_visible(timeout=8000)
            browser.close()

    def test_posted_filter_browser(self):
        """Step 02b: each card says how old the job is; the Posted filter narrows Board and List (desktop, then phone)."""
        from datetime import date, timedelta
        from playwright.sync_api import sync_playwright, expect
        ago = lambda n: (date.today() - timedelta(days=n)).isoformat()
        store_spec = importlib.util.spec_from_file_location('tracker_store_posted', HERE.parent / 'tracker' / 'store.py')
        store = importlib.util.module_from_spec(store_spec)
        store_spec.loader.exec_module(store)
        store.db_path(self.book).unlink()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Fictional jobs'
        ws.append(['#', 'Company', 'Role', 'Posted', 'My status', 'Date applied', 'Notes', 'Link'])
        rows = [('Today Co', ago(0)), ('Fresh Co', f'{ago(2)} (Lever)'), ('LinkedIn Co', f'{ago(1)} (LinkedIn)'),
                ('Week Co', f'{ago(6)} (Greenhouse)'), ('Month Co', ago(20)), ('Undated Co', None)]
        for n, (company, posted) in enumerate(rows, 1):
            ws.append([n, company, 'Example Engineer', posted, 'Review', '', 'Example only', f'https://example.com/{n}'])
        wb.save(self.book)
        wb.close()
        self.assertTrue(store.migrate(self.book, apply=True)['ok'])
        con = sqlite3.connect(store.db_path(self.book))
        con.execute("UPDATE jobs SET first_seen=? WHERE first_seen IS NULL OR first_seen=''", (ago(0),))
        con.execute("UPDATE jobs SET first_seen=? WHERE company='Undated Co'", (ago(10),))
        con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
        con.commit()
        con.close()
        lines = {'Today Co': 'Posted today', 'Fresh Co': 'Posted 2 days ago', 'LinkedIn Co': 'Posted about 1 day ago',
                 'Week Co': 'Posted 6 days ago', 'Month Co': 'Posted 20 days ago', 'Undated Co': 'First seen about 10 days ago'}
        self.login()
        self.serve_tls()
        shots = os.environ.get('HQ_SHOT_DIR')
        with sync_playwright() as p:
            browser = p.webkit.launch()
            for name, options in (('desktop', {'viewport': {'width': 1440, 'height': 900}}), ('phone', p.devices['iPhone 15'])):
                context = browser.new_context(**options, ignore_https_errors=True)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                self.browser_login(page)
                expect(page.locator('#sync')).to_contain_text('6 jobs', timeout=10000)
                page.click('.seg [data-v="board"]')
                for company, line in lines.items():
                    expect(page.locator('.bcard', has_text=company).locator('.posted-age')).to_have_text(line)
                cards = page.locator('.bcard')
                expect(cards).to_have_count(6)
                page.evaluate('openFilters()')
                panel = page.locator('#fdrawer')
                for label in ('Last 3 days', '1 week', '2 weeks', '3 weeks', '4 weeks', 'All'):
                    expect(panel.get_by_role('button', name=label, exact=True)).to_be_visible()
                expect(panel.get_by_role('button', name='All', exact=True)).to_have_attribute('aria-pressed', 'true')
                panel.get_by_role('button', name='1 week', exact=True).click()
                expect(panel.get_by_role('button', name='1 week', exact=True)).to_have_attribute('aria-pressed', 'true')
                if shots:
                    page.wait_for_timeout(1000)
                    page.screenshot(path=str(Path(shots) / f'02b-filter-{name}.png'), full_page=False)
                page.evaluate('closeDrawers()')
                expect(cards).to_have_count(4)
                for gone in ('Month Co', 'Undated Co'):
                    expect(page.locator('.bcard', has_text=gone)).to_have_count(0)
                expect(page.locator('#fcount')).to_be_visible()
                if shots:
                    page.wait_for_timeout(1000)
                    page.screenshot(path=str(Path(shots) / f'02b-board-{name}.png'), full_page=False)
                page.click('.seg [data-v="list"]')
                expect(page.locator('.lcard')).to_have_count(4)
                expect(page.locator('.lcard', has_text='Week Co').locator('.posted-age')).to_have_text('Posted 6 days ago')
                if shots:
                    page.wait_for_timeout(1000)
                    page.screenshot(path=str(Path(shots) / f'02b-list-{name}.png'), full_page=False)
                page.reload()
                expect(page.locator('#sync')).to_contain_text('6 jobs', timeout=10000)
                page.click('.seg [data-v="list"]')
                expect(page.locator('.lcard')).to_have_count(4)
                page.evaluate('openFilters()')
                page.locator('#fdrawer').get_by_role('button', name='Last 3 days', exact=True).click()
                page.evaluate('closeDrawers()')
                expect(page.locator('.lcard')).to_have_count(3)
                chip = page.locator('.fchip[data-rm="posted"]')
                expect(chip).to_contain_text('Posted')
                chip.click()
                expect(page.locator('.lcard')).to_have_count(6)
                page.evaluate('openFilters()')
                page.locator('#fdrawer').get_by_role('button', name='2 weeks', exact=True).click()
                page.evaluate('clearFilters()')
                page.evaluate('closeDrawers()')
                expect(page.locator('.lcard')).to_have_count(6)
                self.assertEqual(errors, [], name)
                context.close()
            browser.close()


    def test_job_views_browser(self):
        """Build step 1 (S1-S3): five job views, To do by default; closed postings and Skipped out of it; a simpler filter panel (desktop, then phone)."""
        from playwright.sync_api import sync_playwright, expect
        store_spec = importlib.util.spec_from_file_location('tracker_store_views', HERE.parent / 'tracker' / 'store.py')
        store = importlib.util.module_from_spec(store_spec)
        store_spec.loader.exec_module(store)
        store.db_path(self.book).unlink()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Fictional jobs'
        ws.append(['#', 'Company', 'Role', 'My status', 'Date applied', 'Notes', 'Link', 'Open check 2026-01-05'])
        rows = [('Open Review Co', 'Review', 'OPEN (fictional)'), ('Unchecked Tracked Co', 'Tracked', None),
                ('Open Apply Co', 'Apply', 'OPEN (fictional)'), ('Closed Review Co', 'Review', 'CLOSED (fictional)'),
                ('Applied Closed Co', 'Applied', 'CLOSED (fictional)'), ('Interview Co', 'Interview', 'OPEN (fictional)'),
                ('Skipped Co', 'Skipped', 'OPEN (fictional)'), ('Rejected Co', 'Rejected', None)]
        for n, (company, status, check) in enumerate(rows, 1):
            ws.append([n, company, 'Example Analyst', status, '', 'Example only', f'https://example.com/{n}', check])
        wb.save(self.book)
        wb.close()
        self.assertTrue(store.migrate(self.book, apply=True)['ok'])
        views = {'todo': ('To do', ['Open Review Co', 'Unchecked Tracked Co', 'Open Apply Co']),
                 'progress': ('In progress', ['Applied Closed Co', 'Interview Co']),
                 'skipped': ('Skipped', ['Skipped Co']),
                 'closed': ('Closed', ['Closed Review Co', 'Rejected Co']),
                 'all': ('All', [r[0] for r in rows])}
        self.login()
        self.serve_tls()
        shots = os.environ.get('HQ_SHOT_DIR')
        with sync_playwright() as p:
            browser = p.webkit.launch()
            for name, options in (('desktop', {'viewport': {'width': 1440, 'height': 900}}), ('phone', p.devices['iPhone 15'])):
                context = browser.new_context(**options, ignore_https_errors=True)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                self.browser_login(page)
                expect(page.locator('#sync')).to_contain_text('8 jobs', timeout=10000)
                # The page opens on the List tab with the To do view.
                expect(page.locator('.seg [data-v="list"]')).to_have_attribute('aria-selected', 'true')
                bar = page.locator('.jobviews')
                expect(bar.locator('[data-jv="todo"]')).to_have_attribute('aria-pressed', 'true')
                for key, (label, companies) in views.items():
                    button = bar.locator(f'[data-jv="{key}"]')
                    expect(button).to_contain_text(label)
                    expect(button.locator('.n')).to_have_text(str(len(companies)))
                if shots:
                    page.wait_for_timeout(1000)
                    page.screenshot(path=str(Path(shots) / f'01d-todo-{name}.png'), full_page=False)
                for key, (label, companies) in views.items():
                    bar.locator(f'[data-jv="{key}"]').click()
                    expect(bar.locator(f'[data-jv="{key}"]')).to_have_attribute('aria-pressed', 'true')
                    expect(page.locator('.lcard')).to_have_count(len(companies))
                    for company in companies:
                        expect(page.locator('.lcard', has_text=company)).to_have_count(1)
                    expect(page.locator('.list-view-wrap h2').first).to_contain_text(label)
                # Board: views filter cards while all stage columns stay in place.
                bar.locator('[data-jv="todo"]').click()
                page.click('.seg [data-v="board"]')
                expect(page.locator('.jobviews [data-jv="todo"]')).to_have_attribute('aria-pressed', 'true')
                expect(page.locator('.col[data-stage]')).to_have_count(8)
                expect(page.locator('.bcard')).to_have_count(3)
                page.locator('.jobviews [data-jv="progress"]').click()
                for stage in ('applied', 'interview', 'offer'):
                    expect(page.locator(f'.col[data-stage="{stage}"]')).to_have_count(1)
                expect(page.locator('.col[data-stage="skipped"] .bcard')).to_have_count(0)
                expect(page.locator('.bcard')).to_have_count(2)
                page.locator('.jobviews [data-jv="all"]').click()
                expect(page.locator('.bcard')).to_have_count(8)
                # Score action is the filter for the card 04b action labels.
                page.evaluate('openFilters()')
                panel = page.locator('#fdrawer')
                for heading in ('Posted', 'Place', 'Posting', 'Score action'):
                    expect(panel.get_by_role('heading', name=heading, exact=True)).to_be_visible()
                for gone in ('Status', 'Verdict', 'List', 'Country / region'):
                    expect(panel.get_by_role('heading', name=gone, exact=True)).to_have_count(0)
                expect(panel).not_to_contain_text('Hide closed')
                expect(panel).not_to_contain_text('Posting open only')
                for label, n in (('Open', 4), ('Closed', 2), ('Not checked', 2)):
                    expect(panel.locator('label.opt', has_text=label).locator('.n')).to_have_text(str(n))
                panel.locator('label.opt', has_text='Closed').locator('input').check()
                page.evaluate('closeDrawers()')
                expect(page.locator('.bcard')).to_have_count(2)
                for company in ('Closed Review Co', 'Applied Closed Co'):
                    expect(page.locator('.bcard', has_text=company)).to_have_count(1)
                page.evaluate('clearFilters()')
                expect(page.locator('.bcard')).to_have_count(8)
                expect(page.locator('body')).not_to_contain_text('Hide closed')
                # The chosen view survives a reload; the page still opens on List.
                page.locator('.jobviews [data-jv="skipped"]').click()
                page.reload()
                expect(page.locator('#sync')).to_contain_text('8 jobs', timeout=10000)
                expect(page.locator('.seg [data-v="list"]')).to_have_attribute('aria-selected', 'true')
                expect(page.locator('.jobviews [data-jv="skipped"]')).to_have_attribute('aria-pressed', 'true')
                expect(page.locator('.lcard')).to_have_count(1)
                # Overview still counts every job.
                page.click('.seg [data-v="overview"]')
                expect(page.locator('#main')).to_contain_text('8')
                no_scroll = page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
                self.assertTrue(no_scroll, f'{name}: page scrolls sideways')
                if shots:
                    page.click('.seg [data-v="list"]')
                    page.wait_for_timeout(1000)
                    page.screenshot(path=str(Path(shots) / f'01d-skipped-{name}.png'), full_page=False)
                page.evaluate('localStorage.clear()')
                self.assertEqual(errors, [], name)
                context.close()
            browser.close()

    def test_background_refresh_keeps_clicks_and_typing(self):
        """Bug B3 (2026-11-14): a background refresh must not replace the buttons a person is clicking or wipe what they typed.

        CI lost clicks on the job views and typed round details because the documents list arriving
        after load re-rendered the whole List view. Same data must not re-render; a re-render keeps typed text.
        """
        from playwright.sync_api import sync_playwright, expect
        self.login_and_move_to_interview()
        self.serve_tls()
        with sync_playwright() as p:
            browser = p.webkit.launch()
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, ignore_https_errors=True)
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            self.browser_login(page)
            expect(page.locator('#sync')).to_contain_text('1 job', timeout=10000)
            expect(page.locator('.seg [data-v="list"]')).to_have_attribute('aria-selected', 'true')
            # 1. The same documents arriving again does not rebuild the view.
            page.evaluate('window.__jv = document.querySelector(".jobviews [data-jv=skipped]")')
            page.evaluate('pcDocs({cvs: CVS, letters: CLS, reports: REPORTS})')
            self.assertTrue(page.evaluate('window.__jv && window.__jv.isConnected'),
                            'unchanged documents must not replace the view buttons')
            # 2. A re-render keeps round details that are typed but not saved yet (drawer opens from the Board).
            page.click('.seg [data-v="board"]')
            page.locator('.jobviews [data-jv="progress"]').click()
            job = self.app.data()['jobs'][0]
            page.evaluate('(id) => openJob(id)', job['_id'])
            drawer = page.locator('#drawer')
            drawer.get_by_role('button', name='Add round').click()
            expect(drawer.locator('.round')).to_have_count(2, timeout=10000)
            second = drawer.locator('.round[data-round="2"]')
            second.locator('input[data-field="type"]').fill('Recruiter call')
            second.locator('input[data-field="note"]').fill('Fictional note')
            page.evaluate('render()')
            second = drawer.locator('.round[data-round="2"]')
            expect(second.locator('input[data-field="type"]')).to_have_value('Recruiter call')
            expect(second.locator('input[data-field="note"]')).to_have_value('Fictional note')
            self.assertEqual(errors, [])
            context.close()
            browser.close()

if __name__ == '__main__':
    unittest.main(verbosity=2)
