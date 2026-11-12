"""Bounded public job-board discovery.

Only published Greenhouse and Lever GET endpoints are queried. Search results are
snapshots, not applications or eligibility decisions. No workbook, model, API key,
browser session, user-supplied URL or shell is used. The dashboard owner explicitly
chooses whether to add each result through the existing workbook save queue.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import date, datetime, timezone
import hashlib
import html
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
MAX_POSTINGS = 5000
TIMEOUT = 12
STOP_WORDS = {'the', 'and', 'or', 'for', 'with', 'jobs', 'job', 'roles', 'role', 'to', 'in', 'a', 'an'}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def clean_text(value, limit):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', str(value or '')))).strip()[:limit]


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
    raise ValueError('Only Greenhouse and Lever public job boards are supported.')


def fetch_source(source):
    request = Request(source_url(source), headers={'Accept': 'application/json', 'User-Agent': 'JobSearchHQ/1.0 (public job-board reader)'})
    with build_opener(NoRedirect).open(request, timeout=TIMEOUT) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Job board response exceeded the size limit.')
    return json.loads(data)


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
    else:
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
    if not role or not link:
        return None
    key = hashlib.sha256((source['id'] + '\n' + link).encode()).hexdigest()[:24]
    return {'id': key, 'company': source['label'], 'role': role, 'location': location or 'Not specified',
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


def discover(query, fetcher=fetch_source, sources=SOURCES, progress=None):
    """Fetch a bounded source set; retain partial results and visible errors."""
    query = validate(query)
    if not 1 <= len(sources) <= 12:
        raise ValueError('Choose between one and twelve job-board sources.')
    checked = now()
    results, errors, completed = [], [], 0
    # Apply ordering after concurrent reads so repeated fixtures remain stable.
    by_source = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='hq-job-board') as pool:
        pending = {pool.submit(fetcher, source): source for source in sources}
        for future in as_completed(pending):
            source = pending[future]
            try:
                data = future.result()
                rows = data.get('jobs') if source['provider'] == 'greenhouse' and isinstance(data, dict) else data
                if not isinstance(rows, list) or len(rows) > MAX_POSTINGS:
                    raise ValueError('Unexpected or oversized job-board response.')
                found = []
                for raw in rows:
                    result = normalize(source, raw, checked)
                    if result and matches(result, query):
                        found.append(result)
                by_source[source['id']] = found
            except Exception:
                errors.append({'source': source['label'], 'message': 'This job board could not be checked. It may be unavailable or its format may have changed.'})
            completed += 1
            if progress:
                progress(completed, len(sources))
    seen = set()
    total = 0
    for source in sources:
        for result in by_source.get(source['id'], []):
            if result['link'] in seen:
                continue
            seen.add(result['link'])
            total += 1
            if len(results) < MAX_RESULTS:
                results.append(result)
    return {'results': results, 'sourcesChecked': completed - len(errors), 'sourceErrors': errors,
            'sourcesTotal': len(sources), 'totalMatches': total, 'truncated': total > MAX_RESULTS, 'checkedAt': checked}


class Searches:
    def __init__(self, state, fetcher=fetch_source, sources=SOURCES):
        self.path = Path(state) / 'job-searches.sqlite3'
        self.fetcher, self.sources = fetcher, sources
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
