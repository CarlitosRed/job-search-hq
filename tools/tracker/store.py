"""SQLite tracker storage and the disposable Excel mirror."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

import openpyxl
from openpyxl.worksheet.datavalidation import DataValidation

STATUSES = ('Suggested (not applied)', 'Message drafted', 'CV ready', 'Applied',
            'Interview', 'Rejected', 'Offer', 'Skipped')
NAMED = {'company': 'Company', 'role': 'Role', 'place': 'Where', 'link': 'Link',
         'status': 'My status', 'fit': 'Verdict', 'notes': 'Notes',
         'agent_notes': 'Agent notes', 'posted': 'Posted'}
TABLES = ('jobs', 'old_jobs', 'contacts', 'other_rows')


def db_path(xlsx):
    return Path(xlsx).with_suffix('.sqlite3')


def encode(values):
    def tagged(v):
        if isinstance(v, (date, datetime)):
            return {'datetime': v.isoformat()}
        return v
    return json.dumps({str(i): tagged(v) for i, v in enumerate(values)}, ensure_ascii=False)


def decode(raw):
    return [datetime.fromisoformat(v['datetime']) if isinstance(v, dict) else v
            for v in json.loads(raw).values()]


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy_log(path, label):
    path = Path(path)
    folder = path.parent / 'logs'
    folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    target = folder / f'{path.stem}-{label}-{stamp}.xlsx'
    # Exclusive creation preserves earlier copies even within the same second.
    index = 1
    while True:
        try:
            stream = target.open('xb')
            break
        except FileExistsError:
            target = folder / f'{path.stem}-{label}-{stamp}-{index}.xlsx'
            index += 1
    with stream, path.open('rb') as source:
        shutil.copyfileobj(source, stream)
    return target


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row

    def close(self):
        self.db.close()

    def create(self):
        self.db.executescript('''
            CREATE TABLE meta (key TEXT PRIMARY KEY, value);
            CREATE TABLE layouts (source TEXT, sheet TEXT, position INTEGER,
                header TEXT, widths TEXT, freeze TEXT, validation INTEGER, max_row INTEGER,
                PRIMARY KEY(source, sheet));
            CREATE TABLE moves (id INTEGER PRIMARY KEY, job_id INTEGER,
                from_status TEXT, to_status TEXT, at TEXT, who TEXT);
            CREATE TABLE interview_rounds (id INTEGER PRIMARY KEY, job_id INTEGER,
                round INTEGER, date TEXT, type TEXT, note TEXT);
            CREATE TABLE sent (id INTEGER PRIMARY KEY, job_id INTEGER, date TEXT,
                cv_file TEXT, cv_hash TEXT, letter_file TEXT, letter_hash TEXT,
                sentiment TEXT, where_sent TEXT, recorded_later INTEGER);
            CREATE TABLE checks (id INTEGER PRIMARY KEY, job_id INTEGER, date TEXT, result TEXT);
        ''')
        for table in TABLES:
            self.db.execute(f'''CREATE TABLE {table} (id INTEGER PRIMARY KEY,
                sheet TEXT, row INTEGER, company TEXT, role TEXT, place TEXT, link TEXT,
                status TEXT, fit TEXT, notes TEXT, agent_notes TEXT, posted TEXT,
                date_source TEXT, first_seen TEXT, old_status TEXT, fields TEXT,
                UNIQUE(sheet, row))''')
        for key, value in (('revision', 1), ('mirror_hash', ''),
                           ('mirror_warning', ''), ('mirror_stale', 0)):
            self.set_meta(key, value)

    def meta(self, key):
        return self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()[0]

    def set_meta(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', (key, value))

    @staticmethod
    def table(source, title, head):
        if source == 'old':
            return 'old_jobs'
        if 'My status' in head:
            return 'jobs'
        return 'contacts' if title == 'Contacts' else 'other_rows'

    def put_row(self, table, title, number, head, values, history=False):
        fields = encode(values)
        previous = self.db.execute(f'SELECT id, status, fields FROM {table} WHERE sheet=? AND row=?',
                                   (title, number)).fetchone()
        if previous and previous['fields'] == fields:
            return
        row = dict(zip(head, values))
        named = [row.get(h) for h in NAMED.values()]
        named = [v.isoformat() if isinstance(v, (date, datetime)) else v for v in named]
        if previous:
            assignments = ', '.join(f'{key}=?' for key in NAMED)
            self.db.execute(f'UPDATE {table} SET {assignments}, fields=? WHERE id=?',
                            (*named, fields, previous['id']))
            if history and table == 'jobs' and previous['status'] != row.get('My status'):
                self.db.execute('INSERT INTO moves(job_id, from_status, to_status, at, who) VALUES (?,?,?,?,?)',
                                (previous['id'], previous['status'], row.get('My status'),
                                 datetime.now(timezone.utc).isoformat(), 'site'))
        else:
            columns = ', '.join(NAMED)
            self.db.execute(f'''INSERT INTO {table}
                (sheet, row, {columns}, date_source, first_seen, old_status, fields)
                VALUES ({','.join('?' for _ in range(15))})''',
                (title, number, *named, '', date.today().isoformat() if history else None,
                 row.get('My status'), fields))

    def import_workbook(self, path, source):
        wb = openpyxl.load_workbook(path)
        try:
            for position, ws in enumerate(wb):
                head = [c.value for c in ws[1]]
                status_col = head.index('My status') + 1 if 'My status' in head else 0
                validation = any(dv.type == 'list' and any(
                    rng.min_col <= status_col <= rng.max_col for rng in dv.sqref.ranges)
                    for dv in ws.data_validations.dataValidation)
                self.db.execute('INSERT INTO layouts VALUES (?,?,?,?,?,?,?,?)',
                                (source, ws.title, position, encode(head),
                                 json.dumps({k: v.width for k, v in ws.column_dimensions.items()}),
                                 str(ws.freeze_panes) if ws.freeze_panes else None,
                                 int(validation), ws.max_row))
                table = self.table(source, ws.title, head)
                for number, values in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
                    if any(v is not None for v in values):
                        self.put_row(table, ws.title, number, head, values)
        finally:
            wb.close()

    def counts(self):
        result = {'sheets': {}, 'old': {}, 'contacts': 0, 'rows': 0}
        for layout in self.db.execute('SELECT * FROM layouts ORDER BY source, position'):
            head = decode(layout['header'])
            table = self.table(layout['source'], layout['sheet'], head)
            counts = {}
            if layout['source'] == 'main' and 'My status' in head:
                result['sheets'][layout['sheet']] = counts
            if any(v is not None for v in head):
                result['rows'] += 1
            for record in self.db.execute(f'SELECT fields FROM {table} WHERE sheet=?', (layout['sheet'],)):
                values = decode(record['fields'])
                if not any(v is not None for v in values):
                    continue
                result['rows'] += 1
                if 'My status' in head and len(values) > 1 and values[1]:
                    target = result['old'] if layout['source'] == 'old' else counts
                    status = values[head.index('My status')]
                    target[status] = target.get(status, 0) + 1
                elif table == 'contacts' and values[0]:
                    result['contacts'] += 1
        return result

    def workbook(self):
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for layout in self.db.execute("SELECT * FROM layouts WHERE source='main' ORDER BY position"):
            ws = wb.create_sheet(layout['sheet'])
            head = decode(layout['header'])
            rows = [(1, head)]
            table = self.table('main', ws.title, head)
            rows.extend((r['row'], decode(r['fields'])) for r in self.db.execute(
                f'SELECT row, fields FROM {table} WHERE sheet=? ORDER BY row', (ws.title,)))
            for number, values in rows:
                for column, value in enumerate(values, 1):
                    dst = ws.cell(number, column)
                    dst.value = value
                    if isinstance(value, str):
                        dst.data_type = 's'
            ws.cell(max(1, layout['max_row']), len(head))
            for column, width in json.loads(layout['widths']).items():
                ws.column_dimensions[column].width = width
            ws.freeze_panes = layout['freeze']
            if layout['validation'] and 'My status' in head:
                column = openpyxl.utils.get_column_letter(head.index('My status') + 1)
                dv = DataValidation(type='list', formula1='"' + ','.join(STATUSES) + '"', allow_blank=True)
                ws.add_data_validation(dv)
                dv.add(f'{column}2:{column}{max(200, ws.max_row)}')
        return wb

    @contextmanager
    def edit(self):
        """Keep validation, row edits, history and revision in one transaction."""
        wb = None
        try:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                wb = self.workbook()
                yield wb
                for ws in wb:
                    head = [c.value for c in ws[1]]
                    table = self.table('main', ws.title, head)
                    for number, values in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
                        if any(v is not None for v in values):
                            self.put_row(table, ws.title, number, head, values, history=True)
                    self.db.execute("UPDATE layouts SET max_row=? WHERE source='main' AND sheet=?",
                                    (ws.max_row, ws.title))
                self.set_meta('revision', int(self.meta('revision')) + 1)
                self.set_meta('mirror_stale', 1)
        finally:
            if wb is not None:
                wb.close()


def input_counts(paths):
    """Count source rows independently of the database representation."""
    result = {'sheets': {}, 'old': {}, 'contacts': 0, 'rows': 0}
    for source, path in paths:
        wb = openpyxl.load_workbook(path)
        try:
            for ws in wb:
                head = [c.value for c in ws[1]]
                counts = {}
                if source == 'main' and 'My status' in head:
                    result['sheets'][ws.title] = counts
                for number, values in enumerate(ws.iter_rows(values_only=True), 1):
                    if any(v is not None for v in values):
                        result['rows'] += 1
                    if number == 1:
                        continue
                    if 'My status' in head and len(values) > 1 and values[1]:
                        target = result['old'] if source == 'old' else counts
                        status = values[head.index('My status')]
                        target[status] = target.get(status, 0) + 1
                    elif source == 'main' and ws.title == 'Contacts' and values[0]:
                        result['contacts'] += 1
        finally:
            wb.close()
    return result


def migrate(xlsx, old=None, apply=False):
    xlsx = Path(xlsx)
    target = db_path(xlsx)
    if apply and target.exists():
        raise FileExistsError(f'Database already exists: {target}')
    paths = [('main', xlsx)] + ([('old', Path(old))] if old else [])
    before = input_counts(paths)
    backup, temporary, storage = None, None, None
    try:
        if apply:
            backup = str(copy_log(xlsx, 'before-sqlite'))
            if old:
                copy_log(old, 'before-sqlite')
            fd, temporary = tempfile.mkstemp(prefix=f'.{target.stem}-', suffix='.tmp', dir=target.parent)
            os.close(fd)
        storage = Store(temporary if apply else ':memory:')
        storage.create()
        with storage.db:
            for source, path in paths:
                storage.import_workbook(path, source)
            storage.set_meta('mirror_hash', digest(xlsx))
        after = storage.counts()
        ok = before == after
        report = {'ok': ok, 'db': str(target), 'backup': backup,
                  'sheets': {s: {'in': c, 'out': after['sheets'].get(s, {})}
                             for s, c in before['sheets'].items()},
                  'old': {'in': before['old'], 'out': after['old']} if old else None,
                  'contacts': {'in': before['contacts'], 'out': after['contacts']},
                  'rows_in': before['rows'], 'rows_out': after['rows']}
        storage.close()
        storage = None
        if apply:
            if not ok:
                raise RuntimeError('Migration counts differ; database was not installed.')
            os.rename(temporary, target)
        return report
    finally:
        if storage is not None:
            storage.close()
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def rebuild_mirror(xlsx):
    xlsx = Path(xlsx)
    storage = Store(db_path(xlsx))
    temporary, wb = None, None
    try:
        with storage.db:
            storage.set_meta('mirror_stale', 1)
        if xlsx.with_name('~$' + xlsx.name).exists():
            return
        if xlsx.exists() and digest(xlsx) != storage.meta('mirror_hash'):
            saved = copy_log(xlsx, 'edited')
            with storage.db:
                storage.set_meta('mirror_warning', "Your spreadsheet was changed in Excel. The site's data was kept; "
                                 f'your Excel copy is saved as {saved.name}.')
        wb = storage.workbook()
        fd, temporary = tempfile.mkstemp(prefix=f'.{xlsx.stem}-saving-', suffix='.xlsx', dir=xlsx.parent)
        os.close(fd)
        wb.save(temporary)
        check = openpyxl.load_workbook(temporary, read_only=True)
        try:
            if check.sheetnames != wb.sheetnames:
                raise RuntimeError('Mirror verification failed.')
        finally:
            check.close()
        os.replace(temporary, xlsx)
        with storage.db:
            storage.set_meta('mirror_hash', digest(xlsx))
            storage.set_meta('mirror_stale', 0)
    except PermissionError:
        # The database write already succeeded; a later save retries the mirror.
        pass
    finally:
        if wb is not None:
            wb.close()
        storage.close()
        if temporary:
            Path(temporary).unlink(missing_ok=True)
