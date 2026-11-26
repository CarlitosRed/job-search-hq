"""Local credentials and the loopback ChatGPT authorization flow."""
import base64
from contextlib import contextmanager
from datetime import datetime
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import re
import secrets
from tempfile import NamedTemporaryFile
from threading import Thread
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

AUTH = 'https://auth.openai.com'
RESOURCE = 'https://api.openai.com/v1'
TOKEN_URL = AUTH + '/api/accounts/oauth/token'
REVOKE_URL = AUTH + '/oauth/revoke'
SCOPE = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
SIGN_IN = 'Sign in with ChatGPT again on the settings page.'
CONFIG = 'ChatGPT sign-in configuration was refused. Check the settings page.'
TOKEN_FIELDS = ('access_token', 'refresh_token', 'id_token', 'token_type',
                'expires_in', 'scope', 'earliest_refresh_at', 'expires_at')
EXPIRED = {'invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired',
           'refresh_token_invalidated', 'refresh_token_reused'}
PLAN_ERROR = {
    'subscription_sharing_usage_limit_exceeded':
        "Your ChatGPT plan's usage limit is reached; the search agent will try at the next run.",
    'subscription_sharing_usage_unavailable': 'ChatGPT plan usage is temporarily unavailable.',
    'subscription_sharing_user_not_eligible': 'This account is not eligible for ChatGPT plan usage.',
    'subscription_sharing_route_not_supported': 'This request is unavailable through ChatGPT plan usage.',
    'subscription_sharing_unsupported_capability': 'This ChatGPT plan or model does not support this capability.',
    'subscription_sharing_invalid_user': SIGN_IN,
}
MAX_BYTES = 8_000_000


class Stop(ValueError):
    """A fixed, credential-safe message for the owner."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def http_request(method, url, *, form=None, body=None, headers=None, read_timeout=60):
    headers = dict(headers or {})
    data = None
    if form is not None:
        data = urlencode(form).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    elif body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    request = Request(url, data=data, headers=headers, method=method)
    deadline = time.monotonic() + read_timeout
    try:
        response = build_opener(NoRedirect).open(request, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        parts, size = [], 0
        while time.monotonic() < deadline:
            chunk = response.read1(65536)
            if not chunk:
                if time.monotonic() < deadline:
                    return response.code, b''.join(parts)
                break
            size += len(chunk)
            if size > MAX_BYTES:
                break
            parts.append(chunk)
    raise Stop('OpenAI did not return a complete response within the read limit.')


def request(http, *args, **kwargs):
    try:
        return http(*args, **kwargs)
    except Exception:
        raise Stop('OpenAI could not be reached or the response could not be read.') from None


def json_object(raw):
    try:
        raw = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except (ValueError, RecursionError):
        return {}
    return raw if isinstance(raw, dict) else {}


def error_code(data):
    error = data.get('error') or data.get('detail')
    if isinstance(error, dict):
        error = error.get('code') or error.get('error') or error.get('message')
    if not isinstance(error, str):
        return ''
    # Some HTTP errors wrap the code in a plain-language detail.
    for code in PLAN_ERROR:
        if re.search(r'\b' + code + r'\b', error):
            return code
    return error


def bearer(token):
    return {'Authorization': 'Bearer ' + token}


@contextmanager
def file_lock(path):
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def write_private(path, data):
    with NamedTemporaryFile(dir=path.parent, delete=False, mode='w', encoding='utf-8') as stream:
        temporary = Path(stream.name)
        try:
            if os.name != 'nt':
                os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def pkce(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')


def token_bundle(data, now):
    required = TOKEN_FIELDS[:4]
    if (any(not isinstance(data.get(key), str) or not data[key] or re.search(r'\s', data[key])
            for key in required)
            or data['token_type'].lower() != 'bearer'
            or type(data.get('expires_in')) is not int or not 0 < data['expires_in'] <= 86400):
        raise Stop('ChatGPT returned an invalid sign-in response. Start sign-in again.')
    bundle = {key: data[key] for key in TOKEN_FIELDS[:-1] if key in data}
    bundle['expires_at'] = now + data['expires_in']
    refresh_time(bundle)
    return bundle


def refresh_time(data):
    value = data.get('earliest_refresh_at')
    if value is None:
        return 0
    try:
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    raise ValueError
                value = parsed.timestamp()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError
        return value
    except (ValueError, OverflowError, OSError):
        raise Stop('ChatGPT returned an invalid refresh time. Start sign-in again.') from None


def check_nonce(id_token, nonce):
    # The ID token comes directly from the authenticated TLS token endpoint.
    # Only the nonce is used, with no identity or permissions taken from the claims.
    try:
        _, payload, _ = id_token.split('.')
        claims = json_object(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        if not hmac.compare_digest(claims.get('nonce', ''), nonce):
            raise ValueError
    except (ValueError, TypeError):
        raise Stop('ChatGPT sign-in could not be verified. Start sign-in again.') from None


def owner_filename(owner):
    # Host metadata and arbitrary profile IDs must remain distinct on Windows as well.
    reserved = {'con', 'prn', 'aux', 'nul', *(f'{kind}{n}' for kind in ('com', 'lpt') for n in range(10))}
    if re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', owner) and owner not in reserved:
        return owner
    return '.' + hashlib.sha256(owner.encode()).hexdigest()


class Store:
    def __init__(self, root, owner, *, http=None, clock=time.time):
        self.owner = owner
        self.directory = Path(root) / 'work' / 'agent-auth'
        self.path = self.directory / (owner_filename(owner) + '.json')
        self.http = http or http_request
        self.clock = clock

    @contextmanager
    def locked(self, path=None):
        path = path or self.path
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with file_lock(path.with_suffix('.lock')):
                yield
        except (OSError, ValueError) as error:
            if isinstance(error, Stop):
                raise
            raise Stop('The search agent could not access its local credential store.') from None

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError
            return data
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise Stop('The search agent could not read its local credential store.') from None

    def set_key(self, key):
        if not isinstance(key, str) or key and not re.fullmatch(r'sk-[A-Za-z0-9_-]{5,512}', key):
            raise Stop('Enter an OpenAI API key beginning with sk-.')
        with self.locked():
            data = self.read()
            data.pop('key', None)
            if key:
                data['key'] = key
            write_private(self.path, data)

    def preferences(self, mode, model):
        if mode not in ('plan', 'api_key') or not isinstance(model, str) or (model and not valid_slug(model)):
            raise Stop('Choose a payment method and model from the settings page.')
        with self.locked():
            data = self.read()
            data.update(mode=mode, model=model)
            write_private(self.path, data)

    def view(self):
        data = self.read()
        key = data.get('key', '')
        return dict(mode=data.get('mode', 'api_key'), model=data.get('model', ''),
                    key_hint='sk-…' + key[-4:] if key else '', signed_in=bool(data.get('refresh_token')))

    def host_id(self):
        path = self.directory / '.install.json'
        with self.locked(path):
            if path.exists():
                return json.loads(path.read_text(encoding='utf-8'))['host_id']
            host_id = 'urn:uuid:' + str(uuid4())
            write_private(path, dict(host_id=host_id))
            return host_id

    def authorize(self, port, *, verifier=None, state=None, nonce=None):
        verifier = verifier or secrets.token_urlsafe(48)
        state = state or secrets.token_urlsafe(32)
        nonce = nonce or secrets.token_urlsafe(32)
        redirect = f'http://127.0.0.1:{port}/auth/callback'
        host_id = self.host_id()
        with self.locked():
            data = self.read()
            data['pending'] = generation = secrets.token_urlsafe(32)
            write_private(self.path, data)
        client = data.get('client_id')
        params = dict(client_id=client or 'dynamic_agent_client', ext_agent_host_id=host_id,
                      response_type='code', redirect_uri=redirect, scope=SCOPE, resource=RESOURCE,
                      state=state, nonce=nonce, code_challenge_method='S256', code_challenge=pkce(verifier))
        if not client:
            params['agent_name_hint'] = 'Job Search HQ'
        pending = dict(owner=self.owner, state=state, nonce=nonce, verifier=verifier,
                       redirect_uri=redirect, generation=generation, deadline=self.clock() + 300)
        return AUTH + '/api/accounts/authorize?' + urlencode(params), pending

    def callback(self, pending, values, address, session_valid=lambda: True):
        if (address != '127.0.0.1' or pending['owner'] != self.owner or self.clock() > pending['deadline']
                or not hmac.compare_digest(values.get('state', '').encode(), pending['state'].encode())):
            raise Stop('ChatGPT sign-in could not be verified. Start sign-in again.')
        with self.locked():
            data = self.read()
            if data.get('pending') != pending['generation']:
                raise Stop('This ChatGPT sign-in has expired. Start sign-in again.')
            issued = values.get('client_id')
            client = data.get('client_id') or issued
            if not re.fullmatch(r'oaiapp_[A-Za-z0-9_-]+', client or '') or (issued and issued != client):
                raise Stop(CONFIG)
            data.pop('pending')
            write_private(self.path, data)
            if not values.get('code'):
                raise Stop('ChatGPT sign-in was cancelled or expired. Start sign-in again.')
            raw = self.exchange(grant_type='authorization_code', client_id=client, code=values['code'],
                                code_verifier=pending['verifier'], redirect_uri=pending['redirect_uri'])
            bundle = token_bundle({**raw, 'scope': raw.get('scope', values.get('scope', ''))}, self.clock())
            check_nonce(bundle['id_token'], pending['nonce'])
            if not session_valid():
                raise Stop('The Job Search HQ session ended. Sign in and try again.')
            write_private(self.path, {**data, **bundle, 'client_id': client})

    def exchange(self, **form):
        status, raw = request(self.http, 'POST', TOKEN_URL, form={**form, 'resource': RESOURCE})
        data = json_object(raw)
        code = error_code(data)
        if status == 200 and not code:
            return data
        if code in EXPIRED:
            raise Stop(SIGN_IN)
        if code == 'invalid_client':
            raise Stop(CONFIG)
        raise Stop('ChatGPT sign-in could not be completed. Try again on the settings page.')

    def credential(self, mode):
        with self.locked():
            data = self.read()
            if mode == 'api_key':
                token = data.get('key')
                if not token:
                    raise Stop('Save an OpenAI API key on the settings page.')
            else:
                data = self.refresh(data)
                token = data['access_token']
            secrets_ = [data[key] for key in ('key', *TOKEN_FIELDS[:3]) if data.get(key)]
            return token, secrets_

    def refresh(self, data):
        if not data.get('refresh_token'):
            raise Stop(SIGN_IN)
        now, expiry = self.clock(), data['expires_at']
        if now < expiry - 60 or (now < expiry and now < refresh_time(data)):
            return data
        if now < refresh_time(data):
            raise Stop('ChatGPT plan usage cannot refresh yet. The search agent will try at the next run.')
        try:
            raw = self.exchange(grant_type='refresh_token', client_id=data['client_id'],
                                refresh_token=data['refresh_token'])
        except Stop as error:
            if str(error) == SIGN_IN:
                self.clear(data)
            raise
        raw.setdefault('id_token', data['id_token'])
        raw.setdefault('scope', data.get('scope', ''))
        changed = {**data, 'earliest_refresh_at': None, **token_bundle(raw, self.clock())}
        write_private(self.path, changed)
        return changed

    def clear(self, data):
        for key in (*TOKEN_FIELDS, 'pending'):
            data.pop(key, None)
        write_private(self.path, data)

    def invalidate(self, token):
        with self.locked():
            data = self.read()
            if data.get('access_token') == token:
                self.clear(data)

    def sign_out(self, endpoint=REVOKE_URL):
        with self.locked():
            data = self.read()
            try:
                if data.get('refresh_token') and urlsplit(endpoint).scheme == 'https':
                    request(self.http, 'POST', endpoint, form=dict(token=data['refresh_token'],
                            token_type_hint='refresh_token', client_id=data['client_id']))
            except (Stop, ValueError):
                pass
            finally:
                self.clear(data)


def valid_slug(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,119}', value)


def listen(store, session_valid):
    """Open a temporary callback socket separate from the proxied dashboard."""
    done = False

    class Callback(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(5)
            super().setup()

        def log_message(self, *_args):
            pass

        def do_GET(self):
            nonlocal done
            try:
                url = urlsplit(self.path)
                values = parse_qs(url.query, max_num_fields=20) if url.path == '/auth/callback' else {}
            except ValueError:
                values = {}
            if (self.client_address[0] != '127.0.0.1'
                    or self.headers.get_all('Host') != [f'127.0.0.1:{port}']
                    or not session_valid() or any(len(values.get(key, [])) > 1
                                                 for key in ('state', 'code', 'client_id', 'scope'))
                    or not hmac.compare_digest(values.get('state', [''])[0].encode(),
                                               pending['state'].encode())):
                status, message = 400, 'ChatGPT sign-in could not be verified.'
            else:
                done = True
                try:
                    store.callback(pending, {key: value[0] for key, value in values.items()},
                                   self.client_address[0], session_valid)
                    status, message = 200, 'ChatGPT sign-in complete. Return to the Job Search HQ settings page.'
                except Stop as error:
                    status, message = 400, str(error)
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(message.encode())

    listener = HTTPServer(('127.0.0.1', 0), Callback)
    port = listener.server_port
    try:
        url, pending = store.authorize(port)
    except BaseException:
        listener.server_close()
        raise

    def wait():
        listener.timeout = 1
        with listener:
            while not done and store.clock() < pending['deadline'] and session_valid():
                listener.handle_request()

    Thread(target=wait, daemon=True).start()
    return url
