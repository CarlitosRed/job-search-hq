"""Land a public daily-search snapshot in the SQLite tracker."""
import argparse
from contextlib import closing
from datetime import date
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
from urllib.parse import urlsplit


def load_component(name, folder):
    spec = importlib.util.spec_from_file_location('landing_' + name, folder / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).resolve().parent
store = load_component('store', HERE)
daily_search = load_component('daily_search', HERE.parent / 'hq-dashboard')
settings = daily_search.settings
freshness = store.freshness
LANDING_SHEET = 'Daily search'
HEADER = ['#', 'Company', 'Role', 'Where', 'Posted', 'Score', 'Verdict', 'My status',
          'Date applied', 'CV used', 'Source', 'Link', 'Notes', 'Spot-check', 'Agent notes']


def validate(snapshot, today):
    """Check the complete snapshot before opening any writable storage."""
    def fail(message):
        raise ValueError('Invalid daily-search snapshot: ' + message)

    def controls(value):
        if isinstance(value, str) and any(unicodedata.category(c) == 'Cc' for c in value):
            fail('text contains control characters.')
        if isinstance(value, dict):
            for key, item in value.items():
                controls(key)
                controls(item)
        if isinstance(value, list):
            for item in value:
                controls(item)

    def text(value, name, empty=False):
        if not isinstance(value, str) or (not empty and not value.strip()):
            fail(name + ' must be text' + ('.' if empty else ' and not empty.'))

    def day(value, name, empty=False):
        if empty and value == '':
            return
        text(value, name)
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) or date.fromisoformat(value) > today:
                raise ValueError()
        except ValueError:
            fail(name + ' must be a real YYYY-MM-DD date, not in the future.')

    controls(snapshot)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('metadata'), dict):
        fail('metadata must be an object.')
    meta = snapshot['metadata']
    day(meta.get('date'), 'metadata.date')
    text(meta.get('profile_id'), 'metadata.profile_id')
    if type(meta.get('profile_revision')) is not int or meta['profile_revision'] < 1:
        fail('profile_revision must be a positive whole number.')
    if not isinstance(snapshot.get('landing'), list):
        fail('landing must be a list.')
    for job in snapshot['landing']:
        if not isinstance(job, dict):
            fail('each landing job must be an object.')
        for key in ('provider', 'company', 'posting_id', 'title', 'lane', 'link'):
            text(job.get(key), key)
        for key in ('place_text', 'description', 'place_sentence', 'date_source'):
            text(job.get(key), key, empty=True)
        if job['provider'] not in daily_search.profiles.PROVIDERS:
            fail('unknown provider.')
        if job['date_source'] not in ('', *daily_search.profiles.PROVIDERS, 'workday-about'):
            fail('unknown date_source.')
        for key in ('remote', 'judge'):
            if type(job.get(key)) is not bool:
                fail(key + ' must be true or false.')
        day(job.get('posted_on'), 'posted_on', empty=True)
        day(job.get('first_seen'), 'first_seen')
        try:
            link = urlsplit(job['link'])
            if link.scheme not in ('http', 'https') or not link.hostname or any(c.isspace() for c in job['link']):
                fail('link must be an http(s) URL.')
        except ValueError:
            fail('link must be an http(s) URL.')
        if type(job.get('score')) is not int:
            fail('score must be a whole number.')
        for key in ('matched_titles', 'matched_duties'):
            if not isinstance(job.get(key), list):
                fail(key + ' must be a list.')
            for phrase in job[key]:
                text(phrase, key)
        if job.get('place') not in ('ok', 'check', 'out') or job.get('fit') != 'pending':
            fail('place must be ok, check or out and fit must be pending.')


def row_values(job, meta, number):
    posted = 'Unknown'
    if job['posted_on']:
        provider = {'greenhouse': 'Greenhouse', 'lever': 'Lever', 'ashby': 'Ashby', 'workday': 'Workday'}
        label = provider.get(job['date_source'], provider[job['provider']])
        posted = ('about ' if job['date_source'] == 'workday-about' else '') + job['posted_on'] + f' ({label})'
    found = date.fromisoformat(job['first_seen'])
    notes = (f'Status: Availability needs checking (found {found.day} {found:%b %Y}). You have not applied.\n'
             'Place: ' +
             {'ok': 'OK.', 'check': 'Check availability and eligibility.',
              'out': 'Out of the stated work rules.'}[job['place']])
    agent = (f"Daily search {meta['date']}, profile {meta['profile_id']} rev {meta['profile_revision']}; "
             f"lane {job['lane']}; score {job['score']}; titles {', '.join(job['matched_titles'])}; "
             f"duties {', '.join(job['matched_duties'])}; place {job['place']}: {job['place_sentence']}; "
             f"posting {job['provider']}/{job['posting_id']}; judge {str(job['judge']).lower()}.")
    return [number, job['company'], job['title'], job['place_text'], posted, None, None,
            store.stages.NEW_ROW, None, None, 'Daily search ' + meta['date'], job['link'], notes,
            'Not independently spot-checked', agent]


def land_jobs(storage, snapshot, today):
    db, meta = storage.db, snapshot['metadata']
    report = dict(ok=True, **dict.fromkeys(('added', 'merged', 'known', 'skipped_duplicates',
                                         'skipped_archived', 'skipped_old', 'marked_landed'), 0))
    live = {freshness.job_key(r['company'], r['role'], r['place']): r['id']
            for r in db.execute('SELECT id, company, role, place FROM jobs ORDER BY id DESC')}
    archived = {freshness.job_key(*r) for r in db.execute('SELECT company, role, place FROM old_jobs')}
    links = {r[0] for r in db.execute('SELECT link FROM jobs')}
    landed, seen = [], set()
    for job in snapshot['landing']:
        identity = (meta['profile_id'], job['provider'], job['company'], job['posting_id'])
        key = freshness.job_key(job['company'], job['title'], job['place_text'])
        if storage.job_for_posting(*identity) is not None:
            # Card 03: closing and re-check.
            db.execute('UPDATE postings SET title=?,place=? WHERE profile_id=? AND provider=? AND board=? AND posting_id=?',
                       (job['title'], job['place_text'], *identity))
            report['known'] += 1
            landed.append(job)
            seen.add(job['link'])
            continue
        if not freshness.in_window(freshness.age_days(job['posted_on'], job['first_seen'], today)):
            report['skipped_old'] += 1
        elif key in live:
            storage.link_posting(live[key], *identity, link=job['link'], seen=job['first_seen'], source='search', title=job['title'], place=job['place_text'])
            report['merged'] += 1
            landed.append(job)
        elif key in archived:
            report['skipped_archived'] += 1
        elif job['link'] in links or job['link'] in seen:
            report['skipped_duplicates'] += 1
        else:
            layout = db.execute("SELECT * FROM layouts WHERE source='main' AND sheet=?", (LANDING_SHEET,)).fetchone()
            if layout is None:
                position = db.execute("SELECT COALESCE(MAX(position), -1)+1 FROM layouts WHERE source='main'").fetchone()[0]
                widths = {chr(65 + i): width for i, width in enumerate(
                    (16, 24, 44, 28, 26, 10, 18, 18, 16, 24, 28, 48, 70, 32, 70))}
                db.execute('INSERT INTO layouts VALUES (?,?,?,?,?,?,?,?)',
                           ('main', LANDING_SHEET, position, store.encode(HEADER), json.dumps(widths), 'A2', 1, 1))
                head, last_row = HEADER, 1
            else:
                head, last_row = store.decode(layout['header']), layout['max_row']
            next_row = max(last_row, db.execute('SELECT COALESCE(MAX(row), 1) FROM jobs WHERE sheet=?',
                                               (LANDING_SHEET,)).fetchone()[0]) + 1
            prefix = 'S' + date.fromisoformat(meta['date']).strftime('%m%d') + '-'
            numbers = [store.decode(r[0])[0] for table in ('jobs', 'old_jobs')
                       for r in db.execute(f'SELECT fields FROM {table} WHERE fields IS NOT NULL')]
            sequence = max([int(n[len(prefix):]) for n in numbers if isinstance(n, str) and
                            n.startswith(prefix) and n[len(prefix):].isdigit()], default=0) + 1
            values = dict(zip(HEADER, row_values(job, meta, prefix + f'{sequence:02d}')))
            storage.put_row('jobs', LANDING_SHEET, next_row, head, [values.get(h) for h in head], history=True)
            job_id = db.execute('SELECT id FROM jobs WHERE sheet=? AND row=?', (LANDING_SHEET, next_row)).fetchone()[0]
            db.execute('UPDATE jobs SET first_seen=?, posted_on=?, date_source=? WHERE id=?',
                       (job['first_seen'], job['posted_on'] or None, job['date_source'], job_id))
            db.execute("UPDATE layouts SET max_row=? WHERE source='main' AND sheet=?", (next_row, LANDING_SHEET))
            storage.link_posting(job_id, *identity, link=job['link'], seen=job['first_seen'], source='search', title=job['title'], place=job['place_text'])
            live[key] = job_id
            links.add(job['link'])
            report['added'] += 1
            landed.append(job)
        seen.add(job['link'])
    if report['added'] or report['merged']:
        storage.set_meta('revision', int(storage.meta('revision')) + 1)
        storage.set_meta('mirror_stale', 1)
    report['marked_landed'] = len(landed)
    return report, landed


def land(xlsx, snapshot_path, backlog_path, apply=False, today=None):
    today = date.fromisoformat(today) if isinstance(today, str) else today or date.today()
    snapshot = json.loads(Path(snapshot_path).read_text(encoding='utf-8'))
    validate(snapshot, today)
    xlsx = Path(xlsx)
    target = store.db_path(xlsx)
    if not target.is_file():
        return dict(ok=False, message=f'The tracker database {target.name} does not exist. Migrate the workbook first.',
                    **dict.fromkeys(('added', 'merged', 'known', 'skipped_duplicates', 'skipped_archived',
                                     'skipped_old', 'marked_landed'), 0))
    with closing(sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True)) as source:
        if apply:
            folder = target.parent / '.tracker-backups'
            folder.mkdir(exist_ok=True)
            backup = folder / f'{target.stem}-{date.today().isoformat()}.sqlite3'
            if not backup.exists():
                with closing(sqlite3.connect(backup)) as saved:
                    source.backup(saved)
            for old in sorted(folder.glob(f'{target.stem}-????-??-??.sqlite3'))[:-7]:
                old.unlink()
        storage = store.Store(target if apply else ':memory:')
        try:
            if not apply:
                source.backup(storage.db)
                with storage.db:
                    for _, _, step in store.migrations.STEPS:
                        step(storage.db)
            with storage.db:
                storage.db.execute('BEGIN IMMEDIATE')
                report, landed = land_jobs(storage, snapshot, today)
        finally:
            storage.close()
    if apply:
        store.rebuild_mirror(xlsx)
        # Marking is retryable: the committed posting identity is checked first on reruns.
        if landed:
            with closing(sqlite3.connect(Path(backlog_path).resolve().as_uri() + '?mode=rw', uri=True)) as db, db:
                daily_search.mark_jobs(db, snapshot['metadata']['profile_id'], landed)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--backlog', type=Path, default=settings.data_root() / 'work' / 'search-backlog.sqlite3')
    parser.add_argument('--tracker', type=Path, default=settings.data_root() / 'work' / 'applications-tracker.xlsx')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        report = land(args.tracker, args.snapshot, args.backlog, apply=args.apply)
    except (OSError, ValueError, sqlite3.Error) as error:
        report = {'ok': False, 'message': str(error)}
    print(json.dumps(report, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
