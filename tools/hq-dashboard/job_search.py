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
from email.utils import parsedate_to_datetime
import hashlib
import html
from http.client import HTTPException
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
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
_WORKDAY_SLOT = threading.Lock()
WORKDAY_PAGE_SIZE = 20
WORKDAY_DETAIL_LIMIT = 40
SEARCH_WINDOW_DAYS = 45
WORKDAY_ORDER_MARGIN_DAYS = 7
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
    path = load_component('search_profile').default_path()
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
        if fp is not None:
            fp.close()
        raise BoardReadError('redirected away from its configured endpoint', code)


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


def fetch_json(method, url, body=None, *, deadline=None):
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
    timeout = TIMEOUT if deadline is None else min(TIMEOUT, deadline - time.monotonic())
    if timeout <= 0 or not slot.acquire(timeout=timeout):
        raise TimeoutError('The job board request timed out.')
    try:
        if deadline is not None:
            timeout = min(TIMEOUT, deadline - time.monotonic())
            if timeout <= 0:
                raise TimeoutError('The job board request timed out.')
        with build_opener(NoRedirect).open(request, timeout=timeout) as response:
            if deadline is None:
                data = response.read(MAX_BOARD_BYTES + 1)
            else:
                data = bytearray()
                while len(data) <= MAX_BOARD_BYTES:
                    if time.monotonic() >= deadline:
                        raise BoardReadError('timed out reading its response', response.status)
                    chunk = response.read1(min(65536, MAX_BOARD_BYTES + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
    finally:
        slot.release()
    if len(data) > MAX_BOARD_BYTES:
        raise InvalidBoardResponse('returned a response above the size limit', 200)
    try:
        return json.loads(data)
    except ValueError:
        raise InvalidBoardResponse('returned invalid JSON', 200) from None


# Card 06-1d: home-country reads first, place from the job page
class BoardRows(list):
    reason = ''


class BoardReadError(ValueError):
    def __init__(self, explanation, status=None):
        self.explanation = explanation
        self.status = status
        super().__init__(explanation)


class WorkdayPostingError(BoardReadError):
    """A detail error that does not stop the rest of the board."""


class WorkdayReadLimit(BoardReadError):
    """A local time or posting limit that can be planned around on resume."""


class InvalidBoardResponse(BoardReadError):
    """A response with an unreadable or oversized body."""


def board_failure(error):
    if isinstance(error, BoardReadError):
        return error
    if isinstance(error, HTTPError):
        status = error.code
        error.close()
        return BoardReadError({429: 'asked us to slow down', 503: 'was temporarily unavailable'}.get(
            status, 'returned an HTTP error'), status)
    if isinstance(error, UnicodeError):
        return BoardReadError('returned unreadable text', 200)
    if isinstance(error, TimeoutError) or isinstance(error, URLError) and isinstance(error.reason, TimeoutError):
        return BoardReadError('timed out')
    if isinstance(error, (OSError, HTTPException)):
        return BoardReadError('could not be reached')
    if isinstance(error, ValueError):
        return BoardReadError('returned an invalid or unsupported response')
    return BoardReadError('could not be read because of an unexpected error')


def read_reason(provider, explanation, status, page, total_pages, count):
    pages = f' of {total_pages}' if total_pages is not None else ''
    http = f'HTTP {status}' if status is not None else 'HTTP status unavailable'
    return f'{provider} {explanation} after page {page}{pages}. {http}. {count} rows read.'


def retry_after(value):
    if value is None:
        return None
    value = value.strip()
    if re.fullmatch(r'[0-9]+', value):
        return float(value)
    try:
        stamp = parsedate_to_datetime(value)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0, stamp.timestamp() - time.time())
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def workday_date(value, today):
    posted = clean_text(value, 100).casefold()
    days = re.fullmatch(r'posted (\d+) days? ago', posted)
    count = int(days[1]) if days else {'posted today': 0, 'posted yesterday': 1}.get(posted)
    if count is None or not 0 <= count < min(36501, today.toordinal()):
        return ''
    return (today - timedelta(days=count)).isoformat()


def country_facet(facets, countries):
    """Return the advertised country filter and the profile countries it covers."""
    def groups(value, nested=False):
        if isinstance(value, dict):
            name, choices = value.get('facetParameter'), value.get('values')
            if isinstance(name, str) and isinstance(choices, list):
                country = 'country' in name.casefold()
                if country or nested and 'location' in name.casefold():
                    yield name, choices, country
            for child in value.values():
                yield from groups(child, nested or isinstance(name, str))
        elif isinstance(value, list):
            for child in value:
                yield from groups(child, nested)

    matches = []
    for name, choices, country in groups(facets):
        ids, covered = {}, set()
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            token, label = choice.get('id'), choice.get('descriptor')
            if isinstance(token, str) and token and isinstance(label, str):
                label = label.strip().casefold()
                if label in countries:
                    ids[token] = None
                    covered.add(label)
        if country or ids:
            matches.append((name, list(ids), country, covered))
    if not matches:
        return None, set()
    name, ids, _, covered = max(matches, key=lambda match: (match[2], len(match[3]), len(match[1])))
    return ({name: ids} if ids else {}), covered


class WorkdayQueue:
    """Allow another host to run when a Workday read is asked to wait."""
    def __init__(self, run_deadline):
        self.run_deadline = run_deadline
        self.host_ready = {}
        self.shared_ready = 0
        self.succeeded()

    def succeeded(self):
        self.cooldown = 30
        self.last_limited = ''

    def limited(self, host, seconds):
        now = time.monotonic()
        self.last_limited = host
        if seconds is None:
            self.shared_ready = now + self.cooldown
            self.cooldown = min(self.cooldown * 2, 240)
        else:
            self.host_ready[host] = now + seconds

    def read(self, tasks):
        pending = [(key, host.lower(), start, None, self.run_deadline) for key, host, start in tasks]
        worldwide = []
        while pending or worldwide:
            if not pending:
                pending, worldwide = worldwide, []
            now = time.monotonic()

            def ready_at(host):
                return max(now, self.shared_ready, self.host_ready.get(host, 0))

            def priority(task):
                _, host, _, _, deadline = task
                ready = ready_at(host)
                # Finish reads that run out of time and give another ready host the next turn.
                return min(ready, max(now, deadline)), host == self.last_limited

            index = min(range(len(pending)), key=lambda i: priority(pending[i]))
            key, host, start, reader, deadline = pending.pop(index)
            ready = ready_at(host)
            allowed = ready < deadline
            if allowed and ready > now:
                time.sleep(ready - now)
            now = time.monotonic()
            acquired = allowed and now < deadline and _WORKDAY_SLOT.acquire(timeout=deadline - now)
            try:
                if reader is None:
                    reader = start(self.run_deadline if acquired else now)
                    deadline, defer = next(reader)
                else:
                    deadline, defer = reader.send(acquired)
            except StopIteration as result:
                finished = result.value
            except Exception as error:
                finished = error
            else:
                (worldwide if defer else pending).append((key, host, start, reader, deadline))
                continue
            finally:
                if acquired:
                    _WORKDAY_SLOT.release()
            yield key, finished


def board_rows(source, fetch=fetch_json, detail_match=None, today=None, home_first=None, *,
               start_offset=0, page_pause=None, board_budget=None, run_deadline=None, known_links=None,
               detail_first=False, failed_details=None, countries=None, saved_facets=None, queue=None):
    """Shared board adapters; detail_match gates the bounded Workday detail reads."""
    url = source_url(source)
    provider = source['provider']
    if provider != 'workday':
        if provider == 'greenhouse':
            url += '?content=true'
        data = fetch('GET', url)
        rows = data.get('jobs') if provider in ('greenhouse', 'ashby') and isinstance(data, dict) else data
        if not isinstance(rows, list) or len(rows) > MAX_POSTINGS:
            raise BoardReadError('returned an invalid or oversized posting list', 200)
        return rows
    if page_pause is None or board_budget is None:
        defaults = load_component('search_profile').WORKDAY_DEFAULTS
        if page_pause is None:
            page_pause = defaults['workday_page_pause_seconds']
        if board_budget is None:
            board_budget = defaults['workday_board_budget_seconds']
    reader = workday_rows(source, url, fetch, detail_match, today, home_first,
                          start_offset, page_pause, board_budget, run_deadline, known_links,
                          detail_first, failed_details, countries, saved_facets, queue)
    if queue is not None:
        return reader
    with _WORKDAY_SLOT:
        try:
            next(reader)
        except StopIteration as result:
            return result.value


def workday_rows(source, url, fetch, detail_match, today, home_first,
                 start_offset, page_pause, board_budget, run_deadline, known_links,
                 detail_first, failed_details, countries, saved_facets, queue):
    # Legacy entries only contain IDs. Discover again unless the cache proves coverage.
    if not (countries and saved_facets and saved_facets.get('countries') == sorted(countries)
            and saved_facets.get('filter')):
        saved_facets = None
    if saved_facets:
        start_offset, detail_first = 0, False
    rows, offset, total = BoardRows(), start_offset, None
    known_links = known_links or {}
    today = today or date.today()
    cutoff = (today - timedelta(days=SEARCH_WINDOW_DAYS)).isoformat()
    stop_before = (today - timedelta(days=SEARCH_WINDOW_DAYS + WORKDAY_ORDER_MARGIN_DAYS)).isoformat()
    started = time.monotonic()
    deadline = started + board_budget
    budget = 'board'
    if run_deadline is not None and run_deadline <= deadline:
        deadline, budget = run_deadline, 'run'
    rows.attempted = False
    rows.pages_read = rows.rows_read = rows.skipped_rows = 0
    rows.skipped_details = 0
    rows.country_scoped = bool(saved_facets)
    rows.saved_facets = saved_facets
    rows.start_offset = start_offset
    rows.started_with_details = detail_first
    rows.failed_details = {link: day for link, day in (failed_details or {}).items() if day >= cutoff}
    rows.page = start_offset // WORKDAY_PAGE_SIZE
    problem = None
    date_limited = False
    newest_first = source.get('newest_first', True)
    probed_details = {}
    matches = []
    detail_problem = None
    detail_slots = details_read = 0
    applied = saved_facets['filter'] if saved_facets else {}
    discover_country = bool(countries and not saved_facets)
    rediscovery_error = None
    list_finished = False
    no_country_jobs = False

    def budget_error(status=None):
        return WorkdayReadLimit(f'reached the {budget} time budget', status)

    def pause(seconds, status=None):
        if time.monotonic() + seconds >= deadline:
            raise budget_error(status)
        if seconds:
            time.sleep(seconds)

    def defer_worldwide():
        nonlocal budget, deadline
        deferred_at = time.monotonic()
        if not (yield queue.run_deadline, True):
            deadline, budget = time.monotonic(), 'run'
            return False
        if budget == 'board':
            deadline += time.monotonic() - deferred_at
            if queue.run_deadline <= deadline:
                deadline, budget = queue.run_deadline, 'run'
        return True

    def request(method, target, body=None, *, posting_detail=False):
        for delay in (5, 15, 45, None):
            pause(0)
            rows.attempted = True
            try:
                if fetch is fetch_json:
                    data = fetch(method, target, body, deadline=deadline)
                else:
                    data = fetch(method, target, body)
            except HTTPError as error:
                status = error.code
                seconds = retry_after(error.headers.get('Retry-After')) if error.headers is not None else None
                failure = board_failure(error)
                if posting_detail and 300 <= status < 500 and status not in (408, 429):
                    raise WorkdayPostingError(failure.explanation, status) from None
                if status == 429 and queue is not None:
                    queue.limited(source['host'].lower(), seconds)
                    if delay is None:
                        raise failure from None
                    if not (yield deadline, False):
                        raise budget_error(status)
                    pause(0, status)
                    continue
                if status not in (429, 503) or delay is None:
                    raise failure from None
                pause(delay if seconds is None else seconds, status)
            except (OSError, HTTPException, ValueError) as error:
                if time.monotonic() >= deadline:
                    raise budget_error(getattr(error, 'status', None)) from None
                if posting_detail and (isinstance(error, InvalidBoardResponse)
                                       or isinstance(error, BoardReadError) and 300 <= (error.status or 0) < 400):
                    raise WorkdayPostingError(error.explanation, error.status) from None
                raise board_failure(error) from None
            else:
                if queue is not None:
                    queue.succeeded()
                if time.monotonic() >= deadline:
                    raise budget_error(200)
                return data

    def read_detail(raw):
        pause(page_pause)
        detail = yield from request('GET', url.rsplit('/', 1)[0] + raw['externalPath'], posting_detail=True)
        info = detail.get('jobPostingInfo') if isinstance(detail, dict) else None
        if not isinstance(info, dict):
            raise WorkdayPostingError('returned invalid job details', 200)
        return info

    def collect_match(raw):
        if (not detail_match or not raw['title']
                or newest_first and raw['_posted'] and raw['_posted'] < cutoff
                or not detail_match(raw['title'], raw['description'])):
            return
        # Relative dates change over time without changing the listing itself.
        fingerprint = hashlib.sha256(json.dumps([raw['title'], raw['description']]).encode()).hexdigest()
        raw['_list_fingerprint'] = fingerprint
        if known_links.get(raw['_link']) == fingerprint:
            raw['_details_cached'] = True
        else:
            raw['_details_pending'] = True
            matches.append(raw)

    def priority(raw):
        place = raw.get('locationsText', '')
        if home_first and home_first(place):
            return 0
        return 1 if isinstance(place, str) and re.fullmatch(r'\d+ Locations?', place, re.IGNORECASE) else 2

    def record_failure(raw):
        raw.pop('_details_pending', None)
        raw.pop('_details_cached', None)
        raw['_details_failed'] = True
        rows.failed_details[raw['_link']] = today.isoformat()
        rows.skipped_details += 1

    def apply_detail(raw, info):
        nonlocal details_read
        raw.pop('_details_pending', None)
        raw.pop('_details_cached', None)
        raw['_details_read'] = True
        rows.failed_details.pop(raw['_link'], None)
        details_read += 1
        raw['description'] += ' ' + str(info.get('jobDescription') or '')
        raw['posting_id'] = info.get('jobReqId')
        exact = iso_day(info.get('startDate'))
        if exact:
            raw['_posted'], raw['_date_source'] = exact, 'workday'
        places = [info.get('location')]
        additional = info.get('additionalLocations')
        if isinstance(additional, list):
            places.extend(additional)
        places = dict.fromkeys(clean_text(place, 300) for place in places if isinstance(place, str))
        place = clean_text(' · '.join(part for part in places if part), 300)
        if not place:
            place = raw.get('locationsText', '')
        remote = info.get('remoteType')
        if isinstance(remote, str) and 'remote' in remote.casefold() and 'remote' not in str(place).casefold():
            place = clean_text(place, 291) + ' · Remote'
        raw['locationsText'] = place

    def enrich(pending):
        nonlocal detail_slots, detail_problem
        ordered = sorted(pending, key=lambda raw: (rows.failed_details.get(raw['_link'], ''),
                                                   raw['_link'] in known_links, priority(raw)))
        for raw in ordered[:WORKDAY_DETAIL_LIMIT - detail_slots]:
            link = raw['_link']
            detail_slots += 1
            try:
                if link not in probed_details:
                    probed_details[link] = yield from read_detail(raw)
            except WorkdayPostingError:
                record_failure(raw)
                continue
            except BoardReadError as error:
                detail_problem = error
                break
            apply_detail(raw, probed_details[link])

    while True:
        defer_fallback = False
        try:
            if rows.rows_read >= MAX_POSTINGS:
                raise WorkdayReadLimit('reached the posting limit for this read', 200)
            if rows.pages_read and rediscovery_error is None:
                pause(page_pause)
            try:
                data = yield from request('POST', url, {'appliedFacets': applied, 'limit': WORKDAY_PAGE_SIZE,
                                                        'offset': 0 if discover_country else offset, 'searchText': ''})
            except BoardReadError as error:
                if error.status != 400 or not applied:
                    raise
                rows.saved_facets = None
                if not saved_facets or not countries:
                    raise
                rediscovery_error = error
                saved_facets = None
                applied, discover_country = {}, True
                offset = rows.start_offset = 0
                total = None
                detail_first = rows.started_with_details = False
                pause(page_pause)
                continue
            if not isinstance(data, dict) or not isinstance(data.get('jobPostings'), list):
                raise BoardReadError('returned an invalid posting list', 200)
            reported, page = data.get('total'), data['jobPostings']
            if type(reported) is not int or reported < 0 or len(page) > WORKDAY_PAGE_SIZE:
                raise BoardReadError('returned an invalid posting count', 200)
            if discover_country:
                discover_country = False
                selected, covered = country_facet(data.get('facets'), countries)
                if not selected and rediscovery_error and rows.rows_read:
                    raise rediscovery_error
                rows.country_scoped = selected is not None
                if selected is not None:
                    offset = rows.start_offset = 0
                    detail_first = rows.started_with_details = False
                    if not rows.rows_read:
                        rows.page = 0
                    if not selected:
                        no_country_jobs = True
                        break
                    applied = selected
                    if covered == countries.keys():
                        rows.saved_facets = {'filter': selected, 'countries': sorted(covered)}
                    pause(page_pause)
                    continue
                if offset:
                    # The first page was a facet probe. Resume the existing worldwide read.
                    if queue is not None and not (yield from defer_worldwide()):
                        raise budget_error()
                    continue
                defer_fallback = queue is not None
            rediscovery_error = None
            if not reported and rows.start_offset:
                total = None
            elif reported or not offset:
                total = reported if total is None else max(total, reported)
            if rows.rows_read + len(page) > MAX_POSTINGS:
                raise WorkdayReadLimit('reached the posting limit for this read', 200)
            if not page and total is not None and offset < total:
                raise BoardReadError('returned an empty page before the end', 200)
        except BoardReadError as error:
            problem = error
            break
        rows.pages_read += 1
        rows.page = offset // WORKDAY_PAGE_SIZE + 1
        rows.rows_read += len(page)
        page_start = len(rows)
        for listing in page:
            if not isinstance(listing, dict):
                rows.skipped_rows += 1
                continue
            raw = dict(listing)
            raw['title'] = clean_text(raw.get('title'), 200)
            path = raw.get('externalPath', '')
            if not isinstance(path, str) or not re.fullmatch(r'/job/[A-Za-z0-9_/%.-]+', path) or '..' in path or '%' in path:
                rows.skipped_rows += 1
                continue
            raw['_link'] = safe_link(f"https://{source['host']}/{source['site']}" + path)
            if not raw['_link']:
                rows.skipped_rows += 1
                continue
            raw['_page_offset'] = offset
            raw['_posted'] = posted = workday_date(raw.get('postedOn'), today)
            raw['_date_source'] = 'workday-about' if posted else ''
            listing_text = clean_text(' '.join(
                ' '.join(str(part) for part in value) if isinstance(value, list) else str(value)
                for key, value in listing.items() if key not in ('externalPath', 'postedOn')
            ), 20000)
            raw['description'] = listing_text
            rows.append(raw)
            collect_match(raw)
        offset += len(page)
        list_finished = len(page) < WORKDAY_PAGE_SIZE if total is None else offset >= total
        if defer_fallback and (not list_finished or matches):
            # Put off further requests after recording the page we already have.
            if not (yield from defer_worldwide()):
                if not list_finished:
                    problem = budget_error()
                break
        if detail_first and rows.pages_read == 1:
            # Enrich the replayed page before paging consumes the day's budget.
            # Previously failed postings wait for untried matches on later pages.
            yield from enrich([raw for raw in matches if raw['_link'] not in rows.failed_details])
            if detail_problem:
                if not list_finished:
                    problem = detail_problem
                break
        if list_finished:
            break
        # A skipped row leaves the page's newest date uncertain.
        if total is not None and newest_first and len(rows) - page_start == len(page):
            page_rows = rows[page_start:]
            dates = [raw['_posted'] for raw in page_rows]
            newest = max(dates) if all(dates) else ''
            if all(clean_text(raw.get('postedOn'), 100).casefold() == 'posted 30+ days ago' for raw in page_rows):
                last = page_rows[-1]
                try:
                    if last.get('_details_failed'):
                        continue
                    info = probed_details.get(last['_link'])
                    if info is None:
                        info = yield from read_detail(last)
                except WorkdayPostingError:
                    record_failure(last)
                    continue
                except BoardReadError as error:
                    problem = error
                    break
                probed_details[last['_link']] = info
                newest = iso_day(info.get('startDate'))
            # Small reversals across a page boundary can still hide an in-window row.
            if newest and newest < stop_before:
                date_limited = True
                break

    if not detail_problem:
        pending_matches = [raw for raw in matches if raw.get('_details_pending')]
        if problem and not (isinstance(problem, WorkdayReadLimit) and problem.status in (None, 200)):
            # A board-wide error stops further requests. Already-read probes are usable.
            pending_matches = [raw for raw in pending_matches if raw['_link'] in probed_details]
        yield from enrich(pending_matches)
    # A cutoff probe may already have a newer answer for a cached or deferred match.
    for raw in rows:
        if ((raw.get('_details_pending') or raw.get('_details_cached'))
                and raw['_link'] in probed_details):
            if raw.get('_details_cached'):
                matches.append(raw)
            apply_detail(raw, probed_details[raw['_link']])
    pending_offsets = [raw['_page_offset'] for raw in matches if raw.get('_details_pending')]
    pending = len(pending_offsets)
    limited = isinstance(problem or detail_problem, WorkdayReadLimit)
    rows.full_read = not (problem or rows.start_offset or date_limited or rows.country_scoped)
    rows.next_offset = min(pending_offsets, default=offset) if problem else 0
    rows.detail_first = detail_first and not rows.pages_read
    if limited:
        if detail_first and rows.rows_read:
            # Alternate a detail-first replay with forward paging so a tight budget
            # can neither pin the cursor nor spend every morning on list requests.
            rows.next_offset = 0 if date_limited or list_finished else offset
        elif pending:
            rows.next_offset = min(pending_offsets)
            rows.detail_first = True
    rows.elapsed_seconds = round(time.monotonic() - started, 3)
    failure = problem or detail_problem
    rows.http_status = failure.status if failure else 200
    total_pages = None if total is None else (total + WORKDAY_PAGE_SIZE - 1) // WORKDAY_PAGE_SIZE
    reasons = []
    if problem:
        reasons.append(read_reason('Workday', problem.explanation, problem.status, rows.page, total_pages, rows.rows_read))
    elif date_limited:
        reasons.append(f'Workday: finished postings from the last {SEARCH_WINDOW_DAYS} days after page {rows.page} of {total_pages}.')
    elif no_country_jobs:
        reasons.append(f'No postings listed for {next(iter(countries.values()))}.')
    elif rows.start_offset and not pending:
        reasons.append(read_reason('Workday', 'finished a resumed read', 200, rows.page, total_pages, rows.rows_read))
    if pending and problem:
        resume = 'continue paging on the next read' if limited and detail_first else 'resume at the earliest pending page'
        reasons.append(f'{pending} job details deferred; {resume}.')
    elif pending:
        explanation = 'reached the detail limit'
        if detail_problem:
            explanation = {429: 'asked us to slow down', 503: 'was temporarily unavailable'}.get(
                detail_problem.status, detail_problem.explanation)
        continues = 'continues' if pending == 1 else 'continue'
        reasons.append(f'Workday finished the list (page {rows.page} of {total_pages}); job details stopped '
                       f'after {details_read} of {len(matches)} ({explanation}). {pending} {continues} tomorrow.')
    if rows.skipped_rows:
        reasons.append(f'{rows.skipped_rows} rows skipped (no usable posting path)')
    if rows.skipped_details:
        reasons.append(f'{rows.skipped_details} posting detail requests skipped (posting-specific error).')
    rows.reason = ' '.join(reasons)
    rows.read = ('partial' if rows.rows_read else 'failed') if problem else 'complete'
    return rows


def fetch_source(source, detail_match=None, **options):
    rows = board_rows(source, detail_match=detail_match, **options)
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


def remote_location(location):
    return bool(re.search(r'\b(remote|anywhere|distributed)\b', location, re.I))


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
        fields = raw.get('bulletFields')
        posting_id = fields[0] if isinstance(fields, list) and fields else None
    description = raw.get('content') if source['provider'] == 'greenhouse' else raw.get('descriptionPlain') if source['provider'] in ('lever', 'ashby') else raw.get('description')
    return {'id': key, 'company': source['label'], 'role': role, 'location': location or 'Not specified',
            'provider': source['provider'], 'posting_id': str(posting_id) if posting_id is not None else link,
            'remote': remote_location(location),
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
    workday_options = {}
    if fetcher is fetch_source:
        profiles = load_component('search_profile')
        path = profiles.default_path()
        if path.exists():
            profile = profiles.load(path)
        policy = profile['search'] if profile else profiles.WORKDAY_DEFAULTS
        workday_options = dict(page_pause=policy['workday_page_pause_seconds'],
                               board_budget=policy['workday_board_budget_seconds'],
                               run_deadline=time.monotonic() + policy['workday_run_budget_seconds'])

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
                options = workday_options if source['provider'] == 'workday' else {}
                return fetch_source(source, detail_match, **options)
            return fetcher(source)
        ordered_sources = sorted(enumerate(sources), key=lambda item: item[1]['provider'] == 'workday')
        pending = {pool.submit(read, source): index for index, source in ordered_sources}
        for future in as_completed(pending):
            index = pending[future]
            source = sources[index]
            try:
                data = future.result()
                rows = data.get('jobs') if source['provider'] != 'lever' and isinstance(data, dict) else data
                if not isinstance(rows, list) or len(rows) > MAX_POSTINGS:
                    raise ValueError('Unexpected or oversized job-board response.')
                if isinstance(rows, BoardRows) and rows.read != 'complete':
                    errors.append({'source': source['label'], 'message': rows.reason})
                found = []
                for raw in rows:
                    if isinstance(raw, dict) and raw.get('_details_failed'):
                        continue
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
