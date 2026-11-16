"""Preview or link daily-search jobs to their backlog posting identities."""
import argparse
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import re
import sqlite3


_spec = importlib.util.spec_from_file_location('posting_backfill_store', Path(__file__).with_name('store.py'))
store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(store)
PROFILE = re.compile(r'\bprofile\s+(\S+)\s+rev\b')
POSTING = re.compile(r'\bposting\s+([^\s/;]+)/([^\s;]+)')


def backfill(xlsx, backlog, apply=False):
    report = {'ok': True, 'found': 0, 'linkable': 0, 'linked': 0, 'unknown': []}
    target = store.db_path(xlsx)
    if not target.is_file():
        report.update(ok=False, message='The tracker database does not exist. Migrate the workbook first.')
        return report
    storage = store.Store(target if apply else target.resolve().as_uri() + '?mode=ro', upgrade=apply)
    try:
        matches = {}
        with closing(sqlite3.connect(Path(backlog).resolve().as_uri() + '?mode=ro', uri=True)) as db:
            for profile, raw in db.execute('SELECT profile_id, identity FROM search_backlog'):
                try:
                    identity = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if (not isinstance(identity, list) or len(identity) != 3
                        or not all(isinstance(item, str) for item in identity)):
                    continue
                provider, board, posting = identity
                matches.setdefault((profile, provider, posting), []).append(board)
        with storage.db:
            storage.db.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
            for job in storage.db.execute('SELECT id, agent_notes, link, first_seen FROM jobs ORDER BY id').fetchall():
                notes = job['agent_notes'] or ''
                profile, posting = PROFILE.search(notes), POSTING.search(notes)
                if not profile or not posting:
                    continue
                report['found'] += 1
                profile_id = profile.group(1)
                provider, posting_id = posting.groups()
                boards = matches.get((profile_id, provider, posting_id), [])
                if len(boards) != 1:
                    report['unknown'].append({'job_id': job['id'], 'posting': f'{provider}/{posting_id}',
                                              'reason': 'No matching backlog row.' if not boards
                                              else 'Several backlog boards match.'})
                    continue
                report['linkable'] += 1
                if apply:
                    board = boards[0]
                    if storage.job_for_posting(profile_id, provider, board, posting_id) == job['id']:
                        continue
                    storage.link_posting(job['id'], profile_id, provider, board, posting_id,
                                         link=job['link'], seen=job['first_seen'], source='backfill')
                    report['linked'] += 1
        return report
    finally:
        storage.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tracker', required=True, type=Path)
    parser.add_argument('--backlog', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    report = backfill(args.tracker, args.backlog, apply=args.apply)
    print(json.dumps(report, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
