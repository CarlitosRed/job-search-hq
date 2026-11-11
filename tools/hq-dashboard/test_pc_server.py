# Fictional fixtures and settings-aware expectations.
# Activation updates: Codex - Activation Builder, 2026-11-07.
"""Synthetic integration checks; no real workbook or AI calls."""
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path
import secrets
import socket
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
from urllib.parse import urlencode

import openpyxl

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('hq_pc', HERE / 'server.py')
hq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hq)


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
            for path in source.glob('*.py'):
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
        ws.append([1, 'Example Company', 'Example Engineer', 'Suggested (not applied)', '', 'Example only', 'https://example.com/job'])
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
             'My status':'Suggested (not applied)','version':self.data()['version'],'requestId':str(uuid.uuid4())}
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
        self.server = hq.BoundedHTTPServer(('127.0.0.1',0),hq.handler(self.app))
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert,key)
        self.server.socket = tls.wrap_socket(self.server.socket,server_side=True)
        self.port = self.server.server_port
        self.app.origin = f'https://127.0.0.1:{self.port}'
        self.app.host = f'127.0.0.1:{self.port}'
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

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
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
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
            self.app.ai.bank_loader=lambda:{'rubric_version':'v3.2','rules':'Fictional only','entries':{'E-999':{'level':'stated','text':'Fictional skill'}}}
            def fake_runner(prompt,kind,folder,images):
                if kind=='analyze':return {'is_job_posting':True,'readable':True,'company':'Example Company','role':'Example Role','link':'','posting_text':'',
                                          'rubric_version':'v3.2','analysis':__import__('lead_policy_fixtures').fictional_analysis(),'summary':'Fictional result only','location_ok':'unclear','matches':[],'gaps':[],'flags':[]}
                if kind=='letter':return {'cover_letter_markdown':'Dear Example Company team,\n\nFictional skill.\n\nKind regards,\nAlex Example',
                                          'sources':['E-999'],'flags':[],'claims':[{'text':'Fictional skill.','evidence':['E-999']}]}
                return {'cv_markdown':'# Fictional CV\nFictional skill.','sources':['E-999'],'flags':[],
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
            expect(drawer.get_by_role('button', name='Download CV (.docx)')).to_be_visible()
            with page.expect_download() as download:
                drawer.get_by_role('button', name='Download cover letter (.docx)').click()
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
            with page.expect_download() as download:
                page.locator('#result [data-pc-doc="cl"]').click()
            letter = docx.Document(download.value.path())
            self.assertEqual({x.style.name for x in letter.paragraphs}, {'Normal'})
            self.assertNotIn('Sentiment', '\n'.join(x.text for x in letter.paragraphs))
            self.assertNotIn('DRAFT', download.value.suggested_filename)
            self.assertEqual(errors, [])
            page.get_by_role('button', name='Sign out', exact=True).click()
            page.wait_for_selector('#password')
            browser.close()



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


if __name__ == '__main__':
    unittest.main(verbosity=2)
