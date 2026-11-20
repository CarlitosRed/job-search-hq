"""Run and resume the daily search, with a durable record of every phase."""
import argparse
from contextlib import closing, contextmanager
from datetime import date, datetime, time, timedelta
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time as clock
import traceback
import uuid


def load_component(name, folder):
    spec = importlib.util.spec_from_file_location('runner_' + name, folder / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).resolve().parent
land_search = load_component('land_search', HERE)
# Card 03: closing and re-check.
recheck = load_component('recheck', HERE)
# Card 04a: qualification judge.
judge = load_component('judge', HERE)
daily_search = land_search.daily_search
settings = daily_search.settings
SUCCESS = ('done', 'done_with_warnings')


class RunBusy(Exception):
    pass


@contextmanager
def run_lock(root):
    """Hold a nonblocking OS lock; the OS also releases it on process death."""
    path = Path(root) / 'work' / 'daily-run.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            acquire = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        try:
            acquire()
        except OSError as error:
            raise RunBusy() from error
        try:
            yield
        finally:
            stream.seek(0)
            release()


def slot_date(now, at):
    scheduled = time.fromisoformat(at)
    return now.date() - timedelta(days=now.time() < scheduled)


def read_runs(db):
    db.row_factory = sqlite3.Row
    return [dict(row) for row in db.execute('SELECT * FROM runs ORDER BY rowid')]


def missed_slot(runs, slot):
    successes = [date.fromisoformat(r['slot']) for r in runs if r['status'] in SUCCESS]
    if not successes:
        return None
    first = max(successes) + timedelta(days=1)
    return first.isoformat() if first < slot else None


def phases_for(db, run_id):
    return {row['name']: {'status': row['status'], 'detail': row['detail']}
            for row in db.execute('SELECT * FROM phases WHERE run_id=? ORDER BY rowid', (run_id,))}


def configuration(root):
    config = settings.load(root, defaults_root=settings.app_root())['daily_run']
    if not isinstance(config, dict):
        raise ValueError('Daily settings must be an object.')
    config = {'at': '06:00', 'before': [], 'copies': [], 'recheck': 'on', 'judge': 'on', **config}
    if config['recheck'] not in ('on', 'preview'):
        raise ValueError('The daily recheck setting must be on or preview.')
    # Card 04a: qualification judge.
    if config['judge'] not in ('on', 'preview'):
        raise ValueError('The daily judge setting must be on or preview.')
    try:
        at = config['at']
        if not isinstance(at, str) or time.fromisoformat(at).strftime('%H:%M') != at:
            raise ValueError()
    except ValueError:
        raise ValueError('The daily start time must look like 06:00.') from None
    for group in ('before', 'copies'):
        if not isinstance(config[group], list):
            raise ValueError('Daily steps must be a list.')
        names = set()
        for step in config[group]:
            if (not isinstance(step, dict) or
                    any(not isinstance(step.get(key), str) or not step[key].strip()
                        for key in ('name', 'owner', 'destination')) or
                    not isinstance(step.get('command'), list) or not step['command'] or
                    any(not isinstance(part, str) for part in step['command'])):
                raise ValueError('Each daily step needs a name, owner, destination and a list of command strings.')
            if step['name'] in names:
                raise ValueError('Daily step names must be unique within each group.')
            names.add(step['name'])
    return config


def jobs_text(n):
    return f"{n} new {'job' if n == 1 else 'jobs'}."


def last_output_line(output):
    return next((line.strip()[:200] for line in reversed(output.splitlines()) if line.strip()), '')


def command_step(step, root):
    try:
        result = subprocess.run(step['command'], cwd=root, timeout=600, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', shell=False)
        output = last_output_line('\n'.join((result.stdout, result.stderr)))
        return ('done', 'Completed.') if result.returncode == 0 else ('failed', output or 'Command failed.')
    except subprocess.TimeoutExpired as error:
        output = '\n'.join(part.decode('utf-8', errors='replace') if isinstance(part, bytes) else part or ''
                           for part in (error.stdout, error.stderr))
        return 'failed', last_output_line(output) or 'Command timed out.'
    except OSError as error:
        return 'failed', 'Command could not run: ' + str(error)[-500:]


def run_daily(root, now, fetch=None, force=False, trigger='scheduled', link_check=None, complete=None):
    root = Path(root)
    config = configuration(root)  # Validate every command before any writable work.
    slot = slot_date(now, config['at'])
    empty = dict(run_id=None, status='already_running', slot=slot.isoformat(), phases={}, missed_since=None)
    try:
        with run_lock(root):
            return run_locked(root, now, fetch or daily_search.boards.fetch_json, force, trigger, config, slot,
                              link_check or recheck.link_status, complete or judge.seam.complete)
    except RunBusy:
        return empty


def run_locked(root, now, fetch, force, trigger, config, slot, link_check, complete):
    work = root / 'work'
    with closing(sqlite3.connect(work / 'daily-runs.sqlite3')) as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, profile_id TEXT, slot TEXT,
                trigger TEXT, started TEXT, finished TEXT, status TEXT, missed_since TEXT);
            CREATE TABLE IF NOT EXISTS phases (run_id TEXT, name TEXT, status TEXT, started TEXT,
                finished TEXT, detail TEXT, PRIMARY KEY(run_id, name));
        ''')
        runs = read_runs(db)
        current = [r for r in runs if r['slot'] == slot.isoformat()]
        successful = [r for r in current if r['status'] in SUCCESS]
        if successful and not force:
            run = successful[-1]
            return dict(run_id=run['run_id'], status='up_to_date', slot=run['slot'],
                        phases=phases_for(db, run['run_id']), missed_since=run['missed_since'])
        missed = missed_slot(runs, slot)
        run_id = current[-1]['run_id'] if current and not force else uuid.uuid4().hex
        stamp = now.isoformat(timespec='seconds')
        with db:
            db.execute('INSERT OR IGNORE INTO runs VALUES (?,NULL,?,?,?,NULL,?,?)',
                       (run_id, slot.isoformat(), trigger, stamp, 'running', missed))
            db.execute("UPDATE runs SET status='running', finished=NULL WHERE run_id=?", (run_id,))
        phases = phases_for(db, run_id)
        missed = db.execute('SELECT missed_since FROM runs WHERE run_id=?', (run_id,)).fetchone()[0]
        snapshot = work / 'daily-runs' / (run_id + '.json')
        try:
            saved = json.loads(snapshot.read_text(encoding='utf-8'))
            if not isinstance(saved, dict) or not isinstance(saved.get('source_errors'), list):
                saved = None
            elif saved['source_errors'] and len(saved['source_errors']) == len(
                    daily_search.profiles.load(root / 'profile.json')['search']['companies']):
                saved = None
        except (OSError, ValueError):
            saved = None
        if phases.get('search', {}).get('status') == 'failed':
            saved = None
        if saved is None:
            phases.pop('search', None)
        backlog = work / 'search-backlog.sqlite3'
        began = datetime.now()

        def timestamp():
            return (now + max(timedelta(), datetime.now() - began)).isoformat(timespec='seconds')

        def phase(name, action):
            old = phases.get(name, {})
            if (old.get('status') == 'done' or old.get('status') == 'partial' and name != 'recheck') or (
                    name != 'recheck' and old.get('status') == 'skipped'
                    and old.get('detail', '').startswith('not built yet')):
                return old['status']
            with db:
                db.execute('INSERT INTO phases VALUES (?,?,?,?,NULL,?) ON CONFLICT(run_id,name) '
                           'DO UPDATE SET status=excluded.status, started=excluded.started, finished=NULL, detail=excluded.detail',
                           (run_id, name, 'running', timestamp(), ''))
            try:
                state, detail = action()
            except Exception:
                traceback.print_exc()
                state, detail = 'failed', 'The ' + name + ' step could not finish. Please retry.'
            with db:
                db.execute('UPDATE phases SET status=?, finished=?, detail=? WHERE run_id=? AND name=?',
                           (state, timestamp(), detail, run_id, name))
            phases[name] = dict(status=state, detail=detail)
            return state

        def finish(state):
            with db:
                db.execute('UPDATE runs SET status=?, finished=? WHERE run_id=?', (state, timestamp(), run_id))
            return dict(run_id=run_id, status=state, slot=slot.isoformat(), phases=phases, missed_since=missed)

        def search():
            try:
                profile = daily_search.profiles.load(root / 'profile.json')
            except (OSError, ValueError):
                return 'failed', 'The search profile is missing or invalid. Check the profile before retrying.'
            with db:
                db.execute('UPDATE runs SET profile_id=? WHERE run_id=?', (profile['id'], run_id))
            result = saved
            if result is None:
                result = daily_search.run(profile, fetch, backlog, slot, mark_landed=False)
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                temp = snapshot.with_suffix('.tmp')
                temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                temp.replace(snapshot)
            total = len(profile['search']['companies'])
            failed = len(result['source_errors'])
            detail = f'{total - failed} of {total} job boards read; {failed} could not be checked.'
            return ('failed' if failed == total else 'partial' if failed else 'done'), detail

        def land():
            report = land_search.land(work / 'applications-tracker.xlsx', snapshot, backlog,
                                      apply=True, today=now.date())
            if not report['ok']:
                return 'failed', 'The tracker could not take the search results. Please retry.'
            return 'done', jobs_text(report['added'])

        def check_postings():
            preview = config['recheck'] == 'preview'
            tracker = work / 'applications-tracker.sqlite3'
            with closing(land_search.store.Store(':memory:' if preview else tracker)) as storage:
                if preview:
                    with closing(sqlite3.connect(tracker.resolve().as_uri() + '?mode=ro', uri=True)) as source:
                        source.backup(storage.db)
                    with storage.db:
                        for _, _, step in land_search.store.migrations.STEPS:
                            step(storage.db)
                report = recheck.recheck(storage, json.loads(snapshot.read_text(encoding='utf-8')),
                                         slot, run_id, link_check, backlog=backlog, dry_run=preview)
            if preview:
                target = snapshot.with_name(run_id + '-recheck-preview.json')
                temporary = target.with_suffix('.tmp')
                temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                temporary.replace(target)
            detail = (f"{report['checked']} jobs checked; {len(report['closed'])} closed; "
                      f"{len(report['not_checked'])} not checked.")
            if preview:
                detail = (f"Preview: {len(report['closed'])} would close now (links gone). "
                          'Closes after two missed reads start once re-check is on.')
            errors = len(report['errors'])
            if errors:
                detail += f' {errors} jobs could not be checked.'
            return ('partial' if errors else 'done'), detail

        # Card 04a: qualification judge.
        def judge_jobs():
            preview = config['judge'] == 'preview'
            tracker = work / 'applications-tracker.sqlite3'
            profile = daily_search.profiles.load(root / 'profile.json')
            excluded = settings.load(root, defaults_root=settings.app_root())['excluded_evidence_ids']
            evidence = judge.judge_evidence(root, excluded)
            with closing(land_search.store.Store(':memory:' if preview else tracker)) as storage:
                if preview:
                    with closing(sqlite3.connect(tracker.resolve().as_uri() + '?mode=ro', uri=True)) as source:
                        source.backup(storage.db)
                    with storage.db:
                        for _, _, step in land_search.store.migrations.STEPS:
                            step(storage.db)
                waiting = judge.pending_jobs(storage, json.loads(snapshot.read_text(encoding='utf-8')))
                report = judge.judge_pending(storage, profile, evidence, now.date(), run_id, complete,
                                             profile['search']['judge_budget'], dry_run=preview, jobs=waiting,
                                             deadline=clock.monotonic() + 30 * 60, backlog=backlog,
                                             # Previews run on a copy, so the real usage file is named here.
                                             usage=work / 'ai-usage.sqlite3',
                                             preview_cache=snapshot.with_name('judge-preview-cache.json'))
            if preview:
                target = snapshot.with_name(run_id + '-judge-preview.json')
                temporary = target.with_suffix('.tmp')
                temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                temporary.replace(target)
            if report['ai_unavailable'] and not report['judged']:
                return 'skipped', 'AI unavailable. Jobs without a score are still unscored.'

            def count(n):
                return f"{n} {'job' if n == 1 else 'jobs'}"
            detail = (f"{count(report['judged'])} {'would be ' if preview else ''}judged; "
                      f"{report['pending']} waiting for tomorrow.")
            if preview:
                detail = 'Preview: ' + detail + ' Nothing was saved.'
            if report['failed']:
                detail += f" {count(len(report['failed']))} could not be judged."
            no_text = sum(item['reason'] == judge.NO_TEXT for item in report['skipped_jobs'])
            if no_text:
                detail += f" {count(no_text)} without posting text yet."
            if any(item['reason'] == judge.NOT_ENOUGH for item in report['skipped_jobs']):
                detail += ' ' + judge.NOT_ENOUGH + '.'
            if report['backed_off']:
                detail += f" {count(report['backed_off'])} failed recently and will be tried again later."
            if report['ai_unavailable']:
                return 'partial', detail + ' The AI became unavailable; the remaining jobs keep their current scores.'
            return 'done', detail

        for step in config['before']:
            if phase('before:' + step['name'], lambda step=step: command_step(step, root)) == 'failed':
                return finish('failed')
        if phase('search', search) == 'failed':
            phase('land', lambda: ('skipped', 'Search did not finish.'))
            return finish('failed')
        if phase('land', land) == 'failed':
            return finish('failed')
        if phase('recheck', check_postings) == 'failed':
            return finish('failed')
        phase('judge', judge_jobs)
        for step in config['copies']:
            phase('copy:' + step['name'], lambda step=step: command_step(step, root))
        warning = any(p['status'] in ('failed', 'partial') for p in phases.values())
        return finish('done_with_warnings' if warning else 'done')


def status(record_path, now, at='06:00'):
    result = dict(state='never', tone='n', message="Daily search hasn't run yet.",
                  last_success=None, last_run=None, missed_since=None)
    slot = slot_date(now, at)
    overdue = slot == now.date() and now - datetime.combine(slot, time.fromisoformat(at)) > timedelta(minutes=60)
    path = Path(record_path)
    if not path.is_file():
        return result
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            runs = read_runs(db)
            if not runs:
                return result
            latest = runs[-1]
            successful = [r for r in runs if r['status'] in SUCCESS]
            success = successful[-1] if successful else None
            result.update(last_run=latest['finished'] or latest['started'],
                          last_success=success['finished'] if success else None,
                          missed_since=missed_slot(runs, slot))
            phases = phases_for(db, latest['run_id'])
            if latest['status'] == 'running':
                stale = now - datetime.fromisoformat(latest['started']) > timedelta(hours=3)
                result.update(state='running', tone='a' if stale else 'n',
                              message='The last daily search stopped part way. It resumes at the next run.'
                              if stale else 'Daily search is running now.')
            elif latest['status'] == 'failed':
                failed = next((name for name, p in phases.items() if p['status'] == 'failed'), '')
                label = 'searching' if failed == 'search' else 'landing' if failed == 'land' else 'preparing the search'
                if failed == 'recheck':
                    message = 'Daily search found jobs, but the re-check could not finish; it will retry.'
                elif failed.startswith('before:'):
                    detail = last_output_line(phases[failed]['detail'])
                    message = f'Daily search stopped because the {failed[7:]} step failed: {detail}'
                    if not message.endswith('.'):
                        message += '.'
                else:
                    message = 'Last daily search failed while ' + label + '. Nothing was lost; it retries at the next run.'
                result.update(state='failed', tone='r', message=message)
            elif result['missed_since']:
                day = date.fromisoformat(result['missed_since'])
                result.update(state='missed', tone='a', message=f'Daily search missed since {day:%b} {day.day}. '
                              'A catch-up starts automatically.')
            elif overdue and not any(r['slot'] == slot.isoformat() for r in successful):
                result.update(state='missed', tone='a', message=f"Today's {at} search hasn't run yet.")
            elif latest['status'] in SUCCESS:
                finished = datetime.fromisoformat(latest['finished'])
                day = 'today' if finished.date() == now.date() else f'on {finished:%b} {finished.day}'
                prefix = f'Last search {day} at {finished:%H:%M}'
                warnings = [name[5:] + ': ' + last_output_line(p['detail']) for name, p in phases.items()
                            if name.startswith('copy:') and p['status'] == 'failed']
                if phases.get('search', {}).get('status') == 'partial':
                    warnings.append('some job boards could not be checked')
                if phases.get('recheck', {}).get('status') == 'partial':
                    warnings.append(last_output_line(phases['recheck']['detail']))
                if phases.get('judge', {}).get('status') == 'partial':
                    warnings.append('the AI became unavailable while judging; the remaining jobs keep their current scores')
                if warnings:
                    message = prefix + ' finished, but ' + '; '.join(warnings)
                    result.update(state='warning', tone='a', message=message if message.endswith('.') else message + '.')
                else:
                    result.update(state='ok', tone='n', message=prefix + ': ' + phases.get('land', {}).get('detail', 'Search completed.'))
            else:
                result.update(state='warning', tone='a', message='The daily search is running or was interrupted. It resumes at the next run.')
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
        result.update(state='failed', tone='r', message='The daily search record could not be read.')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--now', type=datetime.fromisoformat, default=None)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--trigger', choices=('scheduled', 'catch-up', 'manual'), default='scheduled')
    args = parser.parse_args()
    try:
        result = run_daily(settings.data_root(), args.now or datetime.now(), force=args.force, trigger=args.trigger)
    except (OSError, ValueError, sqlite3.Error):
        print('Daily search failed: check the daily settings and writable data folder.')
        return 1
    state = result['status']
    if state == 'up_to_date':
        print("Up to date: today's search already ran.")
    elif state == 'already_running':
        print('Daily search is already running.')
    elif state == 'failed':
        print(status(settings.data_root() / 'work' / 'daily-runs.sqlite3', args.now or datetime.now())['message'])
    else:
        print('Daily search ' + ('done with warnings' if state == 'done_with_warnings' else 'done') +
              ': ' + result['phases']['land']['detail'])
    return 2 if state == 'already_running' else 1 if state == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
