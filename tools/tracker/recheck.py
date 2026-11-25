"""Observe posting availability without changing stages or deleting history."""
# Card 03: closing and re-check.
import argparse
from contextlib import closing
from datetime import date, datetime, timedelta
from functools import cache
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent


def component(name, folder=HERE):
    spec = importlib.util.spec_from_file_location('recheck_' + name, folder / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@cache
def posting_reader():
    return component('link_fetch', HERE.parent / 'hq-dashboard')


def linkedin(link):
    host = (urlsplit(link or '').hostname or '').lower().rstrip('.')
    return host == 'linkedin.com' or host.endswith('.linkedin.com')


def link_skip_reason(link):
    reader = posting_reader()
    if reader.automatic_read_disallowed(link):
        return reader.READ_DISALLOWED_REASON
    if linkedin(link):
        return 'LinkedIn links are not checked'
    return ''


def classify(code, text):
    reader = posting_reader()
    if code == reader.READ_DISALLOWED:
        return 'unclear', reader.READ_DISALLOWED_REASON
    if code in (404, 410):
        return 'gone', f'The posting link returned {code}.'
    if code != 200:
        return 'unclear', f'The posting link returned {code}.' if code else 'The posting page could not be read.'
    if re.search(r'\b(sign in|log in|access denied|verify you are human|captcha)\b', text or '', re.I):
        return 'unclear', 'The page requires sign in or blocks access.'
    phrases = (r'no longer accepting (?:new )?applications',
               r'(?:this|the) (?:position|job|role) (?:has been|is|was) filled',
               r'(?:this|the) (?:job|position|posting|role) (?:is |was )?no longer available',
               r'(?:this|the) (?:posting|job|position|role) (?:has been |has |is |was )closed',
               r'(?:this|the) (?:posting|job|requisition) (?:has|is) expired',
               r'job expired')
    for sentence in re.split(r'(?<=[.!?])\s+|\n+', text or ''):
        match = re.search(r'\b(?:' + '|'.join(phrases) + r')\b', sentence, re.I)
        qualified = re.search(
            r'\b(until|once|when|after|if|will|from|starting|via|through)\b'
            r'|\bby\s+(?:email|post)\b|\bapply\s+(?:through|on|at)\b'
            r'|\b(?:on|by)\s+(?:\d|[A-Z][a-z]+\s+\d|'
            r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b)', sentence, re.I)
        if match and not qualified:
            return 'closed_text', sentence.strip()[:200]
    return ('open', '') if (text or '').strip() else ('unclear', 'The posting page had no readable text.')


def link_status(link, fetch=None, wait=2, deadline=None):
    reason = link_skip_reason(link)
    if reason:
        return 'unclear', reason
    fetch = fetch or posting_reader().fetch_status_text
    for attempt in range(2):
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            return 'unclear', 'The posting check ran out of time.'
        try:
            code, text = fetch(link) if remaining is None else fetch(link, timeout=remaining)
        except HTTPError as error:
            code, text = error.code, ''
        except Exception:
            if deadline is not None and time.monotonic() >= deadline:
                return 'unclear', 'The posting check ran out of time.'
            return 'unclear', 'The posting page could not be read.'
        if deadline is not None and time.monotonic() >= deadline:
            return 'unclear', 'The posting check ran out of time.'
        if code != 429 or attempt:
            return classify(code, text)
        if deadline is not None and time.monotonic() + wait >= deadline:
            return 'unclear', 'The posting check ran out of time.'
        time.sleep(wait)


def link_key(link):
    try:
        parts = urlsplit(link or '')
        return ((parts.hostname or '').lower(), parts.path.rstrip('/'), parts.query) if parts.path else None
    except ValueError:
        return None


def same_link(left, right):
    return bool(link_key(left)) and link_key(left) == link_key(right)


def title_key(title):
    return ''.join(re.findall(r'\w+', (title or '').casefold()))


def posting_status(storage, job_id):
    posts = storage.postings_for(job_id)
    checks = [dict(r) for r in storage.db.execute(
        'SELECT * FROM checks WHERE job_id=? AND observation IS NOT NULL ORDER BY id DESC', (job_id,))]
    checked = checks[0]['date'] if checks else None
    if posts:
        opened = [p for p in posts if p['state'] != 'closed']
        if opened:
            if all(p['state'] == 'suspected' for p in opened):
                notice = next((r for r in checks if r['observation'] == 'closed_text'), None)
                return dict(state='suspected', since=notice['date'] if notice else checked,
                            reason=notice['detail'] if notice else '', checked=checked)
            # A board sighting is the authority for tracked postings.
            return dict(state='open', since=max((p['last_seen'] or '' for p in opened)), reason='', checked=checked, via='board')
        last = max(posts, key=lambda p: p['closed_on'] or '')
        return dict(state='closed', since=last['closed_on'], reason=last['close_reason'] or '', checked=checked)
    if not checks:
        return dict(state='unknown', since=None, reason='', checked=None)
    last = checks[0]
    return dict(state=last['result'], since=last['date'], reason=last['detail'] or '', checked=checked, via='link')


def stale_boards(storage, today, days=3, current=None):
    today = date.fromisoformat(today) if isinstance(today, str) else today
    groups = {}
    for row in storage.db.execute('SELECT * FROM board_reads WHERE date<=? ORDER BY date,id', (today.isoformat(),)):
        key = (row['provider'], row['board'])
        if current is None or key in current:
            groups.setdefault(key, []).append(dict(row))
    result = []
    for (provider, board), rows in groups.items():
        complete = [r for r in rows if r['read'] == 'complete']
        since = (complete[-1] if complete else rows[0])['date']
        if (today - date.fromisoformat(since)).days >= days:
            result.append(dict(board=board, provider=provider, since=since, reason=rows[-1]['reason']))
    return result


def record_board_reads(db, run_id, today, read_at, reads):
    db.executemany('INSERT INTO board_reads(run_id,date,provider,board,read,rows,reason,read_at) VALUES (?,?,?,?,?,?,?,?)',
                   [(run_id, today, row['provider'], row['board'], row['read'],
                     row['rows'], row.get('reason', '')[:200], read_at) for row in reads])


def recheck(storage, snapshot, today, run_id, link_check, backlog=None, dry_run=False, budget_seconds=600):
    deadline = time.monotonic() + budget_seconds
    today = snapshot['metadata']['date']
    read_at = snapshot['metadata'].get('read_at') or today + 'T06:00'
    observed_at = datetime.fromisoformat(read_at)
    profile = snapshot['metadata']['profile_id']
    db = storage.db
    report = dict(dry_run=dry_run, checked=0, seen=0, missed=0, closed=[], reopened=[],
                  not_checked=[], no_information=[], link_checked=0, errors=[])
    jobs = {r['id']: dict(r) for r in db.execute('SELECT * FROM jobs')}
    posts = [dict(r) for r in db.execute('SELECT * FROM postings WHERE profile_id=?', (profile,))]
    before = {key: posting_status(storage, key) for key in jobs}
    touched, observed, plans, boards = set(), {}, [], {}

    def named(job_id, reason):
        return dict(job_id=job_id, company=jobs[job_id]['company'], role=jobs[job_id]['role'], reason=reason[:200])

    search_reads = []
    for source in snapshot.get('boards', []):
        # Search pages are bounded samples. Absence cannot close a posting.
        if source.get('search_only'):
            search_reads.append(source)
            continue
        key = (source['provider'], source['board'])
        source = dict(source)
        worldwide = source.get('worldwide')
        if (source['provider'] == 'workday' and worldwide and worldwide['read'] == 'complete'
                and worldwide.get('full_read') is True and not worldwide.get('skipped_rows')):
            # Prefer home details for a URL the worldwide pass also found.
            seen = {(row[0], row[1]) for row in source['seen']}
            source = dict(worldwide, seen=source['seen'] + [
                row for row in worldwide['seen'] if (row[0], row[1]) not in seen])
        if (source['provider'] == 'workday' and source['read'] == 'complete'
                and (source.get('full_read') is False or source.get('skipped_rows', 0))):
            source.update(read='partial', reason=source.get('reason') or 'Workday coverage was incomplete.')
        if key not in boards:
            boards[key] = dict(source, seen=list(source['seen']))
        else:
            merged = boards[key]
            merged['seen'].extend(source['seen'])
            merged['rows'] += source['rows']
            if source['read'] != 'complete':
                merged.update(read='partial', reason=source.get('reason', ''))
    for (provider, label), board in boards.items():
        state, count = board['read'], board['rows']
        reason = board.get('reason', '')
        previous = db.execute("SELECT rows FROM board_reads WHERE provider=? AND board=? AND read='complete' ORDER BY date DESC,id DESC LIMIT 1", (provider, label)).fetchone()
        if state == 'complete' and provider == 'lever' and count >= 500:
            state, reason = 'partial', 'Lever returned 500 or more rows.'
        if state == 'complete' and (not count or previous and count < previous['rows'] / 2):
            state, reason = 'partial', 'No information: zero rows or fewer than half the previous rows were read.'
            if provider == 'workday' and 'page' in board:
                reason = (f"Workday returned zero rows or fewer than half the previous rows after page "
                          f"{board['page']}. HTTP 200. {count} rows read.")
        board.update(read=state, reason=reason[:200])
        duplicate = db.execute("SELECT 1 FROM board_reads WHERE provider=? AND board=? AND date=? AND read='complete'",
                               (provider, label, today)).fetchone()
        board['duplicate'] = bool(duplicate)
        if state != 'complete':
            report['no_information'].append(dict(board=label, reason=reason[:200] or 'The board was not fully read.'))
            if provider != 'workday' or not board['seen']:
                continue
        observed[(provider, label)] = board
        tracked = [p for p in posts if (p['provider'], p['board']) == (provider, label)]
        matches, owned = {}, set()
        by_id, by_link = {}, {}
        if provider == 'workday':
            # Two IDs for the same URL can both establish presence, but describe one repost candidate.
            for index, seen in enumerate(board['seen']):
                if seen[0]:
                    by_id.setdefault(str(seen[0]), index)
                url = link_key(seen[1])
                if url:
                    by_link.setdefault(url, index)
        for post in tracked:
            index = by_id.get(post['posting_id'])
            if index is None:
                index = next((i for i, seen in enumerate(board['seen'])
                              if (seen[0] and str(seen[0]) == post['posting_id'])
                              or same_link(seen[1], post['link'])), None)
            if index is not None:
                matches[post['id']] = index
                owned.add(index)
                canonical = by_link.get(link_key(board['seen'][index][1]))
                if canonical is not None:
                    owned.add(canonical)
        missing = [p for p in tracked if p['id'] not in matches]

        def compatible(post, seen):
            job = jobs[post['job_id']]
            place = post.get('place') or job.get('place') or ''
            other = seen[3] if len(seen) > 3 else ''
            return bool(title_key(seen[2])) and title_key(seen[2]) == title_key(post.get('title') or job['role']) and (not place or not other or title_key(place) == title_key(other))

        candidates = {p['id']: [i for i, s in enumerate(board['seen']) if i not in owned and
                      (not by_link or by_link.get(link_key(s[1]), i) == i) and
                      not storage.job_for_posting(profile, provider, label, str(s[0])) and compatible(p, s)] for p in missing}
        for post in tracked:
            pid = post['id']
            indices = candidates.get(pid, [])
            if pid not in matches and indices:
                if len(indices) == 1 and sum(indices[0] in found for found in candidates.values()) == 1:
                    matches[pid] = indices[0]
                else:
                    plans.append((post, 'ambiguous', None, board))
                    continue
            if pid in matches:
                plans.append((post, 'seen', board['seen'][matches[pid]], board))
            elif state == 'complete' and post['state'] != 'closed' and not db.execute(
                    "SELECT 1 FROM checks WHERE posting_row=? AND kind='board' AND date=?",
                    (pid, today)).fetchone():
                plans.append((post, 'missed', None, board))

    seen_owners = {p['id']: storage.job_for_posting(profile, p['provider'], p['board'], str(seen[0]))
                   for p, action, seen, _ in plans if action == 'seen' and seen[0]}
    # Fetch all planned links before opening the write transaction.
    requests = [(p['job_id'], p['id'], p['link']) for p, action, _, _ in plans if action == 'missed']
    for job_id, job in jobs.items():
        if not storage.postings_for(job_id) and job['status'] != 'Skipped' and job.get('link') and before[job_id]['state'] != 'closed':
            requests.append((job_id, None, job['link']))
    results = {}
    for job_id, pid, link in requests:
        previous = db.execute("SELECT * FROM checks WHERE job_id=? AND posting_row IS ? AND kind='link' AND date=? ORDER BY id DESC LIMIT 1", (job_id, pid, today)).fetchone()
        if previous:
            results[(job_id, pid)] = ('prior', dict(previous))
            continue
        if link not in results:
            try:
                reason = link_skip_reason(link)
                if reason:
                    answer = ('unclear', reason)
                elif time.monotonic() >= deadline:
                    answer = ('unclear', 'Not checked: the time budget was reached.')
                else:
                    report['link_checked'] += 1
                    answer = link_status(link, deadline=deadline) if link_check is link_status else link_check(link)
                results[link] = (answer[0], str(answer[1])[:200])
            except Exception as error:
                results[link] = ('error', str(error)[:200] or 'The posting could not be checked.')
        results[(job_id, pid)] = results[link]

    owns_transaction = not db.in_transaction
    if owns_transaction:
        # Without BEGIN, releasing the outer savepoint would commit (and touch the file) on a dry run.
        db.execute('BEGIN IMMEDIATE')
    db.execute('SAVEPOINT posting_recheck')
    backlog_updates = []
    try:
        def valid(job_id, post=None, seen=None):
            current_job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            changed = current_job is None or any(
                current_job[key] != jobs[job_id][key] for key in ('link', 'role', 'place', 'status', 'sheet'))
            if post is not None:
                current = db.execute('SELECT * FROM postings WHERE id=?', (post['id'],)).fetchone()
                changed = changed or current is None or dict(current) != post
                if seen is not None and seen[0]:
                    owner = storage.job_for_posting(profile, post['provider'], post['board'], str(seen[0]))
                    changed = changed or owner != seen_owners[post['id']]
            else:
                changed = changed or bool(storage.postings_for(job_id))
            if changed:
                report['not_checked'].append(named(job_id, 'The posting changed during the check.'))
                return False
            # Snapshot dates, rather than insertion order, determine which evidence is newer.
            newer = db.execute('''SELECT 1 FROM checks WHERE job_id=? AND observation IS NOT NULL
                                  AND date>? LIMIT 1''', (job_id, today)).fetchone()
            if post is not None and max(post['last_seen'] or '', post['closed_on'] or '') > today:
                return False
            return not newer

        def record(job_id, pid, kind, observation, detail='', state='open'):
            db.execute('''INSERT INTO checks(job_id,date,result,run_id,posting_row,kind,observation,detail,at)
                VALUES (?,?,?,?,?,?,?,?,?)''', (job_id, today, state, run_id, pid, kind, observation, detail[:200], read_at))
            touched.add(job_id)

        def history(job_id, pid):
            rows = []
            for row in db.execute('SELECT * FROM checks WHERE job_id=? AND posting_row IS ? ORDER BY id DESC', (job_id, pid)):
                if row['observation'] in ('seen', 'open'):
                    break
                rows.append(dict(row))
            return rows

        def link_result(job_id, pid, record_row=True):
            observation, detail = results[(job_id, pid)]
            if observation == 'prior':
                return detail['observation'], detail['detail'], detail['result']
            if observation == 'error':
                report['errors'].append(named(job_id, detail))
                return 'unclear', detail, 'not_checked'
            notices = []
            for row in history(job_id, pid):
                # An open link on a missed board read resets notices, not misses.
                if row['observation'] == 'missed' and row['result'] == 'open':
                    break
                notices.append(row)
            confirmed = observation == 'gone' or observation == 'closed_text' and any(r['observation'] == 'closed_text' and r['date'] != today for r in notices)
            state = 'closed' if confirmed else 'suspected' if observation == 'closed_text' else 'open' if observation == 'open' else 'not_checked'
            if observation == 'unclear':
                report['not_checked'].append(named(job_id, detail))
            if record_row:
                record(job_id, pid, 'link', observation, detail, state)
            return observation, detail, state

        record_board_reads(db, run_id, today, read_at, [*boards.values(), *search_reads])
        for post, action, seen, board in plans:
            job_id, pid = post['job_id'], post['id']
            if not valid(job_id, post, seen):
                continue
            if action == 'ambiguous':
                report['not_checked'].append(named(job_id, 'Possible repost: more than one match; check by hand.'))
                continue
            if action == 'seen':
                ids = [pid]
                if seen[0] and str(seen[0]) != post['posting_id'] and storage.job_for_posting(profile, post['provider'], post['board'], str(seen[0])) in (None, job_id):
                    storage.link_posting(job_id, profile, post['provider'], post['board'], str(seen[0]), link=seen[1], seen=post['first_seen'], source='repost')
                    ids.append(db.execute('SELECT id FROM postings WHERE profile_id=? AND provider=? AND board=? AND posting_id=?', (profile, post['provider'], post['board'], str(seen[0]))).fetchone()[0])
                for matched in ids:
                    db.execute("UPDATE postings SET state='open',closed_on=NULL,close_reason=NULL,last_seen=MAX(COALESCE(last_seen,''),?),title=?,place=? WHERE id=?", (today, seen[2] or post.get('title') or jobs[job_id]['role'], (seen[3] if len(seen) > 3 else '') or post.get('place') or jobs[job_id].get('place') or '', matched))
                record(job_id, pid, 'board', 'seen')
                report['seen'] += 1
                continue
            prior = [r for r in history(job_id, pid) if r['kind'] == 'board' and r['observation'] in ('missed', 'closed_text')]
            last = prior[0] if prior else None
            counted = not last or (last['date'] != today and observed_at - datetime.fromisoformat(last['at'] or last['date'] + 'T06:00') >= timedelta(hours=18))
            observation, detail, state = link_result(job_id, pid, False)
            if results[(job_id, pid)][0] == 'error':
                continue
            board_closed = counted and bool(prior)
            if board_closed or state == 'closed':
                reason = detail if state == 'closed' else 'Not on the job board on two complete reads.'
                db.execute("UPDATE postings SET state='closed',closed_on=COALESCE(closed_on,?),close_reason=? WHERE id=?", (today, reason[:200], pid))
            elif state == 'suspected':
                db.execute("UPDATE postings SET state='suspected' WHERE id=?", (pid,))
            elif state == 'open':
                db.execute("UPDATE postings SET state='open' WHERE id=?", (pid,))
            if counted:
                record(job_id, pid, 'board', observation if observation in ('gone', 'closed_text') else 'missed', detail, 'closed' if board_closed else state)
                report['missed'] += 1
            elif observation in ('gone', 'closed_text', 'unclear'):
                record(job_id, pid, 'link', observation, detail, state)
        for job_id, pid, link in requests:
            if pid is None and valid(job_id):
                link_result(job_id, pid)

        if backlog and Path(backlog).is_file():
            with closing(sqlite3.connect(Path(backlog).resolve().as_uri() + '?mode=ro', uri=True)) as queue:
                for identity, body in queue.execute('SELECT identity,body FROM search_backlog WHERE profile_id=? AND landed=0', (profile,)):
                    job = json.loads(body)
                    board = observed.get((job['provider'], job['company']))
                    if not board:
                        continue
                    seen = any((s[0] and str(s[0]) == str(job['posting_id'])) or same_link(s[1], job.get('link')) for s in board['seen'])
                    if seen:
                        job.update(gone=False, missed_days=[])
                        job.pop('missed_at', None)
                    elif board['read'] == 'complete' and not board['duplicate']:
                        last = job.get('missed_at')
                        if not last or observed_at - datetime.fromisoformat(last) >= timedelta(hours=18):
                            job['missed_days'] = sorted(set(job.get('missed_days', [])) | {today})
                            job['missed_at'] = read_at
                            if len(job['missed_days']) >= 2:
                                job['gone'] = True
                    backlog_updates.append((json.dumps(job), profile, identity))
        report['checked'] = len(touched)
        for job_id in sorted(touched):
            after = posting_status(storage, job_id)
            if after['state'] == 'closed' and before[job_id]['state'] != 'closed':
                report['closed'].append(named(job_id, after['reason']))
            elif before[job_id]['state'] == 'closed' and after['state'] == 'open':
                report['reopened'].append(named(job_id, 'The posting was seen again.'))
        # Backlog writes are idempotent and precede the tracker commit.
        if backlog_updates and not dry_run:
            with closing(sqlite3.connect(backlog)) as queue, queue:
                queue.executemany('UPDATE search_backlog SET body=? WHERE profile_id=? AND identity=?', backlog_updates)
        if dry_run:
            db.execute('ROLLBACK TO posting_recheck')
        db.execute('RELEASE posting_recheck')
        if owns_transaction:
            db.rollback() if dry_run else db.commit()
    except Exception:
        db.execute('ROLLBACK TO posting_recheck')
        db.execute('RELEASE posting_recheck')
        if owns_transaction:
            db.rollback()
        raise
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    dry_run = not args.apply or args.dry_run
    settings = component('hq_settings', HERE.parent / 'hq-dashboard')
    store = component('store')
    work = settings.data_root() / 'work'
    target = work / 'applications-tracker.sqlite3'
    if not target.is_file():
        parser.error('The tracker database does not exist. Migrate the workbook first.')
    snapshot = json.loads(args.snapshot.read_text(encoding='utf-8'))
    with closing(store.Store(':memory:' if dry_run else target)) as storage:
        if dry_run:
            with closing(sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True)) as source:
                source.backup(storage.db)
            with storage.db:
                for _, _, step in store.migrations.STEPS:
                    step(storage.db)
        report = recheck(storage, snapshot, snapshot['metadata']['date'], 'manual-' + snapshot['metadata']['date'], link_status, backlog=work / 'search-backlog.sqlite3', dry_run=dry_run)
    print(f"{report['checked']} jobs checked.")
    for group in ('closed', 'reopened', 'not_checked', 'errors'):
        for item in report[group]:
            print(f"{group.replace('_', ' ').capitalize()}: {item['company']} - {item['role']}: {item['reason']}")
    for item in report['no_information']:
        print(f"No information: {item['board']}: {item['reason']}")


if __name__ == '__main__':
    main()
