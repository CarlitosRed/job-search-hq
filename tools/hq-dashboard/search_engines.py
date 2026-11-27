"""Execute checked search plans through bounded engine adapters."""
from base64 import b64encode
from datetime import timedelta
from functools import cache, partial
from http.client import HTTPException
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError
from urllib.parse import quote, unquote, unquote_plus, urlencode, urljoin, urlsplit
from urllib.request import Request

HERE = Path(__file__).resolve().parent


@cache
def load(name, folder=HERE / 'search_engines'):
    spec = importlib.util.spec_from_file_location('review_' + name, folder / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


search = load('daily_search', HERE)
links = load('link_fetch', HERE)
careers = load('careers_page')
FEEDS = ('agent_search', 'web_search', 'adzuna', 'jooble', 'careerjet', 'remotive', 'remoteok', 'arbeitnow', 'linkedin')
LEGACY = ('configured_boards', 'workday', 'workable_search', 'himalayas', 'jobicy')
NAMES = (*FEEDS, *LEGACY, 'careers_page')
KEYED = tuple(search.settings.SEARCH_KEY_FIELDS)
AGENT = 'JobSearchHQ/1.0 (public job search)'
NOT_CONFIRMED = 'The lead could not be confirmed on the employer’s website.'
SITE_BLOCKED = 'The service requires a CAPTCHA, bot check or sign-in.'
MOVE_SEARCH_KEY = 'Move this key to work/search-keys.json; keys in settings.json are ignored.'


class ReadStopped(ValueError):
    pass


class SiteBlocked(ReadStopped):
    pass


def api_request(method, url, body=None, *, headers=None, timeout=10):
    request = Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                      headers={'User-Agent': AGENT, 'Accept': 'application/json',
                               'Content-Type': 'application/json', **(headers or {})})
    raw = search.boards.fetch_bounded(request, time.monotonic() + timeout, 8_000_000)
    if raw.lstrip().startswith(b'<') and careers.CHALLENGE.search(raw[:5000].decode('utf-8', 'replace')):
        raise SiteBlocked(SITE_BLOCKED)
    return json.loads(raw)


class Transport:
    def __init__(self, fetch, pace, cap, timeout, blocked):
        self.fetch, self.pace, self.left, self.timeout = fetch, pace, cap, timeout
        self.blocked, self.last = blocked, {}

    def __call__(self, method, url, body=None, *, headers=None):
        domain = careers.host(url)
        if domain in self.blocked:
            raise ReadStopped(self.blocked[domain])
        if self.left <= 0:
            raise ReadStopped('The request limit for this engine was reached.')
        time.sleep(max(0, self.last.get(domain, 0) + self.pace - time.monotonic()))
        self.left -= 1
        self.last[domain] = time.monotonic()
        try:
            return self.fetch(method, url, body, headers=headers, timeout=self.timeout)
        except SiteBlocked:
            self.blocked[domain] = SITE_BLOCKED
            raise ReadStopped(SITE_BLOCKED) from None
        except ReadStopped:
            raise ReadStopped('The service could not be read.') from None
        except HTTPError as error:
            code = error.code
            error.close()
            reason = f'The service returned HTTP {code}.'
            if code in (401, 403, 429):
                self.blocked[domain] = reason
            raise ReadStopped(reason) from None


def page_request(url):
    deadline = links.Deadline(careers.TIMEOUT)
    try:
        return links._get(url, 'text/html, application/xhtml+xml', deadline, with_status=True,
                          agent=AGENT, stop_at_login=True, include_final_url=True)
    finally:
        deadline.stop()


def native_plans(profile):
    rules = profile['work_rules']
    places = [rules['home_country'], 'Remote', *rules['authorized_countries']]
    if rules.get('relocation_wish'):
        places.append('Abroad with relocation')
    words = search.lane_queries(profile)
    return [dict(words=word, place=place) for word in words
            for place in dict.fromkeys(places)][:30]


def options_for(settings, name):
    options = settings.get(name, {})
    return options if isinstance(options, dict) else {}


def configured_options(settings, root=None):
    """Resolve file-backed engine options before planning or dispatch."""
    credentials, error = search.settings.load_search_keys(root)
    effective, stopped = dict(settings), {}
    for name, fields in search.settings.SEARCH_KEY_FIELDS.items():
        options = dict(options_for(settings, name))
        private = credentials.get(name, {})
        inline = {field: options.pop(field, '') for field in fields}
        if error:
            stopped[name] = error
        elif any(value and field not in private for field, value in inline.items()):
            stopped[name] = MOVE_SEARCH_KEY
        effective[name] = dict(options, **private)
    return effective, stopped, error


def enabled(settings, name):
    options = options_for(settings, name)
    if options.get('enabled') is False:
        return False
    if name in FEEDS:
        return load(name).enabled(options)
    return True


def public_hits(rows, options, name):
    """Discard results that echo request credentials before they reach discovery or storage."""
    secrets = {options[field] for field in search.settings.SEARCH_KEY_FIELDS.get(name, ())
               if options.get(field)}
    if not secrets:
        return rows
    if name == 'careerjet':
        secrets.add(b64encode((options['key'] + ':').encode()).decode())

    def echoes_credentials(value):
        return isinstance(value, str) and any(
            secret in text for text in (value, unquote(value), unquote_plus(value)) for secret in secrets)

    return [row for row in rows if not any(echoes_credentials(row.get(field)) for field in ('url', 'title', 'company'))]


def queries_for(name, plan, profile):
    rows = [row for row in plan['queries'] if row['engine'] == name]
    if not rows:
        rows = plan['queries'] or native_plans(profile)
    return list({(row['words'], row['place']): row for row in rows}.values())


def board_key(board):
    return '|'.join(board.get(field, '').casefold() for field in ('provider', 'board', 'host', 'site'))


def board_memory(db):
    db.execute("""CREATE TABLE IF NOT EXISTS review_boards (
        profile_id TEXT NOT NULL, key TEXT NOT NULL, body TEXT NOT NULL, added_on TEXT NOT NULL,
        why TEXT NOT NULL, last_postings TEXT, dropped_on TEXT, reason TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(profile_id,key))""")


def remembered_boards(backlog, owner):
    with search.connect(backlog) as db, db:
        board_memory(db)
        return [json.loads(row[0]) for row in db.execute("""SELECT body FROM review_boards
                    WHERE profile_id=? AND dropped_on IS NULL ORDER BY added_on,key""", (owner,))]


def remember_board(backlog, owner, board, day, answered, count, reason):
    stamp = day.isoformat()
    key = board_key(board)
    with search.connect(backlog) as db, db:
        board_memory(db)
        if answered:
            db.execute("""INSERT INTO review_boards (profile_id,key,body,added_on,why,last_postings)
                          VALUES (?,?,?,?,?,?) ON CONFLICT(profile_id,key) DO UPDATE SET
                          last_postings=COALESCE(excluded.last_postings,review_boards.last_postings),
                          dropped_on=CASE WHEN excluded.last_postings IS NOT NULL THEN NULL ELSE dropped_on END""",
                       (owner, key, json.dumps(board), stamp, board['why'], stamp if count else None))
        db.execute("""UPDATE review_boards SET reason=?, dropped_on=CASE
                      WHEN COALESCE(last_postings,added_on)<=? THEN ? ELSE dropped_on END
                      WHERE profile_id=? AND key=?""",
                   (reason, (day - timedelta(days=30)).isoformat(), stamp, owner, key))


def normalize_extra(raw, board, day):
    provider = board['provider']
    location = raw.get('location') or {}
    if isinstance(location, dict):
        place = ', '.join(filter(None, [location.get('city'), location.get('region'), location.get('country')]))
        if location.get('remote'):
            place += ' Remote'
    else:
        place = str(location)
    title = raw.get('name') or raw.get('title')
    company = (raw.get('company') or {}).get('name') if isinstance(raw.get('company'), dict) else ''
    posting_id = raw.get('id') or raw.get('shortcode') or raw.get('job_path')
    link = raw.get('postingUrl') or raw.get('url') or raw.get('shortlink') or raw.get('jobUrl')
    stamp = raw.get('releasedDate') or raw.get('published_on') or raw.get('posted_date')
    ad = raw.get('jobAd')
    sections = ad.get('sections') if isinstance(ad, dict) else None
    text = ' '.join(value.get('text', '') for value in sections.values()
                    if isinstance(value, dict) and isinstance(value.get('text', ''), str)) if isinstance(sections, dict) else ''
    description = raw.get('description') or raw.get('description_short') or text
    if provider == 'smartrecruiters' and posting_id and not link:
        link = f"https://jobs.smartrecruiters.com/{board['board']}/{posting_id}"
    if provider == 'amazon':
        link = urljoin('https://www.amazon.jobs/', raw.get('job_path') or '')
        place = ', '.join(filter(None, (raw.get('location'), raw.get('country_code'))))
        if isinstance(stamp, str):
            for pattern in ('%B %d, %Y', '%b %d, %Y'):
                try:
                    stamp = time.strftime('%Y-%m-%d', time.strptime(stamp, pattern))
                    break
                except ValueError:
                    continue
    if not (title and search.boards.safe_link(link)):
        return None
    return dict(title=careers.field(title), company=careers.field(company) or board['label'], link=link,
                provider=provider, board=board['board'], posting_id=str(posting_id or link),
                place_text=place, remote=search.boards.remote_location(place), open=raw.get('active') is not False,
                posted_on=search.boards.iso_day(stamp), date_source=provider,
                description=search.boards.clean_text(description, 20000))


def extra_rows(board, queries, fetch, day):
    token = quote(board['board'], safe='')
    provider = board['provider']
    if provider == 'workable':
        data = fetch('GET', f'https://www.workable.com/api/accounts/{token}?details=true')
        raw = data['jobs']
    elif provider == 'smartrecruiters':
        endpoint = f'https://api.smartrecruiters.com/v1/companies/{token}/postings'
        data = fetch('GET', endpoint + '?limit=100&destination=PUBLIC')
        raw = data['content']
        if isinstance(raw, list):
            for row in raw[:20]:
                if not isinstance(row, dict) or not row.get('id'):
                    continue
                token = quote(str(row['id']), safe='')
                try:
                    detail = fetch('GET', f'{endpoint}/{token}')
                except ReadStopped:
                    break
                except (OSError, ValueError):
                    continue
                if isinstance(detail, dict):
                    row.update(detail)
    else:
        raw = []
        for query in queries[:4]:
            params = urlencode(dict(base_query=query['words'], loc_query=query['place'], result_limit=20))
            raw.extend(fetch('GET', 'https://www.amazon.jobs/en/search.json?' + params)['jobs'])
    if not isinstance(raw, list):
        raise ValueError('The employer API did not return a posting list.')
    return [job for row in raw if isinstance(row, dict) and (job := normalize_extra(row, board, day))], len(raw)


def find(profile, plan, day, backlog, settings, *, api=None, page=None, board_fetch=None, registry=None,
         board_reads=None, jobicy_pages=None, allowed=lambda text, **kwargs: True, stopped=None,
         profile_root=None, agent_http=None, usage=None, run_id=None, agent_feedback=(), agent_allowed=None):
    owner = profile['id']
    if registry is not None:
        results = []
        for name, reader in registry.items():
            jobs = reader(queries_for(name, plan, profile))
            results.append((name, '', jobs, len(jobs), ''))
        return results
    api = api or api_request
    page = page or page_request
    denied_hosts = search.settings.load()['no_automatic_read_hosts']
    read_disallowed = partial(links.automatic_read_disallowed, hosts=denied_hosts)
    blocked, batches, hits = {}, [], {}
    web = None
    stopped = stopped or {}
    for name in FEEDS:
        module = load(name)
        options = options_for(settings, name)
        if name in stopped or not enabled(settings, name):
            if name in stopped:
                reason = stopped[name]
            elif options.get('enabled') is False or name == 'agent_search':
                reason = 'This engine is switched off.'
            elif name == 'linkedin':
                reason = 'No LinkedIn alert export is configured.'
            else:
                reason = 'The API key, account ID or supported provider setting is missing.'
            batches.append((name, '', [], 0, reason))
            continue
        queries = [row for row in queries_for(name, plan, profile)
                   if allowed(row['words']) and (not row['place'] or allowed(row['place']))]
        if name == 'agent_search':
            found, reason = [], ''
            root = Path(profile_root) if profile_root is not None else search.settings.data_root()
            try:
                agent_guard = agent_allowed or allowed
                store = module.auth.Store(root, owner, http=agent_http)
                ledger = module.Usage(usage or root / 'work' / 'ai-usage.sqlite3', owner,
                                      day, run_id or day.isoformat())
                context = module.context(profile, queries, plan['employers'], list(agent_feedback), agent_guard)
                found, reason = module.read(store, options, ledger, context, agent_guard, search.boards.safe_link)
            except module.auth.Stop as error:
                reason = str(error)
            except (OSError, ValueError, TypeError, sqlite3.Error):
                reason = 'The search agent could not finish this run.'
            hits[name] = found
            batches.append((name, '', [], len(found), reason))
            continue
        transport = Transport(api, module.PACE if api is api_request else 0, module.CAP, module.TIMEOUT, blocked)
        if name == 'web_search':
            web = (module, transport)
            queries = queries[:30] + [dict(words=item['name'] + ' careers', place=item.get('place', ''))
                                       for item in plan['employers'][:15]]
        found, errors = [], []
        # Keep successful batches when a later request fails. Feed and mail engines read once.
        groups = [[query] for query in queries[:module.CAP]] if name in KEYED else [queries]
        for group in groups:
            try:
                found.extend(public_hits(module.read(transport, group, options), options, name))
            except (OSError, ValueError, KeyError, TypeError, AttributeError, HTTPException) as error:
                errors.append(str(error) if isinstance(error, ReadStopped) else 'The service could not be read.')
                break
        hits[name] = [dict(url=careers.field(row.get('url'), 2000), title=careers.field(row.get('title')),
                           company=careers.field(row.get('company'))) for row in found]
        batches.append((name, '', [], len(found), ' '.join(errors)))

    # Aggregator URLs are leads, and their titles/descriptions cannot establish facts.
    # The web provider can seek the original by matching employer and title.
    leads = [row for name, rows in hits.items() if name not in ('web_search', 'agent_search')
             for row in rows if row['company'] and row['title']]
    leads = [row for row in leads if allowed(row['company']) and allowed(row['title'])
             and any(titles for _, titles, _ in search.lane_matches(profile, row['title'], ''))]
    if web:
        module, transport = web
        for lead in list({(r['company'], r['title']): r for r in leads}.values())[:8]:
            if transport.left <= 0:
                break
            try:
                query = dict(words=f"{lead['company']} {lead['title']} careers", place='')
                options = options_for(settings, 'web_search')
                hits['web_search'].extend(public_hits(module.read(transport, [query], options), options, 'web_search'))
            except (OSError, ValueError, KeyError, TypeError, AttributeError, HTTPException):
                break
    urls = [row['url'] for name, rows in hits.items() if name != 'agent_search' for row in rows
            if search.boards.safe_link(row['url']) and not careers.is_lead(row['url'])]
    urls[:0] = [item['url'] for item in plan['employers'] if item.get('url')]
    employers = [(item['name'], item['why']) for item in plan['employers']]
    employers.extend((row['company'], 'An aggregator found a matching title.') for row in leads)
    candidates = {}
    configured = profile['search']['companies']
    configured_keys = {board_key(board) for board in configured}
    known = remembered_boards(backlog, owner)
    for board in known:
        candidates[board_key(board)] = board

    page_jobs, page_errors, agent_jobs = [], [], []
    agent_reasons, agent_boards = {}, set()
    if enabled(settings, 'careers_page'):
        last = {}

        def polite(url):
            domain = careers.host(url)
            if page is page_request:
                time.sleep(max(0, last.get(domain, 0) + careers.PACE - time.monotonic()))
                last[domain] = time.monotonic()
            return page(url)

        page_jobs, _, detected, page_errors = careers.read(
            polite, [url for url in urls if not read_disallowed(url)], day, blocked, links._closed)
        url_boards = [(url, careers.board_for(url)) for url in urls]
        for board in [*detected, *(b for url, b in url_boards if b and not careers.host(url) in blocked)]:
            domain = board.get('host', '')
            if domain and domain in blocked:
                continue
            token = re.sub(r'\W', '', board['board']).casefold()
            name, why = next(((name, why) for name, why in employers
                              if re.sub(r'\W', '', name).casefold() == token),
                             (board['board'], 'Found on a public careers page or in a web search.'))
            candidates.setdefault(board_key(board), dict(board, label=name, why=why, found_by=why))
        for name, why in list(dict.fromkeys(employers))[:30]:
            token = re.sub(r'[^a-z0-9]', '', name.casefold())[:80]
            if not token:
                continue
            for provider in ('greenhouse', 'lever', 'ashby', 'smartrecruiters', 'workable'):
                board = dict(provider=provider, board=token, label=name, why=why, found_by=why)
                candidates.setdefault(board_key(board), board)
        for url in urls:
            if careers.host(url) in ('amazon.jobs', 'www.amazon.jobs') and careers.host(url) not in blocked:
                board = dict(provider='amazon', board='amazon', label='Amazon', why='Found by web search.',
                             found_by='Public jobs search')
                candidates.setdefault(board_key(board), board)
        if any(item['name'].casefold() == 'amazon' for item in plan['employers']):
            board = dict(provider='amazon', board='amazon', label='Amazon', why='Named in the search plan.',
                         found_by='Public jobs search')
            candidates.setdefault(board_key(board), board)
        agent_reasons = {row['url'].split('#', 1)[0]: row['reason'] for row in hits.get('agent_search', [])
                         if not careers.is_lead(row['url']) and not read_disallowed(row['url'])}
        if agent_reasons:
            def read_agent_page(url):
                answer = polite(url)
                if answer and len(answer) > 3 and url in agent_reasons:
                    agent_reasons.setdefault(answer[3].split('#', 1)[0], agent_reasons[url])
                return answer

            agent_urls = list(agent_reasons)
            agent_jobs, _, detected, errors = careers.read(read_agent_page, agent_urls, day, blocked, links._closed)
            page_errors.extend(errors)
            direct = [board for url in agent_urls if careers.host(url) not in blocked
                      and (board := careers.board_for(url))]
            for board in [*detected, *direct]:
                key = board_key(board)
                if key not in candidates and key not in configured_keys:
                    agent_boards.add(key)
                    candidates[key] = dict(board, label=board['board'], why='Found by the search agent.')
    else:
        page_errors.append('This engine is switched off.')

    legacy_transport = Transport(api, 0.3 if api is api_request else 0, 450, 12, blocked)

    def public_read(fetch, method, url, *args, **kwargs):
        if read_disallowed(url):
            raise ReadStopped(links.READ_DISALLOWED_REASON)
        domain = careers.host(url)
        if domain in blocked:
            raise search.boards.BoardReadError('blocked access on this host', 403)
        try:
            return fetch(method, url, *args, **kwargs)
        except HTTPError as error:
            code = error.code
            error.close()
            if code in (401, 403, 429):
                blocked[domain] = f'The site blocked access (HTTP {code}).'
                raise search.boards.BoardReadError('the site blocked access', code) from None
            raise

    direct_api = partial(public_read, Transport(api, 0.3 if api is api_request else 0, 100, 10, blocked))

    def get_board(method, url, body=None):
        try:
            return public_read(board_fetch or legacy_transport, method, url, body)
        except ReadStopped as error:
            raise search.boards.BoardReadError(str(error)) from None

    def get_workable(method, url, *, deadline):
        return public_read(search.workable.fetch_text, method, url, deadline=deadline, boards=search.boards)

    def get_feed(method, url, *, deadline):
        if api is not api_request:
            return public_read(api, method, url, timeout=max(1, deadline - time.monotonic()))
        return public_read(search.discovery.fetch_json, method, url, deadline=deadline, boards=search.boards)

    def legacy(name, queries):
        module = load(name)
        companies = []
        board_reader = get_board
        if name in ('configured_boards', 'workday'):
            companies = [board for board in configured if (board['provider'] == 'workday') == (name == 'workday')]
            if not companies:
                return [(name, '', [], 0, 'No starting boards are configured for this engine.')]
            transport = Transport(api, module.PACE if api is api_request else 0, module.CAP, module.TIMEOUT, blocked)

            def board_reader(method, url, body=None):
                try:
                    return public_read(board_fetch or transport, method, url, body)
                except ReadStopped as error:
                    raise search.boards.BoardReadError(str(error)) from None

        legacy_profile = dict(profile, search=dict(profile['search'], companies=companies))
        query_map = {}
        if name == 'workable_search':
            query_map[name] = [{field: row[field] for field in ('words', 'place')} for row in queries]
        elif name in ('himalayas', 'jobicy'):
            query_map[name] = [row['words'] for row in queries]
        return search.run(legacy_profile, board_reader, backlog, day, wait=0, review_queries=query_map,
                          review_reads=board_reads, jobicy_pages=jobicy_pages, search_fetch=get_workable,
                          discovery_fetch=get_feed)

    for name in LEGACY:
        if not enabled(settings, name):
            batches.append((name, '', [], 0, 'This engine is switched off.'))
            continue
        module = load(name)
        queries = [row for row in queries_for(name, plan, profile)
                   if allowed(row['words']) and (not row['place'] or allowed(row['place']))]
        try:
            result = module.read(legacy, queries, options_for(settings, name))
        except (OSError, ValueError, KeyError, TypeError):
            batches.append((name, '', [], 0, 'This engine could not finish reading its sources.'))
        else:
            for provider, token, jobs, count, reason in result:
                label = provider.title() if name == 'configured_boards' else module.LABEL
                for job in jobs:
                    job.setdefault('found_by', label)
                batches.append((name, token, jobs, count, reason))

    dynamic_jobs = []
    for key, board in list(candidates.items())[:120] if enabled(settings, 'careers_page') else []:
        if key in configured_keys:
            continue
        provider = board['provider']
        public_host = {'greenhouse': 'boards.greenhouse.io', 'lever': 'jobs.lever.co', 'ashby': 'jobs.ashbyhq.com',
                       'workable': 'apply.workable.com', 'smartrecruiters': 'jobs.smartrecruiters.com',
                       'amazon': 'www.amazon.jobs'}.get(provider, board.get('host'))
        try:
            if public_host in blocked:
                raise ReadStopped(blocked[public_host])
            if provider in search.profiles.PROVIDERS:
                readings = []
                scoped = dict(profile, search=dict(profile['search'], companies=[board]))
                result = search.run(scoped, get_board, backlog, day, wait=0, review_queries={}, review_reads=readings)
                _, _, jobs, count, reason = result[0]
                reading = readings[0]
                answered = reading['read'] in ('complete', 'partial') and reading.get('http_status', 200) in (None, 200)
                count = reading['rows']
                if board_reads is not None and answered:
                    board_reads.extend(readings)
            else:
                jobs, count = extra_rows(board, queries_for('careers_page', plan, profile), direct_api, day)
                answered, reason = True, ''
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            answered, count, jobs = False, 0, []
            reason = str(error) if isinstance(error, ReadStopped) else 'The public employer API did not answer.'
        if key in agent_boards:
            agent_jobs.extend(jobs)
            continue
        remember_board(backlog, owner, board, day, answered, count, reason or ('No postings were returned.' if not count else ''))
        for job in jobs:
            job['found_by'] = 'Employer board discovery'
        dynamic_jobs.extend(jobs)
        if reason and key in {board_key(row) for row in known}:
            page_errors.append(reason)

    employer_jobs = [job for _, _, jobs, _, _ in batches for job in jobs] + page_jobs + dynamic_jobs
    confirmed = {search.job_key(job['company'], job['title']): job for job in employer_jobs if job.get('open')}
    for index, (name, token, jobs, total, reason) in enumerate(batches):
        if name not in FEEDS:
            continue
        if name == 'agent_search':
            if not agent_reasons:
                continue
            explicitly_closed = {job['link'].split('#', 1)[0] for job in agent_jobs
                                 if job.get('open') is False and not job.get('lead')}
            for job in sorted([*agent_jobs, *employer_jobs], key=lambda row: row.get('posted_on') or '', reverse=True):
                link = job['link'].split('#', 1)[0]
                if job.get('open') is True and link in agent_reasons and link not in explicitly_closed:
                    jobs.append(dict(job, found_by=load(name).LABEL, agent_reason=agent_reasons.pop(link)))
            continue
        for hit in hits.get(name, []):
            key = search.job_key(hit.get('company'), hit.get('title'))
            match = confirmed.get(key) if all(key) else None
            if match:
                jobs.append(dict(match, found_by=load(name).LABEL, source_link=hit['url']))
            elif name != 'web_search' and hit['url']:
                jobs.append(dict(provider=name, posting_id=hit['url'], company=hit['company'],
                                 title=hit['title'], link=hit['url'], open=False, lead=True))
        batches[index] = (name, token, jobs, total, reason)
    for job in page_jobs:
        job['found_by'] = 'Web search' if job['link'] in {row['url'] for row in hits.get('web_search', [])} else 'Employer careers page'
    batches.append(('careers_page', '', page_jobs + dynamic_jobs, len(page_jobs) + len(dynamic_jobs),
                    ' '.join(dict.fromkeys([*page_errors, *blocked.values()]))))
    batches.extend(('careers_page', domain, [], 0, reason) for domain, reason in blocked.items() if domain)
    return batches
