"""Profile-driven public job discovery and a durable, deterministic landing queue."""
import argparse
from contextlib import closing
from datetime import date, datetime, timedelta
import importlib.util
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


def distinct(phrases):
    result, seen = [], set()
    for phrase in phrases:
        key = tuple(re.findall(r'\w+', phrase.casefold()))
        if key not in seen:
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
        key = (job['company'], tuple(re.findall(r'\w+', job['title'].casefold())))
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


def boards_read(result):
    # Workday can supply usable postings before a board stops.
    if 'boards' in result:
        return any(board['read'] == 'complete' or board['read'] == 'partial' and board['rows']
                   for board in result['boards'])
    return bool(result.get('landing'))


def run(profile, fetch, backlog, today, mark_landed=False, wait=2):
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

    # Card 06-1d: home-country reads first, place from the job page
    def home_first(text):
        rules = profile['work_rules']
        return any(boards.phrase_matches(country, text)
                   for country in [rules['home_country'], *rules['authorized_countries']])

    companies = profile['search']['companies']
    if not 1 <= len(companies) <= 300:
        raise ValueError('Choose between one and 300 job-board sources.')
    ordinary = [i for i, company in enumerate(companies) if company['provider'] != 'workday']
    workday = [i for i, company in enumerate(companies) if company['provider'] == 'workday']
    progress = workday_progress(backlog, profile['id']) if workday else {}
    known_workday_links = {}
    with connect(backlog) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS search_backlog (profile_id TEXT NOT NULL, identity TEXT NOT NULL, first_seen TEXT NOT NULL, body TEXT NOT NULL, landed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (profile_id, identity))')
        if workday:
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
                if job['provider'] == 'workday':
                    cache_day = job.get('workday_details_read_on', first_seen)
                    expired = not landed and cache_day < refresh_before and not too_old(job, today)
                    known_workday_links.setdefault(job['company'], {})[job['link']] = job, expired
    updates, detail_updates, run_deadline = [], [], None
    policy = {**profiles.WORKDAY_DEFAULTS, **profile['search']}
    workday.sort(key=lambda i: progress.get(workday_key(companies[i]), ('', 0, 0))[0])
    board_reads = [None] * len(companies)
    for index in ordinary + workday:
        company = companies[index]
        is_workday = company['provider'] == 'workday'
        retried = False
        reading = dict(provider=company['provider'], board=company['label'], read='failed',
                       reason='', rows=0, seen=[])
        board_reads[index] = reading
        try:
            if is_workday:
                known = known_workday_links.get(company['label'], {})

                def detail_match(title, text):
                    return (not exclusion_reason(profile, {'company': company['label'], 'title': title})
                            and boards.profile_posting_matches(profile, title, text))

                if run_deadline is None:
                    run_deadline = time.monotonic() + policy['workday_run_budget_seconds']
                key = workday_key(company)
                prior_day, prior_start, prior_next = progress.get(key, ('', 0, 0))
                # A same-day retry replays the segment so it can rebuild a lost snapshot.
                start = prior_next if prior_day < day else prior_start if prior_day == day else 0
                started_with_details, resume_with_details, failed_details = detail_progress.get(key, (False, False, {}))
                detail_first = started_with_details if prior_day == day else prior_day < day and resume_with_details
                rows = boards.board_rows(company, fetch, detail_match, today, home_first=home_first,
                                         start_offset=start, page_pause=policy['workday_page_pause_seconds'],
                                         board_budget=policy['workday_board_budget_seconds'], run_deadline=run_deadline,
                                         known_links={link: None if expired else job.get('workday_list_fingerprint')
                                                      for link, (job, expired) in known.items()},
                                         detail_first=detail_first,
                                         failed_details=failed_details)
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
                    job = {'provider': result['provider'], 'company': result['company'],
                           'posting_id': result['posting_id'], 'title': result['role'],
                           'place_text': result['location'], 'remote': result['remote'],
                           'posted_on': result['postedOn'], 'date_source': result['dateSource'],
                           'link': result['link'], 'description': result['description']}
                    if is_workday:
                        if exclusion_reason(profile, job):
                            counts['excluded'] += 1
                            continue
                        if raw.get('_details_pending') or raw.get('_details_failed'):
                            continue
                        job['workday_list_fingerprint'] = raw.get('_list_fingerprint')
                        if raw.get('_details_read'):
                            job['workday_details_read_on'] = day
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
            if is_workday and rows.attempted:
                updates.append((profile['id'], key, day, start, rows.next_offset))
                detail_updates.append((profile['id'], key, day, detail_first, rows.detail_first,
                                       json.dumps(rows.failed_details)))
        except Exception as error:
            problem = boards.board_failure(error)
            reading.update(read='failed', http_status=problem.status, reason=boards.read_reason(
                company['provider'].title() if not is_workday else 'Workday', problem.explanation,
                problem.status, reading.get('page', 0), None, reading['rows']))
        if reading['read'] != 'complete':
            errors.append({'company': company['label'], 'message': reading['reason']})

    with connect(backlog) as db, db:
        db.execute('BEGIN IMMEDIATE')
        previous = {key: (first_seen, json.loads(body), landed) for key, first_seen, body, landed in
                    db.execute('SELECT identity,first_seen,body,landed FROM search_backlog WHERE profile_id=?', (profile['id'],))}
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
                    VALUES (?,?,?,?,0) ON CONFLICT(profile_id,identity) DO UPDATE SET body=excluded.body''',
                           (profile['id'], key, job['first_seen'], json.dumps(ranked or job, ensure_ascii=False)))
        waiting = []
        for (body,) in db.execute('SELECT body FROM search_backlog WHERE profile_id=? AND landed=0', (profile['id'],)).fetchall():
            job = json.loads(body)
            if job.get('gone') or too_old(job, today) or exclusion_reason(profile, job):
                continue
            ranked = rank(profile, job)
            if ranked:
                waiting.append(ranked)
                db.execute('UPDATE search_backlog SET body=? WHERE profile_id=? AND identity=?',
                           (json.dumps(ranked, ensure_ascii=False), profile['id'], identity(job)))
        landing = landing_set(profile, [job for job in waiting if identity(job) in seen])
        for job in landing:
            job.pop('workday_list_fingerprint', None)
            job.pop('workday_details_read_on', None)
            job.pop('workday_earliest_posted_on', None)
        if mark_landed:
            mark_jobs(db, profile['id'], landing)
        counts['backlog_waiting'] = len(waiting) - (len(landing) if mark_landed else 0)
        counts['landing'] = len(landing)
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
                result = run(profile, boards.fetch_json, args.backlog, date.today())
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
