"""Durable, single-edit workbook queue.

A pending edit blocks another edit until saved/cancelled/resolved. Each edit keeps
its original workbook version. Changed workbooks are never silently rebased.
An interrupted 'applying' operation needs a human check instead of a blind retry.
"""
from contextlib import closing
import hashlib
import json
import sqlite3
import threading
import time
import uuid


class WriteQueue:
    def __init__(self, state, apply):
        self.path = state / 'pending-writes.sqlite3'
        self.apply = apply
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS edits (
                id TEXT PRIMARY KEY, signature TEXT NOT NULL, path TEXT NOT NULL,
                payload TEXT NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL,
                message TEXT NOT NULL, result TEXT, created REAL NOT NULL, updated REAL NOT NULL)''')
            db.execute("UPDATE edits SET status='needs_check', message=?, updated=? WHERE status='applying'",
                       ('HQ stopped during this save. Check the spreadsheet before making this edit again.', time.time()))

    def connect(self):
        return closing(sqlite3.connect(self.path))

    def submit(self, path, payload):
        request_id = payload.get('requestId')
        try:
            if str(uuid.UUID(request_id)) != request_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Reload HQ before saving this edit.')
        body = json.dumps({k: v for k, v in payload.items() if k != 'requestId'}, sort_keys=True)
        signature = hashlib.sha256((path + body).encode()).hexdigest()
        expected = payload.get('expect') or {}
        label = str(payload.get('Company') or expected.get('Company') or expected.get('Person') or 'Spreadsheet edit')[:200]
        with self.lock, self.connect() as db, db:
            old = db.execute('SELECT signature FROM edits WHERE id=?', (request_id,)).fetchone()
            if old:
                if old[0] != signature:
                    raise ValueError('This save request was already used for a different edit.')
            else:
                active = db.execute("SELECT id FROM edits WHERE status IN ('waiting','applying','needs_check','conflict') LIMIT 1").fetchone()
                if active:
                    raise ValueError('An edit is waiting. Check the save message at the top of HQ first.')
                now = time.time()
                db.execute('INSERT INTO edits VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (request_id, signature, path, body, label, 'waiting', 'Waiting to save to your spreadsheet.', None, now, now))
        self.process_one()
        return self.get(request_id)

    def get(self, request_id):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT id,label,status,message,result,created,updated FROM edits WHERE id=?', (request_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown save request.')
        out = dict(row)
        out['result'] = json.loads(out['result']) if out['result'] else None
        return out

    def recent(self):
        with self.connect() as db:
            rows = db.execute('SELECT id FROM edits ORDER BY created DESC LIMIT 20').fetchall()
        return [self.get(row[0]) for row in rows]

    def dismiss(self, request_id):
        with self.lock, self.connect() as db, db:
            row = db.execute('SELECT status FROM edits WHERE id=?', (request_id,)).fetchone()
            if not row or row[0] not in ('waiting', 'conflict', 'needs_check', 'failed'):
                raise ValueError('This edit cannot be dismissed right now.')
            db.execute("UPDATE edits SET status='cancelled',message=?,updated=? WHERE id=?",
                       ('Removed from the queue. Check the spreadsheet before entering another edit.', time.time(), request_id))
        return self.get(request_id)

    def process_one(self):
        with self.lock:
            with self.connect() as db, db:
                row = db.execute("SELECT id,path,payload FROM edits WHERE status='waiting' ORDER BY created LIMIT 1").fetchone()
                if not row:
                    return
                request_id, path, body = row
                db.execute("UPDATE edits SET status='applying',updated=? WHERE id=?", (time.time(), request_id))
            result = None
            try:
                result = self.apply(path, json.loads(body))
                status, message = 'saved', 'Saved to your spreadsheet.'
            except PermissionError:
                status, message = 'waiting', 'Waiting for Excel or the job scan. Close Excel if it is open; HQ will retry.'
            except Exception as e:
                # Caller tags optimistic-concurrency conflicts without importing its module.
                if getattr(e, 'hq_conflict', False):
                    status, message = 'conflict', 'The spreadsheet changed. Reload, check the entry, then enter your edit again.'
                elif isinstance(e, (ValueError, KeyError, TypeError)):
                    status, message = 'failed', 'This edit has invalid fields. Check it before trying again.'
                    if path == '/api/rounds' and isinstance(e, ValueError):
                        message = str(e)
                else:
                    # A failure after an atomic workbook replacement can be ambiguous.
                    status, message = 'needs_check', 'This save could not be confirmed. Check the spreadsheet before trying again.'
            with self.connect() as db, db:
                db.execute('UPDATE edits SET status=?,message=?,result=?,updated=? WHERE id=?',
                           (status, message, json.dumps(result) if result else None, time.time(), request_id))

    def start(self):
        def run():
            while not self.stop_event.wait(5):
                try:
                    self.process_one()
                except sqlite3.Error:
                    # Keep requests durable; next pass retries reading the journal.
                    continue
        self.thread = threading.Thread(target=run, name='hq-workbook-queue', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=20)
