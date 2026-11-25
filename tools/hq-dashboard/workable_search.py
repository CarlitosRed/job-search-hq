"""Read the bounded Workable search and posting endpoints for daily review."""
from datetime import date, datetime
from html.parser import HTMLParser
import json
import re
import time
from urllib.error import HTTPError
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request


SOURCE = 'workable_search'
ORIGIN = 'https://jobs.workable.com'
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
CONTROL = re.compile(r'[\x00-\x1f\x7f-\x9f]')
LIST_LIMIT = 25
DETAIL_LIMIT = 40
PAUSE = 1.5
RUN_SECONDS = 180


class ViewRedirect(HTTPRedirectHandler):
    def __init__(self, boards):
        self.boards = boards
        self.followed = False

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        location = headers.get('Location', headers.get('URI', ''))
        if not self.followed and not CONTROL.search(location) and self.boards.safe_link(newurl):
            endpoint = urlsplit(newurl)
            match = re.fullmatch(r'/view/[A-Za-z0-9_-]+/([^/]+)', endpoint.path)
            if endpoint.netloc == 'jobs.workable.com' and not endpoint.fragment and match:
                slug = unquote(match[1])
                # urllib resolves relative Locations before calling this handler.
                original_path = unquote(urlsplit(location).path)
                has_dot_segment = any(part in ('.', '..') for part in original_path.split('/'))
                if (not has_dot_segment and slug not in ('.', '..') and '/' not in slug
                        and '\\' not in slug and not CONTROL.search(slug)):
                    self.followed = True
                    return super().redirect_request(req, fp, code, msg, headers, newurl)
        fp.close()
        raise self.boards.BoardReadError('redirected outside its allowed view page', code)


def fetch_text(method, url, *, deadline, boards):
    endpoint = urlsplit(url)
    view = not endpoint.query and re.fullmatch(rf'/view/{UUID}', endpoint.path)
    if (method != 'GET' or endpoint.netloc != 'jobs.workable.com' or endpoint.scheme != 'https'
            or endpoint.fragment or not boards.safe_link(url)
            or not (endpoint.path == '/jobs.md' or view)):
        raise ValueError('Use a public Workable listing or posting endpoint.')
    request = Request(url, headers={'Accept': 'text/html' if view else 'text/markdown',
                                    'User-Agent': 'JobSearchHQ/1.0 (public job-board reader)'})
    data = boards.fetch_bounded(request, deadline, boards.MAX_BYTES,
                                redirect_handler=ViewRedirect(boards) if view else None, expected_status=200)
    return data.decode('utf-8', errors='strict')


def listing_total(markdown):
    found = re.findall(
        r'^Found \*\*([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\*\* matching job(?:s|\(s\))?'
        r'(?: \(showing first 30\))?\.[ \t]*\r?$', markdown, re.M)
    if len(found) != 1:
        raise ValueError('Unreadable Workable total.')
    return int(found[0].replace(',', ''))


def listing_rows(markdown, total, boards):
    rows = []
    header = '| Title | Company | Location | Workplace | Type | Link |'
    lines = markdown.splitlines()
    if header in lines:
        # A literal pipe in Markdown cells must be escaped.
        for line in lines[lines.index(header) + 2:]:
            if not line.startswith('|'):
                break
            cells = re.split(r'(?<!\\)\|', line.strip())[1:-1]
            if len(cells) != 6:
                raise ValueError('Unreadable Workable listing row.')
            title, company, location, workplace, _, link = (cell.strip().replace(r'\|', '|') for cell in cells)
            match = re.fullmatch(rf'\[View\]\({re.escape(ORIGIN)}/jobs/({UUID})\.md\)', link)
            if not match or not title or not company:
                raise ValueError('Unreadable Workable listing row.')
            token = match[1]
            rows.append(dict(provider=SOURCE, posting_id=token, title=boards.clean_text(title, 200),
                             company=boards.clean_text(company, 200), place_text=boards.clean_text(location, 300),
                             remote=workplace.casefold() == 'remote' or boards.remote_location(location),
                             link=f'{ORIGIN}/view/{token}', description='', posted_on='', date_source='', open=None))
    if len(rows) > min(total, 30) or total and not rows:
        raise ValueError('Workable listing is missing its expected rows.')
    return rows


def strict_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('Duplicate JSON field.')
    return result


def reject_constant(value):
    raise ValueError('Invalid JSON constant.')


class PostingScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.kind = None
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.kind = dict(attrs).get('type')

    def handle_endtag(self, tag):
        if tag == 'script':
            self.kind = None

    def handle_data(self, data):
        if self.kind == 'application/ld+json':
            try:
                payload = json.loads(data, object_pairs_hook=strict_object, parse_constant=reject_constant)
            except (ValueError, RecursionError):
                return
            self.scripts.append(payload)


def posting_stamp(value):
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return date.fromisoformat(value)
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:[0-5]\d)?', value):
            return datetime.fromisoformat(value)
    except ValueError:
        pass
    return None


def records(value):
    return (item for item in (value if isinstance(value, list) else [value]) if isinstance(item, dict))


def posting_data(markup, job, today, boards):
    parser = PostingScripts()
    parser.feed(markup)
    parser.close()
    structured, pending = [], list(parser.scripts)
    while pending:
        item = pending.pop()
        for record in records(item):
            types = record.get('@type')
            if types == 'JobPosting' or isinstance(types, list) and 'JobPosting' in types:
                structured.append(record)
            pending.extend(record.values())
    if len(structured) != 1:
        raise boards.InvalidBoardResponse('returned no unique JobPosting', 200)
    data = structured[0]
    identifiers = [entry.get('value') for entry in records(data.get('identifier'))]
    if 'id' in data:
        identifiers.append(data['id'])
    if not identifiers or any(value != job['posting_id'] for value in identifiers):
        raise boards.InvalidBoardResponse('returned a JobPosting for a different ID', 200)

    raw_expiry = data.get('validThrough')
    expiry = posting_stamp(raw_expiry)
    if raw_expiry is not None and expiry is None:
        raise boards.InvalidBoardResponse('returned an unreadable posting expiry', 200)
    if isinstance(expiry, datetime):
        expired = expiry < datetime.now(expiry.tzinfo) if today == date.today() else expiry.date() < today
    else:
        expired = expiry is not None and expiry < today
    posted = posting_stamp(data.get('datePosted'))
    if isinstance(posted, datetime):
        posted = posted.date()
    day = posted.isoformat() if posted and posted <= today else ''

    def clean(value, limit):
        return boards.clean_text(value, limit) if isinstance(value, str) else ''

    organization = next(records(data.get('hiringOrganization')), {})
    location_type = data.get('jobLocationType')
    remote = location_type == 'TELECOMMUTE' or isinstance(location_type, list) and 'TELECOMMUTE' in location_type
    countries, locations = [], []
    if remote:
        for country in records(data.get('applicantLocationRequirements')):
            name = clean(country.get('name'), 200)
            if country.get('@type') == 'Country' and name:
                countries.append(name)
        if 'applicantLocationRequirements' in data:
            job['applicant_countries'] = countries
    else:
        for location in records(data.get('jobLocation')):
            for address in records(location.get('address')):
                country = address.get('addressCountry')
                if isinstance(country, dict):
                    country = country.get('name')
                fields = [clean(address.get('addressLocality'), 100),
                          clean(address.get('addressRegion'), 100), clean(country, 100)]
                if any(fields):
                    locations.append(', '.join(filter(None, fields)))
    sections = [clean(data.get(key), 20000) for key in ('description', 'requirementsSection', 'benefitsSection')]
    job.update(open=not expired, posted_on=day, date_source=SOURCE if day else '',
               title=clean(data.get('title'), 200) or job['title'],
               company=clean(organization.get('name'), 200) or job['company'],
               remote=remote, place_text=(', '.join(countries) or 'Remote')[:300] if remote
               else ' · '.join(locations)[:300], description='\n'.join(filter(None, sections))[:20000])


def read(profile, queries, today, boards, fetch, skip_detail):
    """Return unique findings, per-query totals and one board-read record."""
    deadline = time.monotonic() + RUN_SECONDS
    next_request = 0
    remaining = {'list': LIST_LIMIT, 'detail': DETAIL_LIMIT}
    rows, runs = {}, []
    reading = dict(provider=SOURCE, board=f"Workable ({profile['id']})", read='failed', reason='',
                   rows=0, checked=0, seen=[], search_only=True)

    def request(url, kind):
        nonlocal next_request
        for attempt in range(2):
            now = time.monotonic()
            if remaining[kind] <= 0 or max(now, next_request) >= deadline:
                raise boards.BoardReadError('reached its request or time limit')
            if now < next_request:
                time.sleep(next_request - now)
            remaining[kind] -= 1
            next_request = time.monotonic() + PAUSE
            if kind == 'detail':
                reading['checked'] += 1
            try:
                return fetch('GET', url, deadline=deadline)
            except HTTPError as error:
                if kind != 'list' or error.code not in (429, 503) or attempt or remaining[kind] <= 0:
                    raise
                delay = boards.retry_after(error.headers.get('Retry-After')) if error.headers else None
                error.close()
                next_request = max(next_request, time.monotonic() + (PAUSE if delay is None else delay))

    def problem(error):
        failure = boards.board_failure(error)
        reading['http_status'] = failure.status
        if failure.status == 403:
            return 'Workable refused access (HTTP 403).'
        return f'Workable {failure.explanation}.'

    if not queries:
        reading['reason'] = 'Workable had no search phrases to read.'
        return [], runs, reading

    recent = dict(day_range=14)
    scope = dict(recent, location=profile['work_rules']['home_country'].strip())
    guard_reason = 'Workable location filter could not be confirmed.'
    try:
        worldwide = listing_total(request(f'{ORIGIN}/jobs.md?{urlencode(recent)}', 'list'))
        scoped = listing_total(request(f'{ORIGIN}/jobs.md?{urlencode(scope)}', 'list'))
    except (OSError, ValueError, boards.HTTPException) as error:
        reading['reason'] = f'{guard_reason} {problem(error)}'
        return [], runs, reading
    if not worldwide or scoped * 2 > worldwide:
        reading['reason'] = guard_reason
        return [], runs, reading

    for query in queries:
        if remaining['list'] <= 0:
            reading['reason'] = 'Workable reached its listing request limit.'
            break
        run = dict(query=query, total=None, reason='', posting_ids=[])
        runs.append(run)
        parameters = dict(scope, query=query)
        try:
            markdown = request(f'{ORIGIN}/jobs.md?{urlencode(parameters)}', 'list')
            total = listing_total(markdown)
            run['total'] = total
            found = listing_rows(markdown, total, boards)
            run['posting_ids'] = list(dict.fromkeys(job['posting_id'] for job in found))
            for job in found:
                rows.setdefault(job['posting_id'], job)
        except (OSError, ValueError, boards.HTTPException) as error:
            run['reason'] = problem(error)
            if reading['http_status'] == 403:
                reading['reason'] = run['reason']
                break
    queries_read = any(not run['reason'] for run in runs)
    for job in rows.values():
        reason = skip_detail(job)
        if reason:
            job['fact_reason'] = reason
            continue
        if reading.get('http_status') == 403 or remaining['detail'] <= 0 or time.monotonic() >= deadline:
            job['read_reason'] = 'Workable stopped before the posting could be checked.'
            reading['reason'] = reading['reason'] or job['read_reason']
            continue
        try:
            posting_data(request(job['link'], 'detail'), job, today, boards)
        except HTTPError as error:
            if error.code in (404, 410):
                job['open'] = False
                error.close()
            else:
                job['read_reason'] = reading['reason'] = problem(error)
        except (OSError, ValueError, boards.HTTPException) as error:
            job['read_reason'] = reading['reason'] = problem(error)
    for run in runs:
        if not run['reason']:
            run['reason'] = next((rows[token]['read_reason'] for token in run['posting_ids']
                                  if rows[token].get('read_reason')), '')
    reading['rows'] = len(rows)
    reading['reason'] = reading['reason'] or next((run['reason'] for run in runs if run['reason']), '')
    if queries_read:
        reading['read'] = 'partial' if reading['reason'] else 'complete'
    return list(rows.values()), runs, reading
