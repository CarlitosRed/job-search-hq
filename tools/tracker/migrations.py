"""Numbered tracker schema upgrades, backed up before any change."""
from datetime import datetime
from pathlib import Path
import sqlite3


def columns(db, table):
    return {row[1] for row in db.execute(f'PRAGMA table_info({table})')}


def baseline(db):
    for table in ('jobs', 'old_jobs'):
        if columns(db, table) and 'posted_on' not in columns(db, table):
            db.execute(f'ALTER TABLE {table} ADD COLUMN posted_on TEXT')
    db.execute('''CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY,
        job_id TEXT, kind TEXT, file_name TEXT, sha256 TEXT, made_by TEXT, created TEXT,
        posting_id TEXT, evidence_ids TEXT, checks TEXT)''')
    if columns(db, 'old_jobs') and 'original_job_id' not in columns(db, 'old_jobs'):
        db.execute('ALTER TABLE old_jobs ADD COLUMN original_job_id INTEGER')


def job_ref(db, text_id):
    if not isinstance(text_id, str) or '|' not in text_id:
        return None
    sheet, number = text_id.rsplit('|', 1)
    if not sheet or not number.isascii() or not number.isdecimal():
        return None
    if len(number) > 19 or int(number) > 9223372036854775807:
        return None
    table, field = ('old_jobs', 'original_job_id') if sheet == 'Old' else ('jobs', 'id')
    row = db.execute(f'SELECT {field} FROM {table} WHERE sheet=? AND row=?',
                     (sheet, int(number))).fetchone()
    return row[0] if row else None


def document_refs(db):
    if 'job_ref' not in columns(db, 'documents'):
        db.execute('ALTER TABLE documents ADD COLUMN job_ref INTEGER')
    for row in db.execute('SELECT id, job_id FROM documents WHERE job_ref IS NULL').fetchall():
        ref = job_ref(db, row[1])
        if ref is not None:
            db.execute('UPDATE documents SET job_ref=? WHERE id=?', (ref, row[0]))


def postings(db):
    db.execute('''CREATE TABLE IF NOT EXISTS postings (id INTEGER PRIMARY KEY,
        job_id INTEGER NOT NULL, profile_id TEXT NOT NULL, provider TEXT NOT NULL,
        board TEXT NOT NULL, posting_id TEXT NOT NULL, link TEXT, first_seen TEXT,
        last_seen TEXT, source TEXT, UNIQUE(profile_id, provider, board, posting_id))''')


# Card 03: closing and re-check.
def posting_checks(db):
    for name in ('run_id', 'posting_row', 'kind', 'observation', 'detail', 'at'):
        if name not in columns(db, 'checks'):
            db.execute(f'ALTER TABLE checks ADD COLUMN {name} ' + ('INTEGER' if name == 'posting_row' else 'TEXT'))
    for name, declaration in (('state', "TEXT DEFAULT 'open'"), ('closed_on', 'TEXT'), ('close_reason', 'TEXT'),
                              ('title', 'TEXT'), ('place', 'TEXT')):
        if name not in columns(db, 'postings'):
            db.execute(f'ALTER TABLE postings ADD COLUMN {name} {declaration}')
    db.execute('''CREATE TABLE IF NOT EXISTS board_reads (id INTEGER PRIMARY KEY,
        run_id TEXT, date TEXT, provider TEXT, board TEXT, read TEXT, rows INTEGER, reason TEXT)''')
    if 'read_at' not in columns(db, 'board_reads'):
        db.execute('ALTER TABLE board_reads ADD COLUMN read_at TEXT')


# Card 04a: qualification judge.
JUDGE_TABLES = '''
    CREATE TABLE IF NOT EXISTS judgements (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL,
        posting_row INTEGER, pass TEXT NOT NULL DEFAULT 'qualification', posting_hash TEXT,
        profile_id TEXT, profile_revision INTEGER, evidence_revision TEXT, model TEXT,
        prompt_version TEXT, schema_version TEXT, rule_version TEXT, cache_key TEXT,
        requirements TEXT, fit TEXT, suggestion TEXT, why TEXT, notes TEXT, place_sentence TEXT, score REAL,
        complete INTEGER, created TEXT, day TEXT, run_id TEXT, error TEXT, current INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS judgements_by_job ON judgements (job_id, pass, current);
    CREATE TABLE IF NOT EXISTS choice_signals (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL,
        kind TEXT NOT NULL, value TEXT, at TEXT, origin TEXT, owner TEXT, active INTEGER NOT NULL DEFAULT 1,
        supersedes INTEGER, reason TEXT, idempotency_key TEXT UNIQUE);
'''


def judge(db):
    for statement in JUDGE_TABLES.split(';'):
        if statement.strip():
            db.execute(statement)
    if 'day' not in columns(db, 'judgements'):
        db.execute('ALTER TABLE judgements ADD COLUMN day TEXT')
    # Archive and restore copy whole rows, so both tables carry the star.
    for table in ('jobs', 'old_jobs'):
        if columns(db, table) and 'starred' not in columns(db, table):
            db.execute(f'ALTER TABLE {table} ADD COLUMN starred INTEGER DEFAULT 0')


def choice_owner(db):
    if 'owner' not in columns(db, 'choice_signals'):
        db.execute('ALTER TABLE choice_signals ADD COLUMN owner TEXT')


def current_fit(db):
    # Keep migration 7 numbered for old databases. Only a judge save writes the action copy.
    pass


def judgement_score(db):
    if 'score' not in columns(db, 'judgements'):
        db.execute('ALTER TABLE judgements ADD COLUMN score REAL')
    if 'fit' in columns(db, 'jobs'):
        db.execute('''UPDATE jobs SET fit=NULL WHERE fit IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM judgements WHERE job_id=jobs.id AND pass='qualification' AND current=1 AND score IS NOT NULL
        )''')


STEPS = [(1, 'baseline', baseline), (2, 'document job numbers', document_refs),
         (3, 'posting links', postings), (4, 'posting checks', posting_checks), (5, 'judge', judge),
         (6, 'owner choices', choice_owner), (7, 'current fit', current_fit),
         (8, 'judgement score', judgement_score)]


def __getattr__(name):
    if name == 'LATEST':
        return STEPS[-1][0]
    raise AttributeError(name)


def upgrade(db, db_file):
    if not columns(db, 'meta'):
        return
    row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    version = int(row[0]) if row else 0
    # Older tools can leave a version marker beside an incomplete baseline.
    # Re-run the safe steps when the actual schema still needs that upgrade.
    if (any(columns(db, table) and 'posted_on' not in columns(db, table)
            for table in ('jobs', 'old_jobs'))
            or not columns(db, 'documents')
            or (columns(db, 'old_jobs') and 'original_job_id' not in columns(db, 'old_jobs'))):
        version = 0
    if version >= 4 and (not {'title', 'place'} <= columns(db, 'postings')
                         or 'read_at' not in columns(db, 'board_reads')):
        version = 3
    # Card 04a: qualification judge.
    if version >= 5 and ('day' not in columns(db, 'judgements') or not columns(db, 'choice_signals')
                         or any(columns(db, table) and 'starred' not in columns(db, table)
                                for table in ('jobs', 'old_jobs'))):
        version = 4
    if version >= 6 and 'owner' not in columns(db, 'choice_signals'):
        version = 5
    if version >= 8 and 'score' not in columns(db, 'judgements'):
        version = 7
    pending = [step for step in STEPS if step[0] > version]
    if not pending:
        return
    latest = STEPS[-1][0]
    path = Path(db_file)
    folder = path.parent / '.tracker-backups'
    folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    name = f'{path.stem}-before-migration-{version}-to-{latest}-{stamp}'
    target, index = folder / f'{name}.sqlite3', 1
    while True:
        try:
            with target.open('xb'):
                pass
            break
        except FileExistsError:
            target = folder / f'{name}-{index}.sqlite3'
            index += 1
    backup = sqlite3.connect(target)
    try:
        db.backup(backup)
    finally:
        backup.close()
    with db:
        db.execute('BEGIN IMMEDIATE')
        for number, name, step in pending:
            step(db)
        db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (latest,))
