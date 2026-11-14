"""Bounded public job-board discovery.

Only public employer board endpoints are queried. Search results are
snapshots, not applications or eligibility decisions. No workbook, model, API key,
browser session, user-supplied URL or shell is used. The dashboard owner explicitly
chooses whether to add each result through the existing workbook save queue.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import hashlib
import html
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


# Board tokens are public identifiers, not credentials. Some boards can move or
# become unavailable; each run reports source failures instead of hiding them.
SOURCES = (
    {'id': 'gitlab', 'label': 'GitLab', 'provider': 'greenhouse', 'board': 'gitlab'},
    {'id': 'canonical', 'label': 'Canonical', 'provider': 'greenhouse', 'board': 'canonical'},
    {'id': 'grafana', 'label': 'Grafana Labs', 'provider': 'greenhouse', 'board': 'grafanalabs'},
    {'id': 'figma', 'label': 'Figma', 'provider': 'greenhouse', 'board': 'figma'},
    {'id': 'datadog', 'label': 'Datadog', 'provider': 'greenhouse', 'board': 'datadog'},
    {'id': 'asana', 'label': 'Asana', 'provider': 'greenhouse', 'board': 'asana'},
    {'id': 'elastic', 'label': 'Elastic', 'provider': 'greenhouse', 'board': 'elastic'},
    {'id': 'includedhealth', 'label': 'Included Health', 'provider': 'lever', 'board': 'includedhealth'},
)
MAX_RESULTS = 40
MAX_BYTES = 4 * 1024 * 1024
MAX_BOARD_BYTES = 32 * 1024 * 1024
MAX_POSTINGS = 5000
TIMEOUT = 12
_HOST_LOCK = threading.Lock()
_HOST_SLOTS = {}
STOP_WORDS = {'the', 'and', 'or', 'for', 'with', 'jobs', 'job', 'roles', 'role', 'to', 'in', 'a', 'an'}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def clean_text(value, limit):
    value = html.unescape(html.unescape(str(value or '')))
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]*>', ' ', value)).strip()[:limit]


def load_component(name):
    spec = importlib.util.spec_from_file_location('search_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configured_sources():
    path = load_component('hq_settings').data_root() / 'profile.json'
    if not path.exists():
        return SOURCES
    companies = load_component('search_profile').load(path)['search']['companies']
    return tuple(dict(company, id=f'profile-{index}') for index, company in enumerate(companies))


def phrase_matches(phrase, text):
    """Match a complete phrase after ignoring case and punctuation."""
    words = re.findall(r'\w+', str(phrase).casefold())
    haystack = re.findall(r'\w+', str(text).casefold())
    return bool(words) and (' ' + ' '.join(words) + ' ') in (' ' + ' '.join(haystack) + ' ')


def profile_posting_matches(profile, title, text):
    return any(any(phrase_matches(phrase, title) for phrase in lane['titles']) or
               any(phrase_matches(phrase, title + ' ' + text) for phrase in lane['duties'])
               for lane in profile['goals']['lanes'])


def validate(payload):
    if not isinstance(payload, dict) or set(payload) - {'query', 'location', 'remote', 'requestId'}:
        raise ValueError('Use keywords, a location and the remote filter only.')
    query = payload.get('query')
    location = payload.get('location', '')
    remote = payload.get('remote', False)
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 300:
        raise ValueError('Enter 2 to 300 characters of job-title keywords.')
    if not isinstance(location, str) or len(location) > 120 or not isinstance(remote, bool):
        raise ValueError('Use a location of at most 120 characters and a remote checkbox.')
    if any(ord(c) < 32 for c in query + location):
        raise ValueError('Search fields must be plain text on one line.')
    if not keywords(query):
        raise ValueError('Enter at least one job-title keyword of two characters.')
    return {'query': query.strip(), 'location': location.strip(), 'remote': remote}


def keywords(query):
    return tuple(dict.fromkeys(t for t in re.findall(r'[\w+#.-]{2,}', query.casefold()) if t not in STOP_WORDS))[:30]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('The public job-board endpoint redirected. Review this source configuration.')


def source_url(source):
    board = source.get('board', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', board):
        raise ValueError('Invalid job-board token.')
    if source.get('provider') == 'greenhouse':
        return 'https://boards-api.greenhouse.io/v1/boards/' + quote(board) + '/jobs'
    if source.get('provider') == 'lever':
        return 'https://api.lever.co/v0/postings/' + quote(board) + '?mode=json&limit=500'
    if source.get('provider') == 'ashby':
        return 'https://api.ashbyhq.com/posting-api/job-board/' + quote(board)
    if source.get('provider') == 'workday':
        host, site = source.get('host', ''), source.get('site', '')
        if not re.fullmatch(r'[A-Za-z0-9-]+\.wd\d+\.myworkdayjobs\.com', host) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', site):
            raise ValueError('Use a public Workday jobs host and site token.')
        return f'https://{host}/wday/cxs/{board}/{site}/jobs'
    raise ValueError('Only Greenhouse, Lever, Ashby and Workday public job boards are supported.')


def fetch_json(method, url, body=None):
    """Bounded, paced HTTP transport for the fixed public board endpoints."""
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    fixed = host in ('boards-api.greenhouse.io', 'api.lever.co', 'api.ashbyhq.com')
    workday = re.fullmatch(r'[A-Za-z0-9-]+\.wd\d+\.myworkdayjobs\.com', host)
    if not safe_link(url) or not (fixed or workday) or method not in ('GET', 'POST') or (method == 'POST' and not workday):
        raise ValueError('Use a supported public job-board endpoint.')
    data = None if body is None else json.dumps(body).encode('utf-8')
    request = Request(url, data=data, method=method, headers={'Accept': 'application/json', 'Content-Type': 'application/json', 'User-Agent': 'JobSearchHQ/1.0 (public job-board reader)'})
    with _HOST_LOCK:
        slot = _HOST_SLOTS.setdefault(host, threading.BoundedSemaphore(2))
    with slot:
        with build_opener(NoRedirect).open(request, timeout=TIMEOUT) as response:
            data = response.read(MAX_BOARD_BYTES + 1)
    if len(data) > MAX_BOARD_BYTES:
        raise ValueError('Job board response exceeded the size limit.')
    return json.loads(data)


def board_rows(source, fetch=fetch_json, detail_match=None, today=None):
    """Shared board adapters; detail_match gates the bounded Workday detail reads."""
    url = source_url(source)
    provider = source['provider']
    if provider != 'workday':
        if provider == 'greenhouse':
            url += '?content=true'
        data = fetch('GET', url)
        rows = data.get('jobs') if provider in ('greenhouse', 'ashby') and isinstance(data, dict) else data
        if not isinstance(rows, list) or len(rows) > MAX_POSTINGS:
            raise ValueError('Unexpected or oversized job-board response.')
        return rows
    rows, offset, details = [], 0, 0
    today = today or date.today()
    while True:
        data = fetch('POST', url, {'appliedFacets': {}, 'limit': 20, 'offset': offset, 'searchText': ''})
        if not isinstance(data, dict) or not isinstance(data.get('jobPostings'), list):
            raise ValueError('Unexpected Workday response.')
        total, page = data.get('total'), data['jobPostings']
        if type(total) is not int or not 0 <= total <= MAX_POSTINGS or len(page) > 20 or len(rows) + len(page) > MAX_POSTINGS:
            raise ValueError('Unexpected or oversized Workday response.')
        for listing in page:
            if not isinstance(listing, dict):
                continue
            raw = dict(listing)
            path = raw.get('externalPath', '')
            if not isinstance(path, str) or not re.fullmatch(r'/job/[A-Za-z0-9_/%.-]+', path) or '..' in path or '%' in path:
                continue
            raw['_link'] = f"https://{source['host']}/{source['site']}" + path
            raw['_posted'], raw['_date_source'] = '', ''
            posted = clean_text(raw.get('postedOn'), 100).casefold()
            days = re.fullmatch(r'posted (\d+) days? ago', posted)
            count = int(days[1]) if days else 0 if posted == 'posted today' else 1 if posted == 'posted yesterday' else None
            if count is not None and count <= 36500:
                raw['_posted'] = (today - timedelta(days=count)).isoformat()
                raw['_date_source'] = 'workday-about'
            listing_text = clean_text(' '.join(
                ' '.join(str(part) for part in value) if isinstance(value, list) else str(value)
                for key, value in listing.items() if key not in ('externalPath', 'postedOn')
            ), 20000)
            raw['description'] = listing_text
            if details < 40 and detail_match and detail_match(raw.get('title', ''), listing_text):
                details += 1
                detail = fetch('GET', url.rsplit('/', 1)[0] + path)
                info = detail.get('jobPostingInfo') if isinstance(detail, dict) else None
                if not isinstance(info, dict):
                    raise ValueError('Unexpected Workday job details.')
                raw['description'] = listing_text + ' ' + str(info.get('jobDescription') or '')
                raw['posting_id'] = info.get('jobReqId')
                exact = iso_day(info.get('startDate'))
                if exact:
                    raw['_posted'], raw['_date_source'] = exact, 'workday'
            rows.append(raw)
        offset += len(page)
        if offset >= total:
            break
        if not page:
            raise ValueError('Workday paging stopped before all postings were read.')
    return rows


def fetch_source(source, detail_match=None):
    rows = board_rows(source, detail_match=detail_match)
    return rows if source['provider'] == 'lever' else {'jobs': rows}


def iso_day(value):
    if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value[:10]):
        try:
            return date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            pass
    return ''


def safe_link(value):
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 33 for c in value):
        return ''
    try:
        parsed = urlsplit(value)
        # Links are returned by the official provider API. They are never fetched
        # by this service. HTTPS without credentials is still required for opening.
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
            return ''
    except ValueError:
        return ''
    return value


def normalize(source, raw, checked):
    if not isinstance(raw, dict):
        return None
    posted, date_source = '', ''
    if source['provider'] == 'greenhouse':
        role = clean_text(raw.get('title'), 200)
        loc = raw.get('location') or {}
        location = clean_text(loc.get('name') if isinstance(loc, dict) else '', 300)
        link = safe_link(raw.get('absolute_url'))
        published = raw.get('first_published')
        if isinstance(published, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', published[:10]):
            try:
                posted = date.fromisoformat(published[:10]).isoformat()
                date_source = 'greenhouse'
            except ValueError:
                pass
    elif source['provider'] == 'lever':
        role = clean_text(raw.get('text'), 200)
        categories = raw.get('categories') or {}
        location = clean_text(categories.get('location') if isinstance(categories, dict) else '', 300)
        if raw.get('workplaceType') == 'remote' and 'remote' not in location.casefold():
            location = (location + ' · Remote').strip(' ·')
        link = safe_link(raw.get('hostedUrl'))
        created = raw.get('createdAt')
        if isinstance(created, int) and not isinstance(created, bool) and created > 0:
            try:
                posted = datetime.fromtimestamp(created / 1000, timezone.utc).date().isoformat()
                date_source = 'lever'
            except (ValueError, OverflowError, OSError):
                pass
    elif source['provider'] == 'ashby':
        role = clean_text(raw.get('title'), 200)
        location = clean_text(raw.get('location'), 300)
        if raw.get('isRemote') is True and 'remote' not in location.casefold():
            location = (location + ' Remote').strip()
        link = safe_link(raw.get('jobUrl'))
        posted = iso_day(raw.get('publishedAt'))
        date_source = 'ashby' if posted else ''
    else:
        role = clean_text(raw.get('title'), 200)
        location = clean_text(raw.get('locationsText'), 300)
        link = safe_link(raw.get('_link'))
        posted, date_source = raw.get('_posted', ''), raw.get('_date_source', '')
    if not role or not link:
        return None
    key = hashlib.sha256((source.get('id', source['label']) + '\n' + link).encode()).hexdigest()[:24]
    posting_id = raw.get('posting_id') or raw.get('id')
    if posting_id is None and source['provider'] == 'workday':
        posting_id = (raw.get('bulletFields') or [None])[0]
    description = raw.get('content') if source['provider'] == 'greenhouse' else raw.get('descriptionPlain') if source['provider'] in ('lever', 'ashby') else raw.get('description')
    return {'id': key, 'company': source['label'], 'role': role, 'location': location or 'Not specified',
            'provider': source['provider'], 'posting_id': str(posting_id) if posting_id is not None else link,
            'remote': bool(re.search(r'\b(remote|anywhere|distributed)\b', location, re.I)),
            'description': clean_text(description, 20000),
            'postedOn': posted, 'dateSource': date_source,
            'link': link, 'source': source['label'] + ' (' + source['provider'].title() + ')', 'checkedAt': checked,
            'summary': 'Listed on the employer job board when checked. Read the posting for country and work-authorization requirements.'}


def matches(result, query):
    title_words = set(re.findall(r'[\w+#.-]+', result['role'].casefold()))
    if not any(term in title_words or (len(term) >= 4 and term in result['role'].casefold()) for term in keywords(query['query'])):
        return False
    location = result['location'].casefold()
    locations = [part.strip().casefold() for part in query['location'].split(',') if part.strip()]
    if locations and not any(term in location for term in locations):
        return False
    if query['remote'] and not any(term in location for term in ('remote', 'anywhere', 'distributed')):
        return False
    return True


def discover(query, fetcher=fetch_source, sources=None, progress=None):
    """Fetch a bounded source set; retain partial results and visible errors."""
    query = validate(query)
    sources = configured_sources() if sources is None else sources
    if not 1 <= len(sources) <= 300:
        raise ValueError('Choose between one and 300 job-board sources.')
    profile = None
    if fetcher is fetch_source:
        path = load_component('hq_settings').data_root() / 'profile.json'
        if path.exists():
            profile = load_component('search_profile').load(path)

    def detail_match(title, text):
        if profile is not None:
            return profile_posting_matches(profile, title, text)
        return any(phrase_matches(word, title + ' ' + text) for word in keywords(query['query']))

    checked = now()
    results, errors, completed = [], [], 0
    # Apply ordering after concurrent reads so repeated fixtures remain stable.
    by_source = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='hq-job-board') as pool:
        def read(source):
            if fetcher is fetch_source:
                return fetch_source(source, detail_match)
            return fetcher(source)
        pending = {pool.submit(read, source): index for index, source in enumerate(sources)}
        for future in as_completed(pending):
            index = pending[future]
            source = sources[index]
            try:
                data = future.result()
                rows = data.get('jobs') if source['provider'] != 'lever' and isinstance(data, dict) else data
                if not isinstance(rows, list) or len(rows) > MAX_POSTINGS:
                    raise ValueError('Unexpected or oversized job-board response.')
                found = []
                for raw in rows:
                    result = normalize(source, raw, checked)
                    if result and matches(result, query):
                        found.append(result)
                by_source[index] = found
            except Exception:
                errors.append({'source': source['label'], 'message': 'This job board could not be checked. It may be unavailable or its format may have changed.'})
            completed += 1
            if progress:
                progress(completed, len(sources))
    seen = set()
    total = 0
    for index, source in enumerate(sources):
        for result in by_source.get(index, []):
            if result['link'] in seen:
                continue
            seen.add(result['link'])
            total += 1
            if len(results) < MAX_RESULTS:
                results.append(result)
    return {'results': results, 'sourcesChecked': completed - len(errors), 'sourceErrors': errors,
            'sourcesTotal': len(sources), 'totalMatches': total, 'truncated': total > MAX_RESULTS, 'checkedAt': checked}


class Searches:
    def __init__(self, state, fetcher=fetch_source, sources=None):
        self.path = Path(state) / 'job-searches.sqlite3'
        self.fetcher = fetcher
        self.use_profile = sources is None
        self.sources = configured_sources() if sources is None else sources
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        with self.connect() as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS searches (id TEXT PRIMARY KEY, signature TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, created TEXT NOT NULL, finished TEXT, result TEXT)')
            db.execute("UPDATE searches SET status='interrupted', message='HQ stopped during this search. Start a new search to check the job boards again.', finished=? WHERE status='running'", (now(),))

    def connect(self):
        return closing(sqlite3.connect(self.path, timeout=10))

    def submit(self, payload):
        query = validate(payload)
        request_id = payload.get('requestId')
        try:
            if str(uuid.UUID(request_id)) != request_id:
                raise ValueError()
        except (TypeError, ValueError, AttributeError):
            raise ValueError('Reload HQ before starting this search.')
        body = json.dumps(query, sort_keys=True)
        signature = hashlib.sha256(body.encode()).hexdigest()
        with self.lock, self.connect() as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT signature FROM searches WHERE id=?', (request_id,)).fetchone()
            if old:
                if old[0] != signature:
                    raise ValueError('This search request was already used for different keywords.')
                return request_id
            if db.execute("SELECT id FROM searches WHERE status IN ('queued','running') LIMIT 1").fetchone():
                raise ValueError('A search is already running. Wait for its result before starting another.')
            db.execute('INSERT INTO searches VALUES (?,?,?,?,?,?,?,?)', (request_id, signature, body, 'queued', 'Waiting to check the public job boards.', now(), None, None))
            db.execute("DELETE FROM searches WHERE id NOT IN (SELECT id FROM searches ORDER BY rowid DESC LIMIT 20) AND status NOT IN ('queued','running')")
        return request_id

    def state(self):
        if self.use_profile:
            self.sources = configured_sources()
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT id,body,status,message,created,finished,result FROM searches ORDER BY rowid DESC LIMIT 20').fetchall()
        searches = []
        for row in rows:
            item = dict(row)
            item.update(json.loads(item.pop('body')))
            item.update(json.loads(item.pop('result') or '{}'))
            item.setdefault('results', [])
            item.setdefault('sourcesChecked', 0)
            item.setdefault('sourceErrors', [])
            searches.append(item)
        return {'searches': searches, 'sources': [{'id': s['id'], 'label': s['label']} for s in self.sources]}

    def process_one(self):
        with self.lock, self.connect() as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT id,body FROM searches WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not row:
                return
            key, body = row
            db.execute("UPDATE searches SET status='running',message='Checking public employer job boards.' WHERE id=?", (key,))
        def progress(done, total):
            with self.connect() as db, db:
                db.execute('UPDATE searches SET message=? WHERE id=?', (f'Checked {done} of {total} public job boards.', key))
        try:
            if self.use_profile:
                self.sources = configured_sources()
            result = discover(json.loads(body), self.fetcher, self.sources, progress)
            status = 'completed' if result['sourcesChecked'] else 'failed'
            message = f"Found {len(result['results'])} matching postings across {result['sourcesChecked']} checked job boards."
            if result['truncated']:
                message += f" Showing the first {MAX_RESULTS} of {result['totalMatches']} matches; narrow the keywords to see more."
            if result['sourceErrors']:
                message += f" {len(result['sourceErrors'])} job board(s) could not be checked."
        except Exception:
            result = {}
            status, message = 'failed', 'This search could not finish. Start a new search to try again.'
        with self.connect() as db, db:
            db.execute('UPDATE searches SET status=?,message=?,finished=?,result=? WHERE id=?', (status, message, now(), json.dumps(result), key))

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        def run():
            while not self.stop_event.wait(.5):
                try:
                    self.process_one()
                except sqlite3.Error:
                    continue
        self.thread = threading.Thread(target=run, name='hq-job-search', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=45)


def main():
    parser = argparse.ArgumentParser(description='Check public employer job boards and save a JSON snapshot. Does not read or update any tracker workbook.')
    parser.add_argument('--query', required=True, help='Job-title keywords; any matching keyword is included.')
    parser.add_argument('--location', default='', help='Location text; comma-separated alternatives are accepted.')
    parser.add_argument('--remote', action='store_true', help='Only locations explicitly marked remote, anywhere or distributed.')
    parser.add_argument('--output', type=Path, required=True, help='New JSON snapshot path; an existing file is never overwritten.')
    args = parser.parse_args()
    query = validate({'query': args.query, 'location': args.location, 'remote': args.remote})
    if args.output.suffix.lower() != '.json':
        parser.error('--output must be a .json snapshot path.')
    # Exclusive create before fetching: accidentally targeting an existing file,
    # including a workbook, stops without a network call or an overwrite.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        result = discover(query)
        stream.write(json.dumps({'query': query, **result}, ensure_ascii=False, indent=2) + '\n')
    print(f"Checked {result['sourcesChecked']} job boards; {len(result['results'])} matches; {len(result['sourceErrors'])} source failures. Snapshot saved.")
    return 0 if result['sourcesChecked'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
