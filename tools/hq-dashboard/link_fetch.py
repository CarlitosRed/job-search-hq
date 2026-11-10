"""Best-effort fetch of a job-posting link for the HQ posting review. Standard library only.

Runs on the HQ server, never inside Codex (Codex keeps web access off). The link is untrusted:
only public http/https addresses on ports 80/443 are opened, every redirect hop is re-checked, the
connection goes to the address that was checked (no second DNS lookup), and size and time are capped.
Anything unusual returns None and the review falls back to the pasted text or photos.
"""
import html, http.client, ipaddress, json, re, socket, ssl, threading, time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 1_500_000
TIMEOUT = 10
TOTAL_SECONDS = 20  # whole fetch, however many hops: a watchdog closes the sockets at this point
MAX_HOPS = 3
MIN_USEFUL = 300  # characters of text below which the page is treated as "nothing found"
AGENT = 'Mozilla/5.0 (compatible; JobSearchHQ posting reader)'


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


def public_address(host):
    """Return one public IP for host, or None when any resolved address is private, local or reserved."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
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


def _get(url, accept, deadline=None):
    """GET with manual redirects. Returns (final_url, content_type, text) or None."""
    deadline = deadline or Deadline()
    for _ in range(MAX_HOPS + 1):
        if deadline.expired():
            return None
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or '@' in parts.netloc:
            return None
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        if port not in (80, 443):
            return None
        ip = public_address(parts.hostname)
        if not ip:
            return None
        target = (parts.path or '/') + ('?' + parts.query if parts.query else '')
        headers = {'Host': parts.netloc.split('@')[-1], 'User-Agent': AGENT, 'Accept': accept,
                   'Accept-Encoding': 'identity', 'Connection': 'close'}
        try:
            if parts.scheme == 'https':
                raw = socket.create_connection((ip, port), timeout=TIMEOUT)
                sock = ssl.create_default_context().wrap_socket(raw, server_hostname=parts.hostname)
                conn = http.client.HTTPSConnection(parts.hostname, port, timeout=TIMEOUT)
            else:
                sock = socket.create_connection((ip, port), timeout=TIMEOUT)
                conn = http.client.HTTPConnection(parts.hostname, port, timeout=TIMEOUT)
            deadline.socks.append(sock)
            conn.sock = sock  # reuse the connection to the address that was checked
            conn.request('GET', target, headers=headers)
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                conn.close()
                if not location:
                    return None
                url = urljoin(url, location)
                continue
            if response.status != 200:
                conn.close()
                return None
            kind = (response.getheader('Content-Type') or '').split(';')[0].strip().lower()
            chunks, size = [], 0
            while size <= MAX_BYTES and not deadline.expired():
                piece = response.read(16384)
                if not piece:
                    break
                chunks.append(piece)
                size += len(piece)
            conn.close()
            if deadline.expired():
                return None
            body = b''.join(chunks)
        except (OSError, http.client.HTTPException, ssl.SSLError):
            return None
        if len(body) > MAX_BYTES or kind not in ('text/html', 'application/json', 'text/plain', 'application/xhtml+xml'):
            return None
        return url, kind, body.decode('utf-8', 'replace')
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
        except ValueError:
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


def fetch_posting_text(link):
    """Readable posting text from a public link, or None when it could not be read well."""
    if not isinstance(link, str) or not link.startswith(('https://', 'http://')) or len(link) > 2000:
        return None
    deadline = Deadline()
    try:
        return _fetch_posting_text(link, deadline)
    finally:
        deadline.stop()


def _fetch_posting_text(link, deadline):
    api = workday_api(link)
    if api:
        got = _get(api, 'application/json', deadline)
        if got and got[1] == 'application/json':
            try:
                info = json.loads(got[2]).get('jobPostingInfo') or {}
            except ValueError:
                info = {}
            if info.get('jobDescription'):
                head = '\n'.join(str(info[k]) for k in ('title', 'location', 'timeType', 'remoteType') if info.get(k))
                text = (head + '\n\n' if head else '') + html_to_text(str(info['jobDescription']))
                if len(text) >= MIN_USEFUL:
                    return text[:40000]
    got = _get(link, 'text/html,application/xhtml+xml,text/plain', deadline)
    if not got:
        return None
    markup = got[2]
    text = _json_ld_posting(markup) if got[1] != 'text/plain' else markup
    if len(text) < MIN_USEFUL:
        text = html_to_text(markup) if got[1] != 'text/plain' else markup
    return text[:40000] if len(text) >= MIN_USEFUL else None
