"""Best-effort fetch of a job-posting link for posting review and re-check.

Runs on the HQ server, never inside Codex (Codex keeps web access off). The link is untrusted:
only public http/https addresses on ports 80/443 are opened, every redirect hop is re-checked, the
connection goes to the address that was checked (no second DNS lookup), and size and time are capped.
Direct reads use only the standard library. Unreadable pages can use the optional browser reader.
"""
import html, http.client, ipaddress, json, re, socket, ssl, threading, time
from functools import cache
import importlib.util
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

MAX_BYTES = 1_500_000
TIMEOUT = 10
TOTAL_SECONDS = 20  # whole fetch, however many hops: a watchdog closes the sockets at this point
MAX_HOPS = 3
MIN_USEFUL = 300  # characters of text below which the page is treated as "nothing found"
MAX_TEXT = 40000
BROWSER_SECONDS = 30
AGENT = 'Mozilla/5.0 (compatible; JobSearchHQ posting reader)'
_BOT_CHECK = object()  # A recognized challenge ends the read, unlike a bare 403.
_INACTIVE_POSTING = object()


@cache
def _recheck():
    settings_spec = importlib.util.spec_from_file_location(
        'hq_owner_settings', Path(__file__).with_name('hq_settings.py'))
    hq_settings = importlib.util.module_from_spec(settings_spec)
    settings_spec.loader.exec_module(hq_settings)
    path = hq_settings.app_root() / 'tools' / 'tracker' / 'recheck.py'
    spec = importlib.util.spec_from_file_location('hq_reader_recheck', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _closed(text):
    return bool(text) and _recheck().classify(200, text)[0] == 'closed_text'


DENY = [ipaddress.ip_network(n) for n in ('64:ff9b::/96', '64:ff9b:1::/48', '::/96', '224.0.0.0/4', 'ff00::/8')]


class Deadline:
    """One clock for the whole fetch. Closing the sockets also stops a server that sends one byte at a time."""

    def __init__(self, seconds=TOTAL_SECONDS):
        self.end = time.monotonic() + seconds
        self.socks = []
        self.timer = threading.Timer(seconds, self._close)
        self.timer.daemon = True
        self.timer.start()

    def _close(self):
        for sock in self.socks:
            try:
                sock.shutdown(socket.SHUT_RDWR)  # wakes a read that is waiting on this socket; close() alone does not
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def expired(self):
        return time.monotonic() >= self.end

    def stop(self):
        self.timer.cancel()
        self._close()


# Card 03: closing and re-check.
def public_address(host, deadline=None):
    """Return one public IP for host, or None when any resolved address is private, local or reserved."""
    end = deadline.end if deadline is not None else time.monotonic() + TIMEOUT
    if time.monotonic() >= end:
        return None
    infos = []

    def lookup():
        try:
            infos.extend(socket.getaddrinfo(host, None, type=socket.SOCK_STREAM))
        except OSError:
            pass

    # Only resolve in the worker; a late result can never open a connection.
    worker = threading.Thread(target=lookup, daemon=True)
    worker.start()
    worker.join(max(0, end - time.monotonic()))
    if worker.is_alive() or time.monotonic() >= end:
        return None
    addresses = {info[4][0] for info in infos}
    if not addresses:
        return None
    for text in addresses:
        try:
            address = ipaddress.ip_address(text.split('%')[0])
            if not address.is_global or any(address in net for net in DENY if net.version == address.version):
                return None
        except ValueError:
            return None
    v4 = sorted(a for a in addresses if ':' not in a)
    return v4[0] if v4 else sorted(addresses)[0]


def workday_api(url):
    """A Workday posting page is built by JavaScript; its public JSON twin carries the text."""
    parts = urlsplit(url)
    match = re.match(r'^([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$', parts.hostname or '', re.I)
    if not match:
        return None
    segments = [s for s in parts.path.split('/') if s]
    if segments and re.fullmatch(r'[a-z]{2}(-[A-Z]{2})?', segments[0]):
        segments = segments[1:]
    if len(segments) < 3 or segments[1] != 'job':
        return None
    path = '/wday/cxs/%s/%s/%s' % (match.group(1), segments[0], '/'.join(segments[1:]))
    return 'https://%s%s' % (parts.hostname, path)


def greenhouse_api(url):
    parts = urlsplit(url)
    if parts.hostname in ('boards.greenhouse.io', 'job-boards.greenhouse.io',
                          'boards.eu.greenhouse.io', 'job-boards.eu.greenhouse.io'):
        match = re.fullmatch(r'/([\w-]+)/jobs/(\d+)/?', parts.path, re.ASCII)
        if match:
            board, job = match.groups()
            host = 'boards-api.eu.greenhouse.io' if '.eu.' in parts.hostname else 'boards-api.greenhouse.io'
            return f'https://{host}/v1/boards/{board}/jobs/{job}'


def smartrecruiters_api(url):
    parts = urlsplit(url)
    if parts.hostname == 'jobs.smartrecruiters.com':
        match = re.fullmatch(r'/([\w-]+)/(\d+)(?:-[^/]+)?/?', parts.path, re.ASCII)
        if match:
            company, job = match.groups()
            return f'https://api.smartrecruiters.com/v1/companies/{company}/postings/{job}'


def lever_api(url):
    parts = urlsplit(url)
    if parts.hostname in ('jobs.lever.co', 'jobs.eu.lever.co'):
        match = re.fullmatch(r'/([\w-]+)/([\w-]+)(?:/apply)?/?', parts.path, re.ASCII)
        if match:
            company, job = match.groups()
            host = 'api.eu.lever.co' if '.eu.' in parts.hostname else 'api.lever.co'
            return f'https://{host}/v0/postings/{company}/{job}'


def icims_page(url):
    parts = urlsplit(url)
    if (parts.hostname or '').endswith('.icims.com') and re.match(r'^/jobs/\d+(?:/|$)', parts.path):
        query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != 'in_iframe']
        return urlunsplit(parts._replace(query=urlencode(query + [('in_iframe', '1')]), fragment=''))


def public_url(url, resolve, deadline):
    """Return parsed HTTP(S) URL, port and checked IP, or None for a disallowed target."""
    try:
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or '@' in parts.netloc:
            return None
        port = parts.port if parts.port is not None else (443 if parts.scheme == 'https' else 80)
        if port not in (80, 443):
            return None
    except ValueError:
        return None
    ip = resolve(parts.hostname, deadline)
    return (parts, port, ip) if ip else None


def _get(url, accept, deadline=None, with_status=False):
    """GET with manual redirects, returning the final URL or status, content type, and body."""
    deadline = deadline or Deadline()
    for _ in range(MAX_HOPS + 1):
        if deadline.expired():
            return None
        checked = public_url(url, public_address, deadline)
        if not checked or deadline.expired():
            return None
        parts, port, ip = checked
        target = (parts.path or '/') + ('?' + parts.query if parts.query else '')
        headers = {'Host': parts.netloc.split('@')[-1], 'User-Agent': AGENT, 'Accept': accept,
                   'Accept-Encoding': 'identity', 'Connection': 'close'}
        code = None
        try:
            timeout = min(TIMEOUT, max(0, deadline.end - time.monotonic()))
            if timeout <= 0:
                return None
            if parts.scheme == 'https':
                raw = socket.create_connection((ip, port), timeout=timeout)
                deadline.socks.append(raw)
                raw.settimeout(min(timeout, max(0, deadline.end - time.monotonic())))
                sock = ssl.create_default_context().wrap_socket(raw, server_hostname=parts.hostname)
                conn = http.client.HTTPSConnection(parts.hostname, port, timeout=timeout)
            else:
                sock = socket.create_connection((ip, port), timeout=timeout)
                conn = http.client.HTTPConnection(parts.hostname, port, timeout=timeout)
            deadline.socks.append(sock)
            conn.sock = sock  # reuse the connection to the address that was checked
            conn.request('GET', target, headers=headers)
            response = conn.getresponse()
            code = response.status
            if (response.getheader('cf-mitigated') or '').strip().lower() == 'challenge':
                conn.close()
                return (_BOT_CHECK, '', '') if with_status else None
            redirect = code in (301, 302, 303, 307, 308)
            location = response.getheader('Location') if redirect else None
            kind = (response.getheader('Content-Type') or '').split(';')[0].strip().lower()
            readable = kind in ('text/html', 'text/plain', 'application/xhtml+xml')
            if code != 200 and not (with_status and readable):
                conn.close()
                if redirect and location:
                    url = urljoin(url, location)
                    continue
                return (code, '', '') if with_status else None
            chunks, size = [], 0
            while size <= MAX_BYTES and not deadline.expired():
                piece = response.read(16384)
                if not piece:
                    break
                chunks.append(piece)
                size += len(piece)
            conn.close()
            if deadline.expired():
                return (code, '', '') if code != 200 else None
            body = b''.join(chunks)
        except (OSError, http.client.HTTPException, ssl.SSLError):
            return (code, '', '') if with_status and code not in (None, 200) else None
        if readable and (redirect or len(body) > MAX_BYTES):
            title, visible = _page_text(kind, body[:MAX_BYTES].decode('utf-8', 'replace'))
            if _bot_check(title) or _bot_check(visible):
                return _BOT_CHECK, '', ''
        if redirect and location:
            url = urljoin(url, location)
            continue
        if len(body) > MAX_BYTES or kind not in ('text/html', 'application/json', 'text/plain', 'application/xhtml+xml'):
            return (code, '', '') if code != 200 else None
        return (code if with_status else url), kind, body.decode('utf-8', 'replace')
    return None


class _Text(HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'template', 'svg', 'head', 'nav', 'footer'}
    BREAK = {'p', 'br', 'li', 'div', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'ul', 'ol'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BREAK:
            self.out.append('\n')

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BREAK:
            self.out.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(markup):
    parser = _Text()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        return ''
    text = html.unescape(''.join(parser.out))
    lines = [re.sub(r'[ \t\r\f\v ]+', ' ', line).strip() for line in text.split('\n')]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def _json_ld_posting(markup):
    """Many career sites publish the whole posting as schema.org JobPosting data."""
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', markup, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except (ValueError, RecursionError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if 'JobPosting' in str(item.get('@type', '')) and item.get('description'):
                    head = ' '.join(str(item.get(k, '')) for k in ('title',) if item.get(k))
                    return (head + '\n\n' if head else '') + html_to_text(str(item['description']))
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
            elif isinstance(item, list):
                stack.extend(item)
    return ''


def _json_text(data, board):
    """Extract the description and useful headings from a public posting response."""
    if board is workday_api or 'jobPostingInfo' in data:
        info = data.get('jobPostingInfo')
        if not isinstance(info, dict):
            return ''
        description = html_to_text(info['jobDescription']) if isinstance(info.get('jobDescription'), str) else ''
        if not description:
            return ''
        heading = '\n'.join(str(info[key]) for key in ('title', 'location', 'timeType', 'remoteType') if info.get(key))
        return _join_posting(heading, description)
    if board is greenhouse_api:
        content = data.get('content')
        return _posting_sections(data.get('title'), html.unescape(content) if isinstance(content, str) else '')
    if board is smartrecruiters_api:
        ad = data.get('jobAd')
        sections = ad.get('sections') if isinstance(ad, dict) else {}
        if not isinstance(sections, dict):
            sections = {}
        descriptions = [_posting_sections(section.get('title'), section.get('text')) for section in sections.values()
                        if isinstance(section, dict)]
        return _join_posting(data.get('name'), '\n\n'.join(filter(None, descriptions)))
    if board is lever_api:
        lists = data.get('lists')
        sections = []
        if isinstance(lists, list):
            sections = [_posting_sections(item.get('text'), item.get('content')) for item in lists if isinstance(item, dict)]
        description = _posting_sections(None, data.get('description')) or _plain_text(data.get('descriptionPlain'))
        extra = _posting_sections(None, data.get('additional')) or _plain_text(data.get('additionalPlain'))
        return _join_posting(data.get('text'), '\n\n'.join(filter(None, [description, *sections, extra])))
    return ''


def _plain_text(value):
    return value.strip() if isinstance(value, str) else ''


def _join_posting(title, description):
    return '\n\n'.join(filter(None, (_plain_text(title), description))) if description else ''


def _posting_sections(title, markup):
    return _join_posting(title, html_to_text(_plain_text(markup)))


def _bot_check(text):
    sample = text[:2000].strip()
    return bool(re.match(r'just a moment\b[.!\u2026]*\s*(?:\n|$)', sample, re.I)
                or re.fullmatch(r'(?:captcha|human verification)[.!]?', sample, re.I)
                or re.search(r"\b(?:verif(?:y|ies|ying) (?:that )?you(?: are|['\u2019]re) (?:a human|human|not a (?:bot|robot))|"
                             r'checking your browser|performing (?:a )?security verification|'
                             r'enable javascript and cookies to continue|complete the (?:security check|captcha))\b',
                             ' '.join(sample.split()), re.I))


def _blocked_page(text):
    sample = text[:2000].strip()
    return bool(_bot_check(sample)
                or re.match(r'(?:access denied|forbidden)[.!\u2026]*\s*(?:\n|$)', sample, re.I)
                or re.fullmatch(r'(?:sign in|log in|login)[.!]?', sample, re.I)
                or re.search(r'\b(?:(?:sign|log) in to (?:view|continue)|you don.t have permission to access)\b',
                             sample, re.I))


def _page_text(kind, body):
    if kind == 'text/plain':
        return '', body.strip()
    title = re.search(r'<title\b[^>]*>(.*?)</title\s*>', body, re.I | re.S)
    return html_to_text(title[1]) if title else '', html_to_text(body)


def _read_direct(url, deadline, board=None):
    got = _get(url, 'text/html,application/json,text/plain,application/xhtml+xml', deadline, with_status=True)
    if not got:
        return None, ''
    code, kind, body = got
    text = ''
    if kind in ('text/html', 'application/xhtml+xml', 'text/plain'):
        title, visible = _page_text(kind, body)
        if _bot_check(title) or _bot_check(visible):
            return _BOT_CHECK, ''
        if code == 200:
            visible = visible[:MAX_TEXT]
            if not _blocked_page(title) and not _blocked_page(visible) and _closed(visible):
                return code, visible
            if kind != 'text/plain':
                text = _json_ld_posting(body)
            if len(text) < MIN_USEFUL:
                if _blocked_page(title):
                    return 403, ''
                text = visible
    elif kind == 'application/json' and code == 200:
        try:
            data = json.loads(body)
        except (ValueError, RecursionError):
            data = None
        if isinstance(data, dict):
            text = _json_text(data, board)
            if board is smartrecruiters_api and data.get('active') is False:
                notice = _recheck().EXPIRED_NOTICE
                text = '\n\n'.join(filter(None, (notice, text)))
                return _INACTIVE_POSTING, text[:MAX_TEXT]
    if code != 200:
        return code, ''
    return (403, '') if _blocked_page(text) else (code, text[:MAX_TEXT])


def _direct(link, deadline):
    mapped = (None, '')
    for board in (workday_api, greenhouse_api, smartrecruiters_api, lever_api, icims_page):
        target = board(link)
        if target:
            mapped = _read_direct(target, deadline, board)
            if (target == link or mapped[0] in (_BOT_CHECK, _INACTIVE_POSTING)
                    or len(mapped[1]) >= MIN_USEFUL or _closed(mapped[1])):
                return mapped
            break
    direct = _read_direct(link, deadline)
    if direct[0] in (404, 410) or len(direct[1]) >= MIN_USEFUL or _closed(direct[1]):
        return direct
    if direct[0] in (_BOT_CHECK, 403, 429):
        return direct
    return mapped if len(mapped[1]) > len(direct[1]) or mapped[0] in (403, 429) else direct


def browser_reader(link, timeout):
    """The browser hook is replaceable in tests and loaded only when needed."""
    spec = importlib.util.spec_from_file_location('hq_browser_read', Path(__file__).with_name('browser_read.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.read(link, timeout, public_address, public_url, MAX_BYTES, MAX_TEXT, MIN_USEFUL, _bot_check, _closed)


def fetch_status_text(link, timeout=None):
    """Return the status and readable text from the direct read or optional browser."""
    if not isinstance(link, str) or len(link) > 2000 or not link.startswith(('https://', 'http://')):
        return None, ''
    start = time.monotonic()
    seconds = TOTAL_SECONDS if timeout is None else min(timeout, TOTAL_SECONDS)
    if seconds <= 0:
        return None, ''
    deadline = Deadline(seconds)
    try:
        # Validate the supplied URL before transforming it into a board URL.
        parts = urlsplit(link)
        if not parts.hostname or '@' in parts.netloc or parts.port not in (None, 80, 443):
            return None, ''
        code, text = _direct(link, deadline)
    except (OSError, ValueError):
        return None, ''
    finally:
        deadline.stop()
    if code is _BOT_CHECK:
        return 403, ''
    if code is _INACTIVE_POSTING:
        return 200, text
    if code in (404, 410) or len(text) >= MIN_USEFUL or _closed(text):
        return code, text
    remaining = BROWSER_SECONDS if timeout is None else min(BROWSER_SECONDS, timeout - (time.monotonic() - start))
    if remaining > 0:
        result = browser_reader(link, remaining)
        if result:
            browser_code, rendered = result
            if browser_code in (404, 410):
                return browser_code, ''
            if _blocked_page(rendered):
                browser_code, rendered = 403, ''
            if browser_code == 200 and rendered.strip():
                return 200, rendered[:MAX_TEXT]
            if (browser_code in (403, 429)
                    or (code == 200 and browser_code is not None and 500 <= browser_code < 600)):
                return browser_code, ''
    return code, text


def fetch_posting_text(link, *, with_status=False):
    """Return useful posting text, optionally accompanied by its reader status."""
    code, text = fetch_status_text(link)
    text = text if code == 200 and (len(text) >= MIN_USEFUL or _closed(text)) else None
    return (code, text) if with_status else text
