"""Profile-driven public job discovery and a durable, deterministic landing queue."""
import argparse
from contextlib import closing
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import re
import sqlite3


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
        for sentence in [location, *sentences]:
            if any(boards.phrase_matches(region, sentence) for region in [*rules['remote_regions'], 'worldwide', 'anywhere']):
                return 'ok', sentence
    return 'check', location


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
    return dict(job, lane=lane, score=(6 if titles else 0) + min(10, 2 * len(duties)) +
                {'ok': 3, 'check': 0, 'out': -10}[place] + penalty,
                matched_titles=titles, matched_duties=duties, place=place,
                place_sentence=sentence, fit='pending', judge=False)


def identity(job):
    return json.dumps([job['provider'], job['company'], job['posting_id']], ensure_ascii=False)


def too_old(job, today):
    dates = [date.fromisoformat(value) for value in (job['posted_on'], job['first_seen']) if value]
    return bool(dates) and min(dates) < today - timedelta(days=45)


def order(job):
    # Within a lane the card explicitly puts oldest first-seen after score.
    posted = date.fromisoformat(job['posted_on']).toordinal() if job['posted_on'] else 0
    return (-job['score'], job['first_seen'], -posted, identity(job))


def landing_set(profile, waiting):
    search = {**profiles.DEFAULTS, **profile['search']}
    limit, wild = search['daily_land_limit'], search['wild_cards']
    ranked = sorted(waiting, key=order)
    active = [lane['id'] for lane in profile['goals']['lanes']
              if any(job['lane'] == lane['id'] for job in ranked)]
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

    ordinary = max(0, limit - wild)
    if active:
        quota, remainder = divmod(ordinary, len(active))
        for index, lane in enumerate(active):
            take([job for job in ranked if job['lane'] == lane], quota + (index < remainder))
    # Reuse unfilled lane slots, then reserve the final wild slots for any lane.
    take(ranked, ordinary - len(selected))
    take(ranked, min(wild, limit - len(selected)))
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


def run(profile, fetch, backlog, today, mark_landed=False):
    """Fetch and rank all candidates; return a landing set without importing it."""
    if isinstance(today, str):
        today = date.fromisoformat(today)
    counts = dict.fromkeys(('fetched', 'candidates', 'excluded', 'too_old', 'backlog_waiting', 'landing'), 0)
    errors, fetched = [], []

    def detail_match(title, text):
        return boards.profile_posting_matches(profile, title, text)

    companies = profile['search']['companies']
    if not 1 <= len(companies) <= 300:
        raise ValueError('Choose between one and 300 job-board sources.')
    for company in companies:
        try:
            rows = boards.board_rows(company, fetch, detail_match, today)
            counts['fetched'] += len(rows)
            found = []
            for raw in rows:
                result = boards.normalize(company, raw, today.isoformat())
                if result:
                    found.append({'provider': result['provider'], 'company': result['company'],
                                  'posting_id': result['posting_id'], 'title': result['role'],
                                  'place_text': result['location'], 'remote': result['remote'],
                                  'posted_on': result['postedOn'], 'date_source': result['dateSource'],
                                  'link': result['link'], 'description': result['description']})
            fetched.extend(found)
        except Exception:
            errors.append({'company': company['label'], 'message': 'This job board could not be checked. It may be unavailable or its format may have changed.'})

    with connect(backlog) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS search_backlog (profile_id TEXT NOT NULL, identity TEXT NOT NULL, first_seen TEXT NOT NULL, body TEXT NOT NULL, landed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (profile_id, identity))')
        db.execute('BEGIN IMMEDIATE')
        previous = {key: (first_seen, json.loads(body), landed) for key, first_seen, body, landed in
                    db.execute('SELECT identity,first_seen,body,landed FROM search_backlog WHERE profile_id=?', (profile['id'],))}
        seen = set()
        for job in fetched:
            key = identity(job)
            if key in seen:
                continue
            seen.add(key)
            old = previous.get(key)
            job['first_seen'] = old[0] if old else today.isoformat()
            # A refreshed publication date must not make an old posting young again.
            if old and old[1]['posted_on'] and (not job['posted_on'] or old[1]['posted_on'] < job['posted_on']):
                job['posted_on'] = old[1]['posted_on']
                job['date_source'] = old[1]['date_source']
            if old:
                # Retain the latest facts even if a changed posting is now excluded
                # or no longer matches; its earlier snapshot must not land instead.
                db.execute('UPDATE search_backlog SET body=? WHERE profile_id=? AND identity=?',
                           (json.dumps({**old[1], **job}, ensure_ascii=False), profile['id'], key))
            if exclusion_reason(profile, job):
                counts['excluded'] += 1
                continue
            if too_old(job, today):
                counts['too_old'] += 1
                continue
            ranked = rank(profile, job)
            if ranked is None:
                continue
            counts['candidates'] += 1
            db.execute('INSERT INTO search_backlog (profile_id,identity,first_seen,body,landed) VALUES (?,?,?,?,0) ON CONFLICT(profile_id,identity) DO UPDATE SET body=excluded.body',
                       (profile['id'], key, job['first_seen'], json.dumps(ranked, ensure_ascii=False)))
        waiting = []
        for (body,) in db.execute('SELECT body FROM search_backlog WHERE profile_id=? AND landed=0', (profile['id'],)).fetchall():
            job = json.loads(body)
            if too_old(job, today) or exclusion_reason(profile, job):
                continue
            ranked = rank(profile, job)
            if ranked:
                waiting.append(ranked)
                db.execute('UPDATE search_backlog SET body=? WHERE profile_id=? AND identity=?',
                           (json.dumps(ranked, ensure_ascii=False), profile['id'], identity(job)))
        landing = landing_set(profile, [job for job in waiting if identity(job) in seen])
        if mark_landed:
            mark_jobs(db, profile['id'], landing)
        counts['backlog_waiting'] = len(waiting) - (len(landing) if mark_landed else 0)
        counts['landing'] = len(landing)
    return {'metadata': {'date': today.isoformat(), 'profile_id': profile['id'],
                         'profile_revision': profile['revision'], 'counts': counts},
            'landing': landing, 'source_errors': errors}


def main():
    parser = argparse.ArgumentParser(description='Search public job boards using a profile and save a new daily JSON snapshot.')
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backlog', type=Path, default=settings.data_root() / 'work' / 'search-backlog.sqlite3')
    parser.add_argument('--mark-landed', action='store_true', help='Mark the selected jobs landed after the snapshot is saved.')
    args = parser.parse_args()
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
    return 0 if len(result['source_errors']) < len(profile['search']['companies']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
