"""Profile-driven public job discovery and a durable, deterministic landing queue."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timedelta
from functools import partial
import importlib.util
from itertools import zip_longest
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError

# Card 03: closing and re-check.


def load_component(name):
    spec = importlib.util.spec_from_file_location('daily_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


boards = load_component('job_search')
profiles = load_component('search_profile')
settings = load_component('hq_settings')
workable = load_component('workable_search')
discovery = load_component('discovery_sources')


def distinct(phrases):
    result, seen = [], set()
    for phrase in phrases:
        key = tuple(re.findall(r'\w+', phrase.casefold()))
        if key and key not in seen:
            result.append(phrase)
            seen.add(key)
    return result


def lane_matches(profile, title, description):
    """Return each lane's whole-phrase title and duty matches, in profile order."""
    return [(lane['id'],
             distinct(phrase for phrase in lane['titles'] if boards.phrase_matches(phrase, title)),
             distinct(phrase for phrase in lane['duties'] if boards.phrase_matches(phrase, title + ' ' + description)))
            for lane in profile['goals']['lanes']]


def exclusion_reason(profile, job):
    for rule in profile['search']['exclude_companies']:
        if rule['name'].strip().casefold() == job['company'].strip().casefold():
            return rule['reason']
    for rule in profile['search']['exclude_titles']:
        if boards.phrase_matches(rule['pattern'], job['title']):
            return rule['reason']
    return ''


def place_facts(profile, job):
    rules = profile['work_rules']
    location, description = job['place_text'], job['description']
    authorized = rules['authorized_countries']
    countries = [rules['home_country'], *authorized]
    if 'applicant_countries' in job:
        names = job['applicant_countries']
        allowed = {name.strip().casefold() for name in countries}
        return ('ok' if allowed.intersection(name.casefold() for name in names) else 'check'), ', '.join(names)
    if job.get('provider') == workable.SOURCE and job['remote']:
        return 'check', location
    scope = job.get('workday_country_scope')
    if scope and scope == json.dumps(sorted(profiles.workday_countries(profile))):
        return 'ok', location
    if any(boards.phrase_matches(country, location) for country in countries):
        return 'ok', location
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+|[\r\n]+', description) if s.strip()]
    refusals = ('no visa sponsorship', 'unable to sponsor', 'without sponsorship', 'must already be authorized to work')
    if rules['sponsorship_needed'] and not any(boards.phrase_matches(country, location) for country in authorized):
        for sentence in sentences:
            if any(boards.phrase_matches(phrase, sentence) for phrase in refusals):
                return 'out', sentence
    if job['remote']:
        # Rule 3 (spec 06 ranking and landing rules, 2026-11-14)
        remaining = ' '.join(word for word in re.findall(r'\w+', location.casefold())
                             if word not in ('remote', 'anywhere', 'distributed', 'hybrid', 'office', 'home', 'based'))
        if remaining:
            regions = [*rules['remote_regions'], *countries, 'worldwide', 'anywhere', 'global']
            return ('ok' if any(boards.phrase_matches(region, remaining) for region in regions) else 'check'), location
        for sentence in [location, *sentences]:
            if any(boards.phrase_matches(region, sentence) for region in [*rules['remote_regions'], 'worldwide', 'anywhere']):
                return 'ok', sentence
    return 'check', location


# Rule 2 (spec 06 ranking and landing rules, 2026-11-14)
def offers_help(description):
    help_phrases = ('relocation', 'relocation package', 'relocation assistance', 'relocation support',
                    'visa sponsorship', 'sponsor a visa', 'sponsor your visa', 'will sponsor',
                    'we sponsor', 'provide visa sponsorship')
    refusals = ('no visa sponsorship', 'unable to sponsor', 'without sponsorship')
    sentences = re.split(r'(?<=[.!?])\s+|[\r\n]+', description)
    return any(not any(boards.phrase_matches(phrase, sentence) for phrase in refusals) and
               any(boards.phrase_matches(phrase, sentence) for phrase in help_phrases)
               for sentence in sentences)


def rank(profile, job):
    matches = lane_matches(profile, job['title'], job['description'])
    titles = distinct(phrase for _, found, _ in matches for phrase in found)
    duties = distinct(phrase for _, _, found in matches for phrase in found)
    if not titles and not duties:
        return None
    lane = next((lane for lane, found, _ in matches if found), None)
    if lane is None:
        lane = max(matches, key=lambda match: len(match[2]))[0]
    place, sentence = place_facts(profile, job)
    seniority = profile['goals']['seniority']
    allowed = any(boards.phrase_matches(word, job['title']) for word in seniority['allowed'])
    stretch = any(boards.phrase_matches(word, job['title']) and
                  not any(boards.phrase_matches(allowed_word, word) for allowed_word in seniority['allowed'])
                  for word in seniority['stretch'])
    penalty = -1 if stretch else 0 if allowed else -3
    if not job['remote'] and place == 'check' and not offers_help(job['description']):
        penalty -= 6
    return dict(job, lane=lane, score=(6 if titles else 0) + min(10, 2 * len(duties)) +
                {'ok': 3, 'check': 0, 'out': -10}[place] + penalty,
                matched_titles=titles, matched_duties=duties, place=place,
                place_sentence=sentence, fit='pending', judge=False)


def identity(job):
    return json.dumps([job['provider'], job['company'], job['posting_id']], ensure_ascii=False)


def job_key(company, title):
    return tuple(' '.join(re.findall(r'\w+', field.casefold())) for field in (company or '', title or ''))


KEPT_FOR_REVIEW = 'Kept for review'
SKIPPED = 'This job was skipped.'
ARCHIVED = 'This job was archived.'
ON_TRACKER = 'This job is already on the tracker.'
EMPLOYER_COPY = 'The employer board already has this posting.'
SEARCH_COPY = 'This search already found another copy of the posting.'


def posting_facts(profile, job, today, repeat=''):
    """Return one review decision for a posting from any daily search source."""
    if repeat:
        return repeat
    if job.get('read_reason'):
        return job['read_reason']
    if job.get('open') is not True:
        return 'The posting could not be confirmed open.'
    posted = job.get('posted_on')
    confirmed = boards.iso_day(posted)
    if (not confirmed or confirmed != posted
            or job.get('date_source') not in (*profiles.PROVIDERS, workable.SOURCE)
            or posted > today.isoformat()):
        return 'The employer posting date could not be confirmed.'
    earliest = min(posted, job['first_seen'], job.get('earliest_posted_on') or posted,
                   job.get('workday_earliest_posted_on') or posted)
    limit = profile['search'].get('landing_max_age_days', profiles.DEFAULTS['landing_max_age_days'])
    if (today - date.fromisoformat(earliest)).days > limit:
        return f'The posting is older than {limit} days.'
    if place_facts(profile, job)[0] != 'ok':
        return 'The place where you can work could not be confirmed.'
    return exclusion_reason(profile, job) or KEPT_FOR_REVIEW


def tracker_repeats(tracker, profile_id):
    """Read tracker identities from this profile's links and its manual jobs."""
    if tracker is None or not Path(tracker).is_file():
        return {}, {}
    uri = Path(tracker).resolve().as_uri() + '?mode=ro'
    titles, links = {}, {}
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute('BEGIN')
        columns = {row[1] for row in db.execute('PRAGMA table_info(postings)')}
        owners = {}
        if columns:
            for job_id, owner in db.execute('SELECT job_id,profile_id FROM postings'):
                owners.setdefault(job_id, set()).add(owner)
        archived_id = {row[1] for row in db.execute('PRAGMA table_info(old_jobs)')}
        archived_id = 'original_job_id' if 'original_job_id' in archived_id else 'NULL'
        for table, ref in [('jobs', 'id'), ('old_jobs', archived_id)]:
            for row_id, company, role, link, status in db.execute(
                    f'SELECT {ref},company,role,link,status FROM {table}'):
                if row_id in owners and profile_id not in owners[row_id]:
                    continue
                reason = (ARCHIVED if table == 'old_jobs' else SKIPPED if (status or '').casefold() == 'skipped'
                          else ON_TRACKER)
                key = job_key(company, role)
                if all(key) and titles.get(key) not in (SKIPPED, ARCHIVED):
                    titles[key] = reason
                if link and links.get(link) not in (SKIPPED, ARCHIVED):
                    links[link] = reason
    return titles, links


def save_search(db, profile, today, source, jobs, runs, previous, employer_keys, repeat_reason, saved_identities):
    profile_id = profile['id']
    day = today.isoformat()
    # Workable's UUID survives spelling changes to its published company name.
    prior = {job['posting_id']: (key, first, job) for key, (first, job, _) in previous.items()
             if job['provider'] == workable.SOURCE}
    decisions, winners = {}, set()

    def copy_reason(job, key):
        return (repeat_reason(job) or (EMPLOYER_COPY if key in employer_keys else '')
                or (SEARCH_COPY if key in winners else ''))

    for token, job in jobs.items():
        key = identity(job)
        old_key = key
        first, old, _ = previous.get(key, (day, {}, 0))
        if job['provider'] == workable.SOURCE and job['posting_id'] in prior:
            old_key, first, old = prior[job['posting_id']]
            if old.get('discovery_only') and source == workable.SOURCE:
                for field in ('found_by', 'source_credit', 'source_link'):
                    job[field] = old[field]
        lead_key = job.get('discovery_lead_identity')
        if lead_key in previous:
            first = min(first, previous[lead_key][0])
        job['first_seen'] = first
        # A failed refresh leaves the date unknown. Remember the earliest proven
        # day separately so a later success cannot make a repost younger.
        prior_date = old.get('earliest_posted_on')
        if job['posted_on']:
            if not job.get('discovery_only'):
                job['posted_on'] = min(job['posted_on'], prior_date or first, first)
            prior_date = min(job['posted_on'], prior_date or job['posted_on'])
        job['earliest_posted_on'] = prior_date
        title_key = job_key(job['company'], job['title'])
        decision = posting_facts(profile, job, today, copy_reason(job, title_key) or job.get('fact_reason', ''))
        if decision == KEPT_FOR_REVIEW:
            winners.add(title_key)
        decisions[token] = job['fact_reason'] = decision
        obsolete = {old_key, lead_key} - {None, key}
        db.executemany('DELETE FROM search_backlog WHERE profile_id=? AND identity=? AND landed=0',
                       [(profile_id, alias) for alias in obsolete])
        db.execute('''INSERT INTO search_backlog (profile_id,identity,first_seen,body,landed)
            VALUES (?,?,?,?,0) ON CONFLICT(profile_id,identity) DO UPDATE
            SET first_seen=excluded.first_seen, body=excluded.body''',
                   (profile_id, key, first, json.dumps(job, ensure_ascii=False)))
        saved_identities.update({key, *obsolete})
    db.executemany('''INSERT INTO search_runs (profile_id,day,source,query,total,kept,reason) VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(profile_id,day,source,query) DO UPDATE SET total=excluded.total,kept=excluded.kept,reason=excluded.reason''',
                   [(profile_id, day, source, query, run['total'],
                     sum(decisions[token] == KEPT_FOR_REVIEW for token in run['posting_ids']), run['reason'])
                    for query, run in runs.items()])
    return decisions


def refresh_search_facts(db, profile, today, previous, saved, employer_keys, winners, repeat_reason):
    for key, (_, old, _) in previous.items():
        search_job = old['provider'] == workable.SOURCE or old.get('discovery_only')
        if not search_job or key in saved or old.get('fact_reason') != KEPT_FOR_REVIEW:
            continue
        title_key = job_key(old['company'], old['title'])
        reason = posting_facts(profile, old, today, repeat_reason(old)
                               or (EMPLOYER_COPY if title_key in employer_keys else '')
                               or (SEARCH_COPY if title_key in winners else ''))
        if reason != KEPT_FOR_REVIEW:
            old['fact_reason'] = reason
            db.execute('UPDATE search_backlog SET body=? WHERE profile_id=? AND identity=?',
                       (json.dumps(old, ensure_ascii=False), profile['id'], key))


def lane_queries(profile):
    lanes = profile['goals']['lanes']
    return distinct(' '.join(phrase.split()) for field in ('titles', 'duties')
                    for group in zip_longest(*(lane[field] for lane in lanes), fillvalue='')
                    for phrase in group if len(phrase.strip()) <= 300)


def follow_discovery(profile, today, feeds, known_keys, prior_keys, repeat_reason,
                     fetch, search_fetch, backlog_connect):
    eligible = {}
    discovered = (lead for leads, _, _ in feeds.values() for lead in leads.values())
    for position, lead in enumerate(discovered):
        key = job_key(lead['company'], lead['title'])
        reason = (repeat_reason(lead) or exclusion_reason(profile, lead)
                  or (EMPLOYER_COPY if key in known_keys else '')
                  or (SEARCH_COPY if key in prior_keys else '') or lead.get('fact_reason'))
        if not reason and place_facts(profile, discovery.place_input(profile, lead))[0] != 'ok':
            reason = discovery.PLACE_UNCONFIRMED
        if reason:
            lead['fact_reason'] = reason
        else:
            title_match = any(titles for _, titles, _ in lane_matches(profile, lead['title'], ''))
            eligible.setdefault(key[0], []).append((not title_match, position, lead))
    ordered = {name: sorted(leads, key=lambda item: item[:2]) for name, leads in eligible.items()}
    groups = {name: [lead for _, _, lead in ordered[name]] for name in sorted(
        ordered, key=lambda name: (*ordered[name][0][:2], -len(ordered[name])))}
    matches, errors = discovery.confirm(profile, today, groups, boards, workable, fetch, search_fetch,
                                        backlog_connect, job_key)
    for leads, _, _ in feeds.values():
        for token, lead in leads.items():
            if lead.get('fact_reason'):
                continue
            key = job_key(lead['company'], lead['title'])
            if key in matches:
                leads[token] = dict(matches.pop(key), discovery_only=True, found_by=lead['found_by'],
                                    source_credit=lead['source_credit'], source_link=lead['source_link'],
                                    discovery_lead_identity=identity(lead))
                prior_keys.add(key)
            else:
                lead['fact_reason'] = SEARCH_COPY if key in prior_keys else errors.get(key[0], discovery.NOT_CONFIRMED)


def too_old(job, today):
    dates = [date.fromisoformat(value) for value in
             (job['posted_on'], job['first_seen'], job.get('workday_earliest_posted_on')) if value]
    return bool(dates) and min(dates) < today - timedelta(days=boards.SEARCH_WINDOW_DAYS)


def order(job):
    # Within a lane the card explicitly puts oldest first-seen after score.
    posted = date.fromisoformat(job['posted_on']).toordinal() if job['posted_on'] else 0
    return (-job['score'], job['first_seen'], -posted, identity(job))


def landing_set(profile, waiting):
    search = {**profiles.DEFAULTS, **profile['search']}
    limit, wild = search['daily_land_limit'], search['wild_cards']
    # Rule 1 (spec 06 ranking and landing rules, 2026-11-14)
    copies = {}
    for job in sorted(waiting, key=order):
        key = (job['company'], job_key('', job['title'])[1])
        copies.setdefault(key, job)
    ranked = list(copies.values())
    selected, seen, company_counts = [], set(), {}

    def take(jobs, count):
        taken = 0
        for job in jobs:
            key = identity(job)
            if taken >= count or len(selected) >= limit:
                break
            if key in seen or company_counts.get(job['company'], 0) >= search['max_per_company']:
                continue
            selected.append(job)
            seen.add(key)
            company_counts[job['company']] = company_counts.get(job['company'], 0) + 1
            taken += 1

    def fill(jobs):
        active = [lane['id'] for lane in profile['goals']['lanes']
                  if any(job['lane'] == lane['id'] for job in jobs)]
        start = len(selected)
        ordinary = max(0, limit - start - wild)
        if active:
            quota, remainder = divmod(ordinary, len(active))
            for index, lane in enumerate(active):
                take([job for job in jobs if job['lane'] == lane], quota + (index < remainder))
        # Reuse unfilled lane slots, then reserve the final wild slots for any lane.
        take(jobs, ordinary - (len(selected) - start))
        take(jobs, min(wild, limit - len(selected)))

    # Rule 4 (spec 06 ranking and landing rules, 2026-11-14)
    reachable, rest = [], []
    for job in ranked:
        (reachable if job['place'] == 'ok' or offers_help(job['description']) else rest).append(job)
    fill(reachable)
    fill(rest)
    selected.sort(key=order)
    for job in selected[:search['judge_budget']]:
        job['judge'] = True
    # Output order is chronological; selection and judge flags above use rank.
    return sorted(selected, key=lambda job: (job['first_seen'], job['posted_on'], identity(job)))


def connect(backlog):
    path = Path(backlog)
    path.parent.mkdir(parents=True, exist_ok=True)
    return closing(sqlite3.connect(path, timeout=10))


def mark_jobs(db, profile_id, jobs):
    db.executemany('UPDATE search_backlog SET landed=1 WHERE profile_id=? AND identity=?',
                   [(profile_id, identity(job)) for job in jobs])


def workday_key(company):
    return json.dumps([company.get(field) for field in ('host', 'board', 'site', 'label')])


def workday_progress(backlog, profile_id):
    with connect(backlog) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS workday_progress (
            profile_id TEXT NOT NULL, board_key TEXT NOT NULL, run_date TEXT NOT NULL,
            start_offset INTEGER NOT NULL, next_offset INTEGER NOT NULL,
            PRIMARY KEY (profile_id, board_key))''')
        return {key: (day, start, offset) for key, day, start, offset in db.execute(
            'SELECT board_key,run_date,start_offset,next_offset FROM workday_progress WHERE profile_id=?', (profile_id,))}


def save_workday_progress(db, updates, detail_updates, facet_updates):
    if updates:
        db.executemany('''INSERT INTO workday_progress VALUES (?,?,?,?,?)
            ON CONFLICT(profile_id, board_key) DO UPDATE SET run_date=excluded.run_date,
                start_offset=excluded.start_offset, next_offset=excluded.next_offset
            WHERE excluded.run_date >= workday_progress.run_date''', updates)
        db.executemany('''INSERT INTO workday_detail_progress VALUES (?,?,?,?,?,?)
            ON CONFLICT(profile_id, board_key) DO UPDATE SET run_date=excluded.run_date,
                started_with_details=excluded.started_with_details, resume_with_details=excluded.resume_with_details,
                failed_details=excluded.failed_details
            WHERE excluded.run_date >= workday_detail_progress.run_date''', detail_updates)
    if facet_updates:
        db.executemany('''INSERT INTO workday_country_facets VALUES (?,?,?,?,?)
            ON CONFLICT(profile_id, board_key) DO UPDATE SET countries=excluded.countries,
                facets=excluded.facets, run_date=excluded.run_date
            WHERE excluded.run_date >= workday_country_facets.run_date''', facet_updates)


def boards_read(result):
    # Workday can supply usable postings before a board stops. A partial Workable
    # read completed at least one search query, which can validly find no matches.
    if 'boards' in result:
        readings = result['boards']
        return any(board['read'] == 'complete' or board['read'] == 'partial'
                   and (board['rows'] or board.get('provider') in profiles.SEARCH_SOURCES)
                   for board in readings) or bool(readings) and all(
                       board['read'] == 'skipped' and board.get('search_only') for board in readings)
    return bool(result.get('landing'))


def run(profile, fetch, backlog, today, mark_landed=False, wait=2, *, search_fetch=None, tracker=None,
        discovery_fetch=None, review_queries=None, review_reads=None, jobicy_pages=None):
    """Fetch and rank all candidates; return a landing set without importing it."""
    if isinstance(today, str):
        today = date.fromisoformat(today)
    day = today.isoformat()
    counts = dict.fromkeys(('fetched', 'candidates', 'excluded', 'too_old', 'backlog_waiting', 'landing'), 0)
    errors, fetched = [], []

    def retry_fetch(method, url, body=None):
        nonlocal retried
        try:
            return fetch(method, url, body)
        except HTTPError as error:
            if error.code != 429 or retried:
                raise
            error.close()
            retried = True
            time.sleep(wait)
            return fetch(method, url, body)

    countries = profiles.workday_countries(profile)

    def home_first(text):
        return any(boards.phrase_matches(country, text) for country in countries.values())

    shadow = review_queries is not None
    companies = profile['search'].get('companies', [])
    sources = profile['search'].get('search_sources', [])
    if shadow:
        planned_queries = review_queries
        sources = list(planned_queries)
    use_workable = workable.SOURCE in sources
    discovery_order = sources if shadow else discovery.LABELS
    discovery_sources = [source for source in discovery_order if source in discovery.LABELS and source in sources]
    if shadow and not (companies or sources):
        return []
    if len(companies) > 300 or not (companies or use_workable or discovery_sources):
        raise ValueError('Choose a search source or between one and 300 job boards.')
    ordinary = [i for i, company in enumerate(companies) if company['provider'] != 'workday']
    workday = [i for i, company in enumerate(companies) if company['provider'] == 'workday']
    progress = workday_progress(backlog, profile['id']) if workday else {}
    known_workday_links = {}
    country_key = json.dumps(sorted(countries))
    with connect(backlog) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS search_backlog (profile_id TEXT NOT NULL, identity TEXT NOT NULL, first_seen TEXT NOT NULL, body TEXT NOT NULL, landed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (profile_id, identity))')
        db.execute('''CREATE TABLE IF NOT EXISTS search_runs (
            profile_id TEXT NOT NULL, day TEXT NOT NULL, source TEXT NOT NULL, query TEXT NOT NULL,
            total INTEGER, kept INTEGER NOT NULL, reason TEXT NOT NULL, PRIMARY KEY (profile_id,day,source,query))''')
        if workday:
            db.execute('''CREATE TABLE IF NOT EXISTS workday_country_facets (
                profile_id TEXT NOT NULL, board_key TEXT NOT NULL, countries TEXT NOT NULL,
                facets TEXT NOT NULL, run_date TEXT NOT NULL, PRIMARY KEY (profile_id, board_key))''')
            saved_facets = {key: (scope, json.loads(facets)) for key, scope, facets in db.execute(
                'SELECT board_key,countries,facets FROM workday_country_facets WHERE profile_id=?', (profile['id'],))}
            db.execute('''CREATE TABLE IF NOT EXISTS workday_detail_progress (
                profile_id TEXT NOT NULL, board_key TEXT NOT NULL, run_date TEXT NOT NULL,
                started_with_details INTEGER NOT NULL, resume_with_details INTEGER NOT NULL,
                failed_details TEXT NOT NULL,
                PRIMARY KEY (profile_id, board_key))''')
            detail_progress = {key: (started, resume, json.loads(failed)) for key, started, resume, failed in
                               db.execute('''SELECT board_key,started_with_details,resume_with_details,failed_details
                                   FROM workday_detail_progress WHERE profile_id=?''', (profile['id'],))}
            refresh_before = (today - timedelta(days=7)).isoformat()
            for first_seen, body, landed in db.execute(
                    'SELECT first_seen,body,landed FROM search_backlog WHERE profile_id=?', (profile['id'],)):
                job = json.loads(body)
                if not shadow and job['provider'] == 'workday':
                    cache_day = job.get('workday_details_read_on', first_seen)
                    expired = not landed and cache_day < refresh_before and not too_old(job, today)
                    known_workday_links.setdefault(job['company'], {})[job['link']] = job, expired
    updates, detail_updates, facet_updates = [], [], []
    policy = {**profiles.WORKDAY_DEFAULTS, **profile['search']}
    workday.sort(key=lambda i: progress.get(workday_key(companies[i]), ('', 0, 0))[0])
    board_reads = [None] * len(companies)
    shadow_boards = {i: [] for i in range(len(companies))}

    def attempts():
        for index in ordinary:
            yield index, None, False
        if not workday:
            return
        queue = boards.WorkdayQueue(time.monotonic() + policy['workday_run_budget_seconds'])

        def tasks(indices, worldwide=False):
            for index in indices:
                company = companies[index]
                key = workday_key(company)
                scope, facets = saved_facets.get(key, (country_key, None))
                prior_day, prior_start, prior_next = progress.get(key, ('', 0, 0))
                started, resume, failed_details = detail_progress.get(key, (False, False, {}))
                if worldwide or scope != country_key:
                    prior_day, prior_start, prior_next = '', 0, 0
                    facets = None
                # A same-day retry replays the segment so it can rebuild a lost snapshot.
                start = prior_next if prior_day < day else prior_start if prior_day == day else 0
                detail_first = started if prior_day == day else prior_day < day and resume
                known = known_workday_links.get(company['label'], {})

                def detail_match(title, text, company=company):
                    return (not exclusion_reason(profile, {'company': company['label'], 'title': title})
                            and boards.profile_posting_matches(profile, title, text))

                options = dict(home_first=home_first, start_offset=start,
                               page_pause=policy['workday_page_pause_seconds'],
                               board_budget=policy['workday_board_budget_seconds'],
                               known_links={link: None if expired else job.get('workday_list_fingerprint')
                                            for link, (job, expired) in known.items()},
                               detail_first=detail_first, failed_details=failed_details,
                               countries=None if worldwide else countries, saved_facets=facets, queue=queue)

                def read(deadline, company=company, detail_match=detail_match, options=options):
                    return boards.board_rows(company, fetch, detail_match, today, run_deadline=deadline, **options)

                yield index, company['host'], read

        reached, scoped = 0, set()
        for index, rows in queue.read(tasks(workday)):
            if isinstance(rows, boards.BoardRows) and rows.attempted:
                reached += 1
                if rows.country_scoped and rows.read == 'complete':
                    scoped.add(index)
            yield index, rows, False
        if reached == len(workday):
            for index, rows in queue.read(tasks([i for i in workday if i in scoped], worldwide=True)):
                if isinstance(rows, boards.BoardRows) and rows.attempted:
                    yield index, rows, True

    for index, rows, worldwide in attempts():
        company = companies[index]
        is_workday = company['provider'] == 'workday'
        retried = False
        reading = dict(provider=company['provider'], board=company['label'], read='failed',
                       reason='', rows=0, seen=[])
        if worldwide:
            board_reads[index]['worldwide'] = reading
        else:
            board_reads[index] = reading
        try:
            if is_workday:
                if isinstance(rows, Exception):
                    raise rows
                known = known_workday_links.get(company['label'], {})
                reading.update(read=rows.read, reason=rows.reason, rows=rows.rows_read,
                               page=rows.page, pages_read=rows.pages_read, http_status=rows.http_status,
                               full_read=rows.full_read, elapsed_seconds=rows.elapsed_seconds,
                               skipped_rows=rows.skipped_rows, skipped_details=rows.skipped_details)
            else:
                rows = boards.board_rows(company, retry_fetch, today=today, home_first=home_first)
                reading.update(read='complete', rows=len(rows))
                if company['provider'] == 'lever' and len(rows) >= 500:
                    reading.update(read='partial', reason=boards.read_reason(
                        'Lever', 'returned 500 or more rows and may have more', 200, 1, None, len(rows)))
            counts['fetched'] += len(rows)
            found = []
            for raw in rows:
                result = boards.normalize(company, raw, today.isoformat())
                if result:
                    cached = known[result['link']][0] if is_workday and raw.get('_details_cached') else None
                    if cached:
                        result.update(posting_id=cached['posting_id'], location=cached['place_text'],
                                      remote=cached['remote'], description=cached['description'])
                        if cached['date_source'] == 'workday':
                            result.update(postedOn=cached['posted_on'], dateSource=cached['date_source'])
                    reading['seen'].append([result['posting_id'], result['link'], result['role'], result['location']])
                    job = discovery.employer_job(result)
                    if shadow:
                        job['open'] = raw.get('isListed') is not False
                        job['board'] = company['board']
                    if is_workday:
                        if exclusion_reason(profile, job):
                            counts['excluded'] += 1
                            continue
                        if raw.get('_details_pending') or raw.get('_details_failed'):
                            continue
                        job['workday_country_scope'] = country_key if rows.country_scoped else None
                        job['workday_list_fingerprint'] = raw.get('_list_fingerprint')
                        if raw.get('_details_read'):
                            job['workday_details_read_on'] = day
                            known_workday_links.setdefault(company['label'], {})[job['link']] = job, False
                    found.append(job)
                else:
                    raw = raw if isinstance(raw, dict) else {}
                    posting_id = raw.get('posting_id') or raw.get('id')
                    link = boards.safe_link(raw.get('absolute_url') or raw.get('hostedUrl') or raw.get('jobUrl') or raw.get('_link'))
                    if posting_id or link:
                        reading['seen'].append([str(posting_id or ''), link, str(raw.get('title') or raw.get('text') or ''), ''])
                    elif not is_workday:
                        reading.update(read='partial', reason=reading['reason'] or boards.read_reason(
                            company['provider'].title(), 'returned a row with no posting id or link', 200,
                            reading.get('page', 1), None, reading['rows']))
            fetched.extend(found)
            if shadow:
                shadow_boards[index].extend(found)
            if is_workday and rows.attempted and not worldwide:
                key = workday_key(company)
                updates.append((profile['id'], key, day, rows.start_offset, rows.next_offset))
                detail_updates.append((profile['id'], key, day, rows.started_with_details, rows.detail_first,
                                       json.dumps(rows.failed_details)))
                if rows.saved_facets or key in saved_facets:
                    facet_updates.append((profile['id'], key, country_key, json.dumps(rows.saved_facets), day))
        except Exception as error:
            problem = boards.board_failure(error)
            reading.update(read='failed', http_status=problem.status, reason=boards.read_reason(
                company['provider'].title() if not is_workday else 'Workday', problem.explanation,
                problem.status, reading.get('page', 0), None, reading['rows']))
        if reading['read'] != 'complete' and not worldwide:
            errors.append({'company': company['label'], 'message': reading['reason']})

    feeds = {}
    search_enabled = use_workable or bool(discovery_sources)
    tracker_titles, tracker_links = tracker_repeats(tracker if search_enabled and not shadow else None, profile['id'])
    employer_keys = {job_key(job['company'], job['title']) for job in fetched}
    prior_keys, landed_keys = set(), set()

    def repeat_reason(job):
        key = job_key(job['company'], job['title'])
        title_reason, link_reason = tracker_titles.get(key), tracker_links.get(job['link'])
        if link_reason in (SKIPPED, ARCHIVED):
            return link_reason
        return title_reason or link_reason or (ON_TRACKER if key in landed_keys else '')

    if search_enabled and not shadow:
        with connect(backlog) as db:
            for body, landed in db.execute('SELECT body,landed FROM search_backlog WHERE profile_id=?', (profile['id'],)):
                job = json.loads(body)
                key = job_key(job['company'], job['title'])
                if landed:
                    landed_keys.add(key)
                if (discovery_sources and job.get('fact_reason') == KEPT_FOR_REVIEW and not job.get('gone')
                        and posting_facts(profile, dict(job, open=True), today) == KEPT_FOR_REVIEW):
                    prior_keys.add(key)
        queries = lane_queries(profile)

    search_jobs = []
    if use_workable:
        def skip_detail(job):
            if shadow:
                return exclusion_reason(profile, job)
            return (repeat_reason(job)
                    or (EMPLOYER_COPY if job_key(job['company'], job['title']) in employer_keys else '')
                    or exclusion_reason(profile, job))

        search_jobs, search_runs, reading = workable.read(
            profile, planned_queries[workable.SOURCE] if shadow else queries, today, boards,
            search_fetch or partial(workable.fetch_text, boards=boards), skip_detail)
        feeds[workable.SOURCE] = ({job['posting_id']: job for job in search_jobs},
                                  {run['query']: run for run in search_runs}, reading)

    if discovery_sources:
        for source in discovery_sources:
            feeds[source] = discovery.read(source, profile, planned_queries[source] if shadow else queries,
                                          countries.values(), boards,
                                          discovery_fetch or partial(discovery.fetch_json, boards=boards),
                                          partial(connect, backlog), shadow=shadow, jobicy_pages=jobicy_pages)
        workable_keys = {job_key(job['company'], job['title']) for job in search_jobs
                         if job['open'] is True and not job.get('fact_reason')}
        follow_discovery(profile, today, {source: feeds[source] for source in discovery_sources},
                         set() if shadow else employer_keys | workable_keys, prior_keys, repeat_reason,
                         fetch, search_fetch, partial(connect, backlog))
    if shadow:
        if updates or facet_updates:
            with connect(backlog) as db, db:
                save_workday_progress(db, updates, detail_updates, facet_updates)
        results = []
        if review_reads is not None:
            review_reads.extend(row for row in board_reads if row)
        for source in sources:
            jobs, runs, reading = feeds[source]
            results.append((source, '', list(jobs.values()), reading['rows'], reading['reason']))
        results.extend((company['provider'], company['board'], shadow_boards[i], (board_reads[i] or {}).get('rows', 0),
                        (board_reads[i] or {}).get('reason', 'This board could not be read.'))
                       for i, company in enumerate(companies))
        return results
    if search_enabled:
        tracker_titles, tracker_links = tracker_repeats(tracker, profile['id'])

    with connect(backlog) as db, db:
        db.execute('BEGIN IMMEDIATE')
        previous = {key: (first_seen, json.loads(body), landed) for key, first_seen, body, landed in
                    db.execute('SELECT identity,first_seen,body,landed FROM search_backlog WHERE profile_id=?', (profile['id'],))}
        landed_keys = {job_key(job['company'], job['title']) for _, job, landed in previous.values() if landed}

        fact_winners = set()
        # A listing-only read can use the list ID while a detail read uses a
        # requisition ID. Retain the established identity for the same link.
        workday_links = {}
        for _, job, landed in previous.values():
            if job['provider'] == 'workday':
                key = (job['company'], job['link'])
                if landed or key not in workday_links:
                    workday_links[key] = job['posting_id']
        seen = set()
        for job in fetched:
            if job['provider'] == 'workday' and identity(job) not in previous:
                job['posting_id'] = workday_links.get((job['company'], job['link']), job['posting_id'])
            key = identity(job)
            if key in seen:
                continue
            seen.add(key)
            old = previous.get(key)
            if old and old[1].get('discovery_only'):
                old = None  # A configured board creates its own ordinary backlog entry.
            fresh_details = 'workday_details_read_on' in job
            if old:
                job = {**old[1], **job}
            job['first_seen'] = old[0] if old else today.isoformat()
            if old:
                job['gone'] = False
                job['missed_days'] = []
                job.pop('missed_at', None)
                if not fresh_details and len(old[1].get('description', '')) > len(job['description']):
                    job['description'] = old[1]['description']
            # A refreshed publication date must not make an old posting young again.
            if old and old[1]['posted_on']:
                if fresh_details:
                    job['workday_earliest_posted_on'] = min(
                        old[1]['posted_on'], old[1].get('workday_earliest_posted_on', old[1]['posted_on']))
                elif not job['posted_on'] or old[1]['posted_on'] < job['posted_on']:
                    job['posted_on'] = old[1]['posted_on']
                    job['date_source'] = old[1]['date_source']
            if job.get('gone'):
                continue
            title_key = job_key(job['company'], job['title'])
            repeat = repeat_reason(job)
            if title_key in fact_winners and not repeat:
                repeat = 'Another employer board already has this posting.'
            job['fact_reason'] = posting_facts(profile, dict(job, open=True), today, repeat)
            if job['fact_reason'] == KEPT_FOR_REVIEW:
                fact_winners.add(title_key)
            ranked = None
            if exclusion_reason(profile, job):
                counts['excluded'] += 1
            elif too_old(job, today):
                counts['too_old'] += 1
            else:
                ranked = rank(profile, job)
                if ranked:
                    counts['candidates'] += 1
            # Keep successful details even when their exact date rules a job out.
            # A replay can then move on to other matches instead of fetching them again.
            if old or fresh_details or ranked:
                db.execute('''INSERT INTO search_backlog (profile_id,identity,first_seen,body,landed)
                    VALUES (?,?,?,?,0) ON CONFLICT(profile_id,identity) DO UPDATE
                    SET first_seen=excluded.first_seen, body=excluded.body''',
                           (profile['id'], key, job['first_seen'], json.dumps(ranked or job, ensure_ascii=False)))
        saved, winners = set(seen), set()
        for source, (jobs, runs, reading) in feeds.items():
            decisions = save_search(db, profile, today, source, jobs, runs, previous,
                                    employer_keys, repeat_reason, saved)
            reading['kept'] = sum(reason == KEPT_FOR_REVIEW for reason in decisions.values())
            if source in discovery.LABELS:
                reading['hidden'] = dict(Counter(reason for reason in decisions.values() if reason != KEPT_FOR_REVIEW))
            winners.update(job_key(job['company'], job['title']) for job in jobs.values()
                           if job['fact_reason'] == KEPT_FOR_REVIEW)
            board_reads.append(reading)
        refresh_search_facts(db, profile, today, previous, saved, employer_keys, winners, repeat_reason)
        waiting = []
        for (body,) in db.execute('SELECT body FROM search_backlog WHERE profile_id=? AND landed=0', (profile['id'],)).fetchall():
            job = json.loads(body)
            if (job['provider'] == workable.SOURCE or job.get('discovery_only') or job.get('gone') or too_old(job, today)
                    or exclusion_reason(profile, job) or repeat_reason(job) in (SKIPPED, ARCHIVED)):
                continue
            ranked = rank(profile, job)
            if ranked:
                waiting.append(ranked)
                db.execute('UPDATE search_backlog SET body=? WHERE profile_id=? AND identity=?',
                           (json.dumps(ranked, ensure_ascii=False), profile['id'], identity(job)))
        landing = landing_set(profile, [job for job in waiting if identity(job) in seen])
        for job in landing:
            job.pop('fact_reason', None)
            job.pop('workday_country_scope', None)
            job.pop('workday_list_fingerprint', None)
            job.pop('workday_details_read_on', None)
            job.pop('workday_earliest_posted_on', None)
        if mark_landed:
            mark_jobs(db, profile['id'], landing)
        counts['backlog_waiting'] = len(waiting) - (len(landing) if mark_landed else 0)
        counts['landing'] = len(landing)
        save_workday_progress(db, updates, detail_updates, facet_updates)
    workday_read = sum(board_reads[i]['read'] == 'complete' for i in workday)
    return {'metadata': {'date': day, 'read_at': datetime.now().isoformat(timespec='minutes'), 'profile_id': profile['id'],
                         'profile_revision': profile['revision'], 'counts': counts,
                         'workday': {'read': workday_read, 'total': len(workday),
                                     'continue_tomorrow': len(workday) - workday_read}},
            'landing': landing, 'source_errors': errors, 'boards': board_reads}


def main():
    parser = argparse.ArgumentParser(description='Search public job boards using a profile and save a new daily JSON snapshot.')
    parser.add_argument('--profile', type=Path, default=profiles.default_path())
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backlog', type=Path, default=settings.data_root() / 'work' / 'search-backlog.sqlite3')
    parser.add_argument('--mark-landed', action='store_true', help='Mark the selected jobs landed after the snapshot is saved.')
    args = parser.parse_args()
    if not args.profile.is_file():
        parser.exit(1, 'The profile file is missing. Create profile.json in the data folder or use --profile.\n')
    if args.output.suffix.lower() != '.json':
        parser.error('--output must be a .json snapshot path.')
    # Reserve before reading the profile, opening the backlog or using the network.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.open('x', encoding='utf-8') as stream:
            try:
                profile = profiles.load(args.profile)
                result = run(profile, boards.fetch_json, args.backlog, date.today(),
                             tracker=args.profile.parent / 'work' / 'applications-tracker.sqlite3')
                if args.mark_landed:
                    result['metadata']['counts']['backlog_waiting'] -= len(result['landing'])
                stream.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
            except Exception:
                stream.close()
                args.output.unlink()
                raise
        if args.mark_landed:
            with connect(args.backlog) as db, db:
                mark_jobs(db, profile['id'], result['landing'])
    except (OSError, ValueError, sqlite3.Error) as error:
        if isinstance(error, profiles.ProfileError):
            parser.exit(1, str(error) + '\n')
        parser.exit(1, 'The daily search could not save its results. Check the file paths and profile, and try again.\n')
    print(f"Saved {len(result['landing'])} jobs; {len(result['source_errors'])} job boards could not be checked.")
    return 0 if boards_read(result) else 1


if __name__ == '__main__':
    raise SystemExit(main())
