"""Password protected PC hosting with durable saves.

Only the listed routes exist: no directory serving or arbitrary shell endpoint.
Typed AI tasks run through the restricted worker; search reads bounded public job boards.
Secrets and login throttle state live outside the checkout, in LOCALAPPDATA.
"""
import argparse
from contextlib import closing
import getpass
import hashlib
import hmac
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DEFAULT_STATE = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'JobSearchHQ'
# Private state path with public request limits.
REQUEST_DEADLINE_SECONDS = 30
AI_UPLOAD_DEADLINE_SECONDS = 600
MAX_REQUEST_WORKERS = 32
LOGIN = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in — Job Search HQ</title>
<style>body{font:18px system-ui;background:#f5f6fa;color:#182034;margin:0;padding:24px}main{max-width:380px;margin:12vh auto;background:white;padding:28px;border-radius:20px}input,button{box-sizing:border-box;width:100%;font:inherit;padding:14px;margin:12px 0;border:1px solid #afb7c8;border-radius:10px}button{background:#244acb;color:white}p{line-height:1.5}</style>
<main><h1>Job Search HQ</h1><p>Your private job-search desk, running on your PC.</p>
<form method="post" action="/api/login"><label for="password">HQ password</label>
<input id="password" name="password" type="password" autocomplete="current-password" required maxlength="512">
<button>Sign in</button></form></main></html>'''


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def set_password(state):
    password = getpass.getpass('Choose an HQ password (at least 16 characters): ')
    if len(password) < 16 or len(password) > 512:
        raise SystemExit('Use 16 to 512 characters. Nothing was saved.')
    if password != getpass.getpass('Type it again: '):
        raise SystemExit('Passwords did not match. Nothing was saved.')
    state.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_hex(16)
    # The raw password is never written or printed.
    temp = state / 'password.new'
    temp.write_text(json.dumps({'salt': salt, 'hash': password_hash(password, salt)}), encoding='utf-8')
    temp.replace(state / 'password.json')
    print('Password hash saved outside the repository. Restart HQ to use it.')


def unlock(state):
    """Local-only: clear the login lockout. Needs file access to the state folder, so a web visitor cannot use it.
    The throttle is read from disk on every login, so no restart is needed."""
    db_path = state / 'login-throttle.sqlite3'
    if not db_path.exists():
        raise SystemExit('No lockout record found. Nothing to clear.')
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute('UPDATE throttle SET failures=0,until=0 WHERE id=1')
    print('Sign-in lockout cleared. No restart needed.')


def load_tracker(workbook):
    spec = importlib.util.spec_from_file_location('pc_tracker', HERE.parent / 'tracker' / 'server.py')
    tracker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tracker)
    tracker.XLSX = Path(workbook).resolve()
    tracker.BACKUPS = tracker.XLSX.parent / '.tracker-backups'
    return tracker


def load_queue():
    spec = importlib.util.spec_from_file_location('hq_write_queue', HERE / 'write_queue.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.WriteQueue


def load_component(name):
    spec=importlib.util.spec_from_file_location('hq_'+name,HERE/(name+'.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class App:
    def __init__(self, state, workbook, origin):
        self.state = Path(state).resolve()
        self.credentials = json.loads((self.state / 'password.json').read_text(encoding='utf-8'))
        self.tracker = load_tracker(workbook)
        self.origin = origin.rstrip('/')
        u = urlsplit(self.origin)
        if u.scheme not in ('http', 'https') or u.path or u.query or u.fragment or u.username or u.password:
            raise ValueError('Origin must be a bare URL, with no path.')
        if u.scheme != 'https' and u.hostname not in ('127.0.0.1', 'localhost'):
            raise ValueError('Public origin must use HTTPS.')
        self.host = u.netloc
        self.sessions = {}
        self.auth_lock = threading.RLock()
        self.db_path = self.state / 'login-throttle.sqlite3'
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS throttle (id INTEGER PRIMARY KEY, failures INTEGER, until REAL)')
            db.execute('INSERT OR IGNORE INTO throttle VALUES (1,0,0)')
        self.tracker.Conflict.hq_conflict = True
        self.queue = load_queue()(self.state, self.write)
        self.scan_log_dir = ROOT / 'work' / 'logs'
        ai_module = load_component('ai_tasks')
        self.ai = ai_module.Tasks(self.state)
        self.rubric_version = ai_module.RUBRIC_VERSION
        # Worker imports/checks the canonical v3.2 policy before serving analysis.
        #
        # Public employer-board discovery has its own durable journal and never
        # writes to the tracker.
        self.search = load_component('job_search').Searches(self.state)
        self.files = load_component('pc_files')
        self.cv_work = ROOT/'work'

    def health(self):
        scan = {'state': 'unknown', 'message': 'No scan result is available yet.'}
        # Preserve the private runner's log names through settings.
        logs = sorted(self.scan_log_dir.glob(load_component('hq_settings').load()['scan_log_glob']))
        if logs:
            # Read only the log tail; never expose the prompt or posting contents.
            with logs[-1].open('rb') as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 8192))
                tail = stream.read().decode('utf-8', errors='replace')
            match = re.search(r'=== ([^\r\n]+) end \(exit (\d+)\) ===\s*$', tail)
            if match:
                scan = {'state': 'finished' if match[2] == '0' else 'failed', 'finishedAt': match[1],
                        'message': ('Last scan finished: ' if match[2] == '0' else 'Last scan failed: ') + match[1]}
            else:
                scan = {'state': 'unconfirmed', 'message': 'The latest scan has no completion message yet. It may be running or interrupted.'}
        return {'server': 'running', 'workbookAvailable': bool(self.tracker.version()),
                'aiWorker': 'configured; live call not yet verified', 'pendingWrites': sum(w['status'] in ('waiting', 'applying') for w in self.queue.recent()), 'scan': scan}

    def login(self, password):
        # Funnel hides the client IP. A durable account-wide throttle avoids trusting spoofable headers.
        with self.auth_lock, closing(sqlite3.connect(self.db_path)) as db, db:
            failures, until = db.execute('SELECT failures,until FROM throttle WHERE id=1').fetchone()
            now = time.time()
            if until > now:
                return None, 429
            if until:
                failures = 0
            valid = hmac.compare_digest(password_hash(password, self.credentials['salt']), self.credentials['hash'])
            if not valid:
                failures += 1
                db.execute('UPDATE throttle SET failures=?,until=? WHERE id=1', (failures, now + 900 if failures >= 5 else 0))
                return None, 401
            db.execute('UPDATE throttle SET failures=0,until=0 WHERE id=1')
            self.sessions = {k: expiry for k, expiry in self.sessions.items() if expiry > now}
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = now + 43200
            return token, 200

    def session_key(self, cookie):
        try:
            c = SimpleCookie(cookie)
            token = c['hq_session'].value
            key = hashlib.sha256(token.encode()).hexdigest()
            with self.auth_lock:
                return key if self.sessions.get(key, 0) > time.time() else None
        except (KeyError, ValueError):
            return None

    def data(self):
        with self.tracker.LOCK:
            d = json.loads(self.tracker.cached())
            for j in d['jobs']:
                j['_id'] = j['_sheet'] + '|' + str(j['_row'])
            for c in d['contacts']:
                c['_id'] = 'Contacts|' + str(c['_row'])
            return d

    def write(self, path, payload):
        t = self.tracker
        with t.LOCK, t.workbook_lock(t.XLSX):
            if payload.get('version') != t.version():
                raise t.Conflict('The spreadsheet changed. Reload before saving this edit.')
            # Reject formula injection and silent Excel truncation before calling the existing writer.
            allowed = set(t.ADD_FIELDS) if path == '/api/add' else {'status', 'date', 'note'}
            for field in allowed:
                value = payload.get(field)
                if value is not None and not isinstance(value, str):
                    raise ValueError('Text fields must contain text.')
                if isinstance(value, str) and (len(value) > 32767 or len(value.encode('utf-16-le')) // 2 > 32767):
                    raise ValueError('That text is too long for a spreadsheet cell.')
                if isinstance(value, str) and t.is_formula_text(value):
                    raise ValueError('Text cannot start with an equals sign. Add a word before it.')
            if path == '/api/update':
                expected = payload.get('expect')
                keys = ('Person',) if payload.get('sheet') == 'Contacts' else ('Company', 'Role')
                if not isinstance(expected, dict) or any(not expected.get(k) for k in keys):
                    raise ValueError('Reload this entry before editing it.')
                t.update(payload)
                return {'ok': True, 'version': t.version()}
            row, saved = t.add(payload)
            return {'ok': True, 'row': row, 'id': payload['sheet'] + '|' + str(row), 'saved': saved, 'version': t.version()}


def attachment(name):
    # Header-safe ASCII file name; the bytes served never depend on it.
    return {'Content-Disposition':'attachment; filename="'+(re.sub(r'[^A-Za-z0-9._-]','_',name) or 'document')+'"'}


class BoundedHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address, RequestHandlerClass, *,
                 request_deadline_seconds=REQUEST_DEADLINE_SECONDS,
                 max_workers=MAX_REQUEST_WORKERS):
        self.request_deadline_seconds = request_deadline_seconds
        self._workers = threading.BoundedSemaphore(max_workers)
        self._deadlines = {}
        super().__init__(server_address, RequestHandlerClass)

    def get_request(self):
        request, client_address = super().get_request()
        self._deadlines[request] = time.monotonic() + self.request_deadline_seconds
        return request, client_address

    def process_request(self, request, client_address):
        if not self._workers.acquire(blocking=False):
            self._deadlines.pop(request, None)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._deadlines.pop(request, None)
            self._workers.release()
            raise

    def finish_request(self, request, client_address):
        self.RequestHandlerClass(request, client_address, self,
                                 request_deadline=self._deadlines[request])

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._deadlines.pop(request, None)
            self._workers.release()


class DeadlineReader(io.RawIOBase):
    """Check the absolute deadline on every recv, including buffered readline."""
    def __init__(self, connection, deadline):
        super().__init__()
        self.connection = connection
        self.deadline = deadline

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Request deadline exceeded')
        self.connection.settimeout(min(15, remaining))
        size = self.connection.recv_into(buffer)
        if time.monotonic() >= self.deadline:
            raise TimeoutError('Request deadline exceeded')
        return size


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        def __init__(self, *args, request_deadline=None, **kwargs):
            self.request_deadline = (time.monotonic() + REQUEST_DEADLINE_SECONDS
                                     if request_deadline is None else request_deadline)
            super().__init__(*args, **kwargs)

        def setup(self):
            super().setup()
            self.rfile.close()
            self.rfile = io.BufferedReader(DeadlineReader(self.connection, self.request_deadline))
            self.connection.settimeout(15)

        def end_headers(self):
            # The request read budget must not shorten response/download writes.
            self.connection.settimeout(15)
            super().end_headers()

        def log_message(self, *args):
            pass  # Never log passwords, request bodies, cookie values or job content.

        def send(self, status, body='', content_type='application/json', headers=None):
            if not isinstance(body, (str, bytes)):
                body = json.dumps(body)
            body = body.encode('utf-8') if isinstance(body, str) else body
            self.send_response(status)
            for k, v in {'Content-Type': content_type, 'Content-Length': str(len(body)), 'Cache-Control': 'no-store',
                         'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
                         # no-referrer makes normal form POSTs send Origin: null.
                         # Preserve same-origin form provenance without sending referrers to other sites.
                         'Referrer-Policy': 'same-origin',
                         'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'",
                         **(headers or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return len(self.headers.get_all('Host', [])) == 1 and self.headers.get('Host') == app.host

        def do_GET(self):
            if not self.valid_host():
                return self.send(404)
            if self.path == '/login':
                return self.send(200, LOGIN, 'text/html; charset=utf-8')
            if not app.session_key(self.headers.get('Cookie', '')):
                return self.send(401, {'error': 'Sign in required.'}) if self.path.startswith('/api/') else self.send(303, headers={'Location': '/login'})
            try:
                if self.path in ('/', '/index.html'):
                    return self.send(200, (HERE / 'pc-index.html').read_bytes(), 'text/html; charset=utf-8')
                if self.path == '/api/data':
                    return self.send(200, app.data())
                if self.path == '/api/version':
                    return self.send(200, {'version': app.tracker.version()})
                if self.path == '/api/writes':
                    return self.send(200, {'writes': app.queue.recent()})
                if self.path == '/api/health':
                    return self.send(200, app.health())
                if self.path == '/api/ai/state':
                    return self.send(200, {'rubric_version':app.rubric_version,'tasks':app.ai.list_tasks(),'postings':app.ai.postings()})
                if self.path == '/api/search/state':
                    return self.send(200, app.search.state())
                if self.path == '/api/cvs':
                    return self.send(200, {'files':app.files.existing(app.cv_work)})
                if self.path.startswith('/api/cvs/'):
                    path=app.files.existing_file(self.path.removeprefix('/api/cvs/'),app.cv_work)
                    return self.send(200,path.read_bytes(),app.files.MIME,attachment(path.name))
                # Documents tab (Claude · HQ Builder (Opus), 2026-11-05): CVs, cover letters and reports, all allowlisted.
                if self.path == '/api/docs':
                    docs=app.files.documents(app.cv_work)
                    return self.send(200,{'cvs':[d for d in docs if d['kind']=='cv'],'letters':[d for d in docs if d['kind']=='cl'],
                                          'reports':app.files.reports(app.cv_work)})
                if self.path.startswith('/api/docs/'):
                    path=app.files.existing_file(self.path.removeprefix('/api/docs/'),app.cv_work,('cv','cl'))
                    return self.send(200,path.read_bytes(),app.files.MIME,attachment(path.name))
                if self.path.startswith('/api/reports/'):
                    key,_,fmt=self.path.removeprefix('/api/reports/').partition('/')
                    if fmt=='':
                        return self.send(200,app.files.report_view(key,app.cv_work))
                    if fmt=='md':
                        path,_=app.files.report_file(key,app.cv_work)
                        return self.send(200,path.read_bytes(),app.files.MD_MIME,attachment(path.name))
                    if fmt=='docx':
                        name,body=app.files.report_docx(key,app.cv_work)
                        return self.send(200,body,app.files.MIME,attachment(name))
                return self.send(404)
            except ValueError:
                return self.send(404, {'error':'Unknown document.'})
            except OSError:
                return self.send(503, {'error': 'Cannot read the spreadsheet right now. Try again shortly.'})
            except Exception:
                return self.send(500, {'error': 'Could not load this page. Check the local setup.'})

        def do_POST(self):
            if not self.valid_host():
                return self.send(404)
            if self.headers.get('Origin') != app.origin:
                return self.send(403, {'error': 'Open HQ directly and try again.'})
            if self.headers.get('Transfer-Encoding'):
                return self.send(400)
            try:
                length = int(self.headers.get('Content-Length', '-1'))
                maximum = 4096 if self.path == '/api/login' else (56*1024*1024 if self.path == '/api/ai/submit' else 262144)
                if not 0 <= length <= maximum:
                    return self.send(413)
                if self.path != '/api/login' and not app.session_key(self.headers.get('Cookie', '')):
                    return self.send(401, {'error': 'Sign in required.'})
                if self.path == '/api/ai/submit':
                    # Signed-in phone uploads of photos can be large and slow; only the unauthenticated routes keep the short deadline.
                    self.rfile.raw.deadline = max(self.rfile.raw.deadline, time.monotonic() + AI_UPLOAD_DEADLINE_SECONDS)
                content_type = self.headers.get('Content-Type', '').split(';')[0]
                chunks = []
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                self.connection.settimeout(15)
                raw = b''.join(chunks).decode('utf-8')
                if self.path == '/api/login':
                    if content_type != 'application/x-www-form-urlencoded':
                        return self.send(415)
                    password = parse_qs(raw).get('password', [''])[0]
                    if len(password) > 512:
                        return self.send(413)
                    token, status = app.login(password)
                    if not token:
                        return self.send(status, 'Sign in failed. After five failures, wait 15 minutes. Return to sign in to retry.', 'text/plain; charset=utf-8')
                    return self.send(303, headers={'Location': '/', 'Set-Cookie': f'hq_session={token}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=43200'})
                if self.path == '/api/logout':
                    with app.auth_lock:
                        app.sessions.pop(app.session_key(self.headers.get('Cookie', '')), None)
                    return self.send(200, {'ok': True}, headers={'Set-Cookie': 'hq_session=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0'})
                if self.path not in ('/api/add', '/api/update', '/api/writes/dismiss','/api/ai/submit','/api/ai/link','/api/ai/export','/api/search/submit'):
                    return self.send(404)
                if content_type != 'application/json':
                    return self.send(415)
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    return self.send(400)
                if self.path=='/api/search/submit':
                    try:
                        return self.send(202, {'id': app.search.submit(payload)})
                    except ValueError as error:
                        return self.send(400, {'error': str(error)})
                if self.path=='/api/ai/submit':
                    return self.send(202,{'id':app.ai.submit(payload)})
                if self.path=='/api/ai/link':
                    record=app.ai.posting(payload.get('id'))
                    job_id=payload.get('jobId')
                    if not any(j['_id']==job_id for j in app.data()['jobs']):raise ValueError('Unknown tracker entry.')
                    record['jobId']=job_id
                    with app.ai.connect() as db,db:
                        db.execute('UPDATE postings SET body=? WHERE id=?',(json.dumps(record),payload['id']))
                    return self.send(200,{'ok':True})
                if self.path=='/api/ai/export':
                    record=app.ai.posting(payload.get('id'))
                    path=app.files.export_draft(app.state,payload['id'],payload.get('kind'),record)
                    return self.send(200,path.read_bytes(),app.files.MIME,attachment(path.name))
                if self.path == '/api/writes/dismiss':
                    return self.send(200, {'write': app.queue.dismiss(payload.get('id'))})
                item = app.queue.submit(self.path, payload)
                if item['status'] == 'saved':
                    return self.send(200, {**item['result'], 'writeId': item['id']})
                if item['status'] == 'waiting':
                    return self.send(202, {'queued': True, 'write': item})
                return self.send(409 if item['status'] in ('conflict', 'needs_check') else 400,
                                 {'error': item['message'], 'write': item})
            except (TimeoutError, ConnectionError):
                self.close_connection = True
                return
            except app.tracker.Conflict as e:
                return self.send(409, {'error': str(e)})
            except PermissionError:
                return self.send(409, {'error': 'Close the spreadsheet in Excel, then save again. This edit has not been saved.'})
            except (ValueError, KeyError, TypeError):
                return self.send(400, {'error': 'Invalid edit. Check the fields and reload if needed.'})
            except Exception:
                return self.send(500, {'error': 'Could not save. Your edit has not been confirmed.'})
    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', type=Path, default=DEFAULT_STATE)
    p.add_argument('--workbook', type=Path, default=ROOT / 'work' / 'applications-tracker.xlsx')
    p.add_argument('--port', type=int, default=8770)
    p.add_argument('--origin', default='http://127.0.0.1:8770')
    p.add_argument('--set-password', action='store_true')
    p.add_argument('--unlock', action='store_true', help='clear the sign-in lockout (local use only)')
    a = p.parse_args()
    if a.state.resolve().is_relative_to(ROOT):
        p.error('Password state must be outside the repository.')
    if a.unlock:
        return unlock(a.state)
    if a.set_password:
        return set_password(a.state)
    if not (a.state / 'password.json').exists():
        p.error('First run this command with --set-password to choose your private password.')
    if not (HERE / 'pc-index.html').exists():
        p.error('First run port_tracker.py --backend pc.')
    # Acquire before opening journals: a duplicate start must not reset a live task.
    with load_tracker(a.workbook).workbook_lock(a.state/'server-instance'):
        app = App(a.state, a.workbook, a.origin)
        print(f'HQ listening only on 127.0.0.1:{a.port}. Expected browser origin: {app.origin}',flush=True)
        server = BoundedHTTPServer(('127.0.0.1', a.port), handler(app))
        app.queue.start()
        app.ai.start()
        app.search.start()
        try:
            server.serve_forever()
        finally:
            server.server_close()
            app.queue.stop()
            app.ai.stop()
            app.search.stop()


if __name__ == '__main__':
    main()
