"""Read discovery feeds and confirm leads on bounded public employer endpoints."""
from collections import deque
from datetime import timedelta
from functools import partial
from itertools import product
import json
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request


LABELS = {'himalayas': 'Himalayas', 'jobicy': 'Jobicy'}
HIMALAYAS = 'https://himalayas.app/jobs/api/search'
JOBICY = 'https://jobicy.com/api/v2/remote-jobs'
JOBICY_LOCATIONS = 'https://jobicy.com/api/jobs?get=locations'
NOT_CONFIRMED = "Not confirmed on the employer's site."
NO_SEARCH_MATCH = 'Does not match your search.'
PLACE_UNCONFIRMED = 'The place where you can work could not be confirmed.'
NO_BOARD = 'none'


class LimitReached(Exception):
    pass


class Budget:
    def __init__(self, count, pause):
        self.remaining = count
        self.pause = pause
        self.deadline = time.monotonic() + 180
        self.ready = {}

    def reserve(self, url):
        host = urlsplit(url).hostname
        now = time.monotonic()
        start = max(now, self.ready.get(host, now))
        if not self.remaining or start >= self.deadline:
            raise LimitReached
        if start > now:
            time.sleep(start - now)
        self.remaining -= 1
        self.ready[host] = time.monotonic() + self.pause

    def defer(self, url, delay):
        host = urlsplit(url).hostname
        self.ready[host] = max(self.ready[host], time.monotonic() + delay)


def fetch_json(method, url, *, deadline, boards):
    """The feeds and documented Jobicy location list never follow redirects."""
    if method != 'GET' or not boards.safe_link(url) or not (
            url == JOBICY_LOCATIONS or url.partition('?')[0] in (HIMALAYAS, JOBICY)):
        raise ValueError('Use a public discovery API endpoint.')
    request = Request(url, headers={'Accept': 'application/json',
                                    'User-Agent': 'JobSearchHQ/1.0 (public job-board reader)'})
    return json.loads(boards.fetch_bounded(request, deadline, boards.MAX_BYTES, expected_status=200))


def reserve_jobicy(backlog_connect):
    # The hourly limit applies to the IP across profiles using this backlog.
    with backlog_connect() as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS jobicy_pass (id INTEGER PRIMARY KEY, next_pass REAL NOT NULL)')
        previous = db.execute('SELECT next_pass FROM jobicy_pass WHERE id=1').fetchone()
        started = time.time()
        if previous and started < previous[0]:
            return False
        db.execute('INSERT OR REPLACE INTO jobicy_pass VALUES (1,?)', (started + 3600,))
        return True


def region_key(text):
    return ' '.join(re.findall(r'\w+', text.casefold()))


def region_matches(name, label):
    """Accept a provider label or a compact abbreviation of the profile's words."""
    name, label = region_key(name), region_key(label)
    if name == label:
        return True
    words = [word for word in name.split() if word not in ('and', 'of', 'the')]
    if not (2 <= len(words) <= 4 and re.fullmatch(r'[a-z]{3,5}', label)):
        return False
    return any(''.join(parts) == label for parts in
               product(*(tuple(word[:size] for size in range(1, min(len(word), 3) + 1)) for word in words)))


def jobicy_geo(profile, payload, countries):
    locations = payload.get('locations') if isinstance(payload, dict) else None
    if not isinstance(locations, list):
        raise ValueError('Unreadable Jobicy locations.')
    supported = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        name, slug = location.get('geoName'), location.get('geoSlug')
        if isinstance(name, str) and isinstance(slug, str) and re.fullmatch(r'[a-z0-9-]{1,80}', slug):
            supported.append((name, slug))
    for name in [*profile['work_rules']['remote_regions'], *countries]:
        for label, slug in supported:
            if slug != 'anywhere' and (region_matches(name, label) or region_matches(name, slug)):
                return slug
    return ''


def lead_from_row(source, raw, boards):
    def field(name, limit=200):
        value = raw.get(name)
        return boards.clean_text(value, limit) if isinstance(value, str) else ''

    if not isinstance(raw, dict):
        raise ValueError('Unreadable discovery posting.')
    if source == 'himalayas':
        title, text = field('title'), field('description', 20000)
        countries = raw.get('locationRestrictions')
        if not isinstance(countries, list) or any(not isinstance(name, str) or not name.strip() for name in countries):
            raise ValueError('Unreadable discovery location.')
        location = ', '.join(countries) if countries else 'Anywhere'
        link = token = boards.safe_link(raw.get('guid'))
    else:
        title, text, location = field('jobTitle'), field('jobDescription', 20000), field('jobGeo', 300)
        link = boards.safe_link(raw.get('url'))
        token = raw.get('id')
        if type(token) is not int or token <= 0 or urlsplit(link).hostname != 'jobicy.com':
            raise ValueError('Unreadable Jobicy posting link or ID.')
    company = field('companyName')
    if not title or not link or not company:
        raise ValueError('Discovery posting is missing its title, company or link.')
    lead = dict(provider=source, posting_id=str(token), title=title, company=company, company_slug=field('companySlug'),
                place_text=location[:300], remote=True, link=link, description=text, posted_on='', date_source='',
                open=None, discovery_only=True, found_by=source, source_credit=LABELS[source], source_link=link)
    if source == 'himalayas' and countries:
        lead['applicant_countries'] = [name.strip() for name in countries]
    return lead


def read(source, profile, queries, countries, boards, fetch, backlog_connect, *, shadow=False, jobicy_pages=None):
    label = LABELS[source]
    reading = dict(provider=source, board=f"{label} ({profile['id']})", read='complete', reason='',
                   rows=0, kept=0, hidden={}, seen=[], search_only=True)
    is_jobicy = source == 'jobicy'
    # Both daily paths may use the same response in this run. Only the first
    # path may contact Jobicy, using the normal durable reservation and pacing.
    replay = is_jobicy and bool(jobicy_pages)
    if is_jobicy and not replay and not reserve_jobicy(backlog_connect):
        reading.update(read='skipped', reason='Jobicy was already read within the past hour.')
        return {}, {}, reading
    budget = Budget(3 if is_jobicy else 12, 2)
    found, runs = {}, {}
    read_page = False

    def request(url):
        if replay:
            if url not in jobicy_pages:
                raise LimitReached()
            return jobicy_pages[url]
        for attempt in range(2):
            budget.reserve(url)
            try:
                payload = fetch('GET', url, deadline=budget.deadline)
                if is_jobicy and jobicy_pages is not None:
                    jobicy_pages[url] = payload
                return payload
            except HTTPError as error:
                if error.code != 429:
                    raise
                delay = boards.retry_after(error.headers.get('Retry-After')) if error.headers else None
                budget.defer(url, 2 if delay is None else delay)
                if is_jobicy:
                    remaining = budget.ready['jobicy.com'] - time.monotonic()
                    with backlog_connect() as db, db:
                        db.execute('UPDATE jobicy_pass SET next_pass=max(next_pass,?) WHERE id=1',
                                   (time.time() + remaining,))
                if attempt or not budget.remaining or budget.ready[urlsplit(url).hostname] >= budget.deadline:
                    raise
                error.close()

    def problem(error, run=None):
        failure = boards.board_failure(error)
        status = f' (HTTP {failure.status})' if failure.status is not None else ''
        reason = f'{label} {failure.explanation}{status}.'
        reading.update(reason=reason, http_status=failure.status)
        if run is not None:
            run['reason'] = reason
        return failure.status

    if is_jobicy:
        try:
            geo = jobicy_geo(profile, request(JOBICY_LOCATIONS), countries)
        except (OSError, ValueError, boards.HTTPException, RecursionError, LimitReached) as error:
            status = problem(error)
            if status in (403, 429):
                reading['read'] = 'failed'
                return found, {'Locations': dict(total=None, reason=reading['reason'], posting_ids=[])}, reading
            geo = ''
        scope = dict(count=50, **({'geo': geo} if geo else {}))
        queue = deque([(geo or 'All locations', '', scope)])
    else:
        queue = deque((query, 1, dict(q=query, country=country))
                      for query in queries for country in countries)
    cursors = set()
    while queue and budget.remaining:
        query, page, scope = queue.popleft()
        run = runs.setdefault(query, dict(total=None, reason=reading['reason'] if is_jobicy else '', posting_ids=[]))
        params = dict(scope, page=page) if not is_jobicy else dict(scope, **({'cursor': page} if page else {}))
        url = (JOBICY if is_jobicy else HIMALAYAS) + '?' + urlencode(params)
        try:
            payload = request(url)
            rows = payload.get('jobs') if isinstance(payload, dict) and payload.get('success') is not False else None
            if not isinstance(rows, list) or len(rows) > (50 if is_jobicy else 20):
                raise ValueError('Unreadable discovery results.')
            if is_jobicy:
                cursor = payload.get('nextCursor')
                if payload.get('hasMore'):
                    if not isinstance(cursor, str) or not cursor or cursor in cursors:
                        raise ValueError('Unreadable Jobicy cursor.')
                    cursors.add(cursor)
                    queue.append((query, cursor, scope))
                total = len(rows)
            else:
                total, limit = payload.get('totalCount'), payload.get('limit')
                if type(total) is not int or total < 0 or type(limit) is not int or not 1 <= limit <= 20:
                    raise ValueError('Unreadable Himalayas page totals.')
                if rows:  # The provider's estimated total can lag the available pages.
                    queue.append((query, page + 1, scope))
            if is_jobicy or page == 1:
                run['total'] = (run['total'] or 0) + total
            for raw in rows:
                lead = lead_from_row(source, raw, boards)
                if shadow and is_jobicy:
                    terms = [phrase for phrase in queries if boards.phrase_matches(
                        phrase, lead['title'] + ' ' + lead['description'])]
                    if not terms:
                        continue
                elif not shadow and not boards.profile_posting_matches(profile, lead['title'], lead['description']):
                    if is_jobicy:
                        continue
                    lead['fact_reason'] = NO_SEARCH_MATCH
                token = lead['posting_id']
                found.setdefault(token, lead)
                if token not in run['posting_ids']:
                    run['posting_ids'].append(token)
                if shadow and is_jobicy:
                    for phrase in terms:
                        row = runs.setdefault(phrase, dict(total=0, reason='', posting_ids=[]))
                        if token not in row['posting_ids']:
                            row['posting_ids'].append(token)
                            row['total'] += 1
            read_page = True
        except LimitReached:
            break
        except (OSError, ValueError, boards.HTTPException, RecursionError) as error:
            if problem(error, run) in (403, 429):
                break
    reading['rows'] = len(found)
    if reading['reason']:
        reading['read'] = 'partial' if read_page else 'failed'
    return found, runs, reading


def place_input(profile, lead):
    """Translate equivalent region labels before applying the shared place rule."""
    parts = [part.strip() for part in lead['place_text'].split(',')]
    if any(region_key(part) == 'anywhere' for part in parts):
        return dict(lead, place_text='Anywhere', description='')
    rules = profile['work_rules']
    for name in [*rules['remote_regions'], rules['home_country'], *rules['authorized_countries']]:
        parts = [name if region_matches(name, part) or region_matches(part, name) else part for part in parts]
    return dict(lead, place_text=', '.join(parts), description='')


def slugs(leads):
    name = leads[0]['company']
    supplied = next((lead['company_slug'] for lead in leads if lead['company_slug']), '')
    tokens = []
    for field in (supplied, name):
        words = re.findall(r'[a-z0-9]+', field.casefold())
        for token in ('-'.join(words), ''.join(words)):
            if 0 < len(token) <= 80 and token not in tokens:
                tokens.append(token)
    return tokens


def named_company(raw):
    # The normalizer uses the requested board label as its company, so it
    # cannot serve as evidence that a guessed slug belongs to that company.
    for field in ('company', 'companyName', 'hiringOrganization'):
        value = raw.get(field)
        if isinstance(value, dict):
            value = value.get('name')
        if isinstance(value, str) and value.strip():
            return value
    return ''


def confirms_company(raw, description, company, boards, job_key):
    name = named_company(raw)
    return job_key(name, '')[0] == job_key(company, '')[0] if name else boards.phrase_matches(company, description)


def employer_job(result):
    return dict(provider=result['provider'], company=result['company'], posting_id=result['posting_id'],
                title=result['role'], place_text=result['location'], remote=result['remote'],
                posted_on=result['postedOn'], date_source=result['dateSource'],
                link=result['link'], description=result['description'])


def confirm(profile, today, groups, boards, workable, fetch, fetch_workable, backlog_connect, job_key):
    """Resolve employer copies and return transient misses by company."""
    if not groups:
        return {}, {}
    owner = profile['id']
    day = today.isoformat()
    cutoff = (today - timedelta(days=14)).isoformat()
    with backlog_connect() as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS discovery_boards (
            profile_id TEXT NOT NULL, company TEXT NOT NULL, checked_on TEXT NOT NULL, board TEXT NOT NULL,
            PRIMARY KEY(profile_id, company))''')
        remembered = {}
        for name, checked, stored in db.execute(
                'SELECT company,checked_on,board FROM discovery_boards WHERE profile_id=?', (owner,)):
            board = json.loads(stored)
            if board != NO_BOARD or checked > cutoff:
                remembered[name] = (board, checked, stored)
    budget = Budget(60, 1)
    matched, errors, updates, expired = {}, {}, [], []
    refused_hosts = {}
    configured = {(board['provider'], board['board']) for board in profile['search'].get('companies', [])}

    def request(read, method, url):
        host = urlsplit(url).hostname
        if host in refused_hosts:
            raise refused_hosts[host]
        budget.reserve(url)
        try:
            return read(method, url)
        except HTTPError as error:
            if error.code in (403, 429):
                failure = boards.board_failure(error)
                refused_hosts[host] = failure
                raise failure from None
            raise

    read_board = partial(request, partial(fetch, deadline=budget.deadline) if fetch is boards.fetch_json else fetch)
    text_fetch = (partial(workable.fetch_text, boards=boards, before_redirect=budget.reserve)
                  if fetch_workable is None else fetch_workable)
    read_text = partial(request, partial(text_fetch, deadline=budget.deadline))

    def look_workable(company, titles):
        query = urlencode(dict(query=company, day_range=14, location=profile['work_rules']['home_country'].strip()))
        markup = read_text('GET', f'{workable.ORIGIN}/jobs.md?{query}')
        name = job_key(company, '')[0]
        candidates = {}
        for job in workable.listing_rows(markup, workable.listing_total(markup), boards):
            company_key, title_key = job_key(job['company'], job['title'])
            if company_key == name and title_key in titles:
                candidates.setdefault(title_key, []).append(job)
        for title_key in titles:
            for job in candidates.get(title_key, []):
                try:
                    detail = read_text('GET', job['link'])
                except HTTPError as error:
                    if error.code not in (404, 410):
                        raise
                    error.close()
                    continue
                # Require the title and company evidence from the detail itself.
                job.update(company='', title='')
                workable.posting_data(detail, job, today, boards)
                if (job['open'] is True and job_key('', job['title'])[1] == title_key
                        and confirms_company(job, job['description'], company, boards, job_key)):
                    yield (name, title_key), dict(job, company=job['company'] or company)
                    break

    def look(board, company, titles, proven):
        rows = boards.board_rows(board, read_board, today=today)
        name = job_key(company, '')[0]
        if board['provider'] == 'greenhouse' and not proven:
            metadata = read_board('GET', boards.source_url(board).removesuffix('/jobs'))
            label = metadata.get('name') if isinstance(metadata, dict) else None
            if not isinstance(label, str) or not label.strip():
                raise boards.InvalidBoardResponse('returned no board name', 200)
            if job_key(label, '')[0] != name:
                return False, {}
            proven = True
        found = {}
        for raw in rows:
            result = boards.normalize(board, raw, day)
            if result is None or raw.get('isListed') is False:
                continue
            result['description'] = result['description'] or boards.clean_text(
                raw.get('descriptionHtml') or raw.get('description'), 20000)
            if board['provider'] != 'greenhouse':
                label = named_company(raw)
                if label and job_key(label, '')[0] != name:
                    continue
                proven = proven or bool(label) or boards.phrase_matches(company, result['description'])
            key = job_key(company, result['role'])
            if key[1] in titles:
                found.setdefault(key, dict(employer_job(result), open=True))
        return proven, found if proven else {}

    companies = 0
    for name, leads in groups.items():
        cached, checked, stored = remembered.get(name, (None, None, None))
        if cached == NO_BOARD:
            continue
        if companies == 15:
            break
        companies += 1
        company = leads[0]['company']
        titles = dict.fromkeys(job_key('', lead['title'])[1] for lead in leads)
        options = [cached] if cached else [
            dict(provider=provider, board=token, label=company) for token in slugs(leads)
            for provider in ('greenhouse', 'lever', 'ashby')]
        if not cached:
            options.append(dict(provider=workable.SOURCE))
        chosen, workable_match = None, False
        exhausted = False
        for board in options:
            if (board['provider'], board.get('board')) in configured:
                continue
            try:
                if board['provider'] == workable.SOURCE:
                    for key, job in look_workable(company, titles):
                        matched[key] = job
                        workable_match = True
                else:
                    proven, jobs = look(board, company, titles, bool(cached))
                    if proven:
                        matched.update(jobs)
                        chosen = board
                        break
            except LimitReached:
                exhausted = True
                break
            except (OSError, ValueError, boards.HTTPException, RecursionError) as error:
                failure = boards.board_failure(error)
                if failure.status in (404, 410):
                    if cached:
                        expired.append((owner, name, checked, stored))
                else:
                    status = f' (HTTP {failure.status})' if failure.status is not None else ''
                    errors.setdefault(name, f'An employer board {failure.explanation}{status}.')
        if not cached and (chosen or (not exhausted and name not in errors and not workable_match)):
            updates.append((owner, name, day, json.dumps(chosen or NO_BOARD)))
        if exhausted:
            break
    with backlog_connect() as db, db:
        db.executemany('DELETE FROM discovery_boards WHERE profile_id=? AND company=? AND checked_on=? AND board=?',
                       expired)
        db.executemany('''INSERT INTO discovery_boards VALUES (?,?,?,?)
            ON CONFLICT(profile_id,company) DO UPDATE SET checked_on=excluded.checked_on,board=excluded.board
            WHERE excluded.checked_on > discovery_boards.checked_on''', updates)
    return matched, errors
