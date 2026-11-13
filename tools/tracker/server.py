# Job Search HQ tracker: local server.
# Serves index.html using SQLite when present, otherwise the Excel workbook.
# Local only (127.0.0.1). Never applies anywhere.
#
# Env vars (optional, for testing against a copy):
#   TRACKER_XLSX  path to the workbook   (default: work/applications-tracker.xlsx in the data folder)
#   TRACKER_PORT  port to listen on      (default: 8765)
#
# Excel writes use a verified temporary file and seven daily backups. SQLite writes
# commit first, then rebuild Excel as a mirror, preserving external edits as copies.
import json, os, re, shutil, sqlite3, sys, threading, webbrowser
from copy import copy
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import openpyxl
import importlib.util
import unicodedata
from contextlib import contextmanager

# Shared Windows writer lock.
_lock_spec = importlib.util.spec_from_file_location('tracker_workbook_lock', Path(__file__).with_name('workbook_lock.py'))
_lock_module = importlib.util.module_from_spec(_lock_spec)
_lock_spec.loader.exec_module(_lock_module)
workbook_lock = _lock_module.workbook_lock
_store_spec = importlib.util.spec_from_file_location('tracker_store', Path(__file__).with_name('store.py'))
store = importlib.util.module_from_spec(_store_spec)
_store_spec.loader.exec_module(store)

HERE = Path(__file__).resolve().parent
# Render the same settings as the hosted page builder.
_settings_spec = importlib.util.spec_from_file_location('tracker_owner_settings', HERE.parent/'hq-dashboard'/'hq_settings.py')
hq_settings = importlib.util.module_from_spec(_settings_spec)
_settings_spec.loader.exec_module(hq_settings)
XLSX = Path(os.environ.get("TRACKER_XLSX") or hq_settings.data_root() / "work" / "applications-tracker.xlsx").resolve()
PORT = int(os.environ.get("TRACKER_PORT") or 8765)
BACKUPS = XLSX.parent / ".tracker-backups"
LOCK = threading.Lock()
# Same order as the data-validation list in the workbook.
_stage_spec = importlib.util.spec_from_file_location('tracker_stages', HERE / 'stages.py')
stages = importlib.util.module_from_spec(_stage_spec)
_stage_spec.loader.exec_module(stages)
STATUSES = stages.NAMES
ADD_FIELDS = ("Company", "Role", "Where", "Posted", "Score", "Verdict", "My status", "Date applied", "CV used", "Source", "Link", "Notes")


class Conflict(Exception):
    pass


def is_formula_text(value):
    """Check stripped text after skipping leading whitespace and control characters."""
    for char in value.strip():
        if char.isspace() or unicodedata.category(char) in ('Cc', 'Cf'):
            continue
        return char == '='
    return False


def cell(v):
    if v is None:
        return ""
    return v.isoformat()[:10] if hasattr(v, "isoformat") else str(v)


def header(ws):
    return [c.value for c in ws[1]]


CHECKED = re.compile(r"\s*(OPEN|CLOSED|REDIRECTED|UNKNOWN)", re.I)


def check_date(col):
    m = re.search(r"\d{4}-\d{2}-\d{2}", col)
    return m.group(0) if m else ""


def sqlite_mode():
    return store.db_path(XLSX).exists()


def load():
    job_rounds = {}
    job_dates = {}
    if sqlite_mode():
        storage = store.Store(store.db_path(XLSX))
        try:
            with storage.db:
                storage.db.execute('BEGIN')
                wb = storage.workbook()
                warning = storage.meta('mirror_warning') or ''
                revision = 'db-' + str(storage.meta('revision'))
                by_job = storage.rounds()
                job_dates = storage.dates()
                job_rounds = {(r['sheet'], r['row']): by_job.get(r['id'], [])
                              for r in storage.db.execute('SELECT id, sheet, row FROM jobs')}
        finally:
            storage.close()
        mode = 'sqlite'
    else:
        wb = openpyxl.load_workbook(XLSX)
        mode, warning, revision = 'excel', '', version()
    try:
        data = load_rows(wb, mode, warning, revision)
        for job in data['jobs']:
            job['_rounds'] = job_rounds.get((job['_sheet'], job['_row']), [])
            if mode == 'sqlite':
                dates = job_dates.get((job['_sheet'], job['_row']), {})
                first_seen, posted, source = (dates.get(k) or '' for k in ('first_seen', 'posted_on', 'date_source'))
                first_seen = first_seen or store.freshness.sheet_date(job['_sheet']) or ''
            else:
                posted, source = store.freshness.parse_posted(job.get('Posted'))
                first_seen = store.freshness.sheet_date(job['_sheet']) or ''
            job.update(_first_seen=first_seen, _posted_on=posted or '', _date_source=source,
                       _age=store.freshness.age_days(posted, first_seen),
                       _about=store.freshness.is_about(source, job.get('Posted')))
        return data
    finally:
        wb.close()


def load_rows(wb, mode, warning, revision):
    jobs, contacts, sheets = [], [], []
    for ws in wb:
        if ws.title == 'Old':
            continue
        head = header(ws)
        if "My status" in head:
            # Newest dated "Open check" column with a value in the row.
            checks = sorted((str(h) for h in head if h and str(h).startswith("Open check")), key=check_date, reverse=True)
            sheets.append({"name": ws.title, "columns": [str(h) for h in head if h]})
            for n, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                if len(row) < 2 or not row[1]:
                    continue
                d = {str(h): cell(v) for h, v in zip(head, row) if h}
                d["_sheet"], d["_row"] = ws.title, n
                check_col = next((h for h in checks if CHECKED.match(d.get(h, ""))), None) or next((h for h in checks if d.get(h)), checks[0] if checks else "")
                d["_check"] = d.get(check_col, "") if check_col else ""
                d["_checkdate"] = check_date(check_col)
                jobs.append(d)
        elif ws.title == "Contacts":
            for n, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                if row and row[0]:
                    d = {str(h): cell(v) for h, v in zip(head, row) if h}
                    d["_row"] = n
                    contacts.append(d)
    return {"jobs": jobs, "contacts": contacts, "sheets": sheets, "statuses": STATUSES,
            "today": date.today().isoformat(), "version": revision, "file": XLSX.name,
            "store": mode, "mirrorWarning": warning}


_CACHE = {"version": None, "today": None, "body": ""}


def cached():
    """The workbook is parsed once per file version and calendar day (openpyxl is slow)."""
    v = version()
    today = date.today().isoformat()
    if v != _CACHE["version"] or today != _CACHE["today"] or not _CACHE["body"]:
        _CACHE["body"], _CACHE["version"], _CACHE["today"] = load(), v, today
    d = dict(_CACHE["body"], today=today, version=v)
    return json.dumps(d)


def version():
    if sqlite_mode():
        storage = store.Store(store.db_path(XLSX))
        try:
            return 'db-' + str(storage.meta('revision'))
        finally:
            storage.close()
    try:
        return str(XLSX.stat().st_mtime_ns)
    except OSError:
        return ""


def excel_open():
    return (XLSX.parent / ("~$" + XLSX.name)).exists()


def backup():
    BACKUPS.mkdir(exist_ok=True)
    today = BACKUPS / f"{XLSX.stem}-{date.today().isoformat()}{XLSX.suffix}"
    if not today.exists():
        shutil.copy2(XLSX, today)
    olds = sorted(BACKUPS.glob(f"{XLSX.stem}-*{XLSX.suffix}"))
    for old in olds[:-7]:
        old.unlink(missing_ok=True)


def backup_database(storage):
    BACKUPS.mkdir(exist_ok=True)
    today = BACKUPS / f"{XLSX.stem}-{date.today().isoformat()}.sqlite3"
    if not today.exists():
        target = sqlite3.connect(today)
        try:
            storage.db.backup(target)
        finally:
            target.close()
    olds = sorted(BACKUPS.glob(f"{XLSX.stem}-*.sqlite3"))
    for old in olds[:-7]:
        old.unlink(missing_ok=True)


def save(wb):
    """Save to a temp file, verify it opens with the same sheets, then atomically replace the original."""
    if excel_open():
        raise PermissionError("open in Excel")
    tmp = XLSX.with_name(f".{XLSX.stem}.saving-{os.getpid()}{XLSX.suffix}")
    try:
        wb.save(tmp)
        check = openpyxl.load_workbook(tmp, read_only=True)
        same = check.sheetnames == wb.sheetnames
        check.close()
        if not same:
            raise RuntimeError("verification failed; workbook left unchanged")
        backup()
        os.replace(tmp, XLSX)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def expect_row(ws, head, row, p):
    """Guard against the daily rerun having reordered rows since the page loaded."""
    exp = p.get("expect") or {}
    for k, v in exp.items():
        if k in head and cell(ws.cell(row, head.index(k) + 1).value) != str(v):
            raise Conflict("The spreadsheet changed since this page loaded. Refresh and try again.")


@contextmanager
def editing_workbook():
    if sqlite_mode():
        storage = store.Store(store.db_path(XLSX))
        try:
            backup_database(storage)
            with storage.edit() as wb:
                yield wb
        finally:
            storage.close()
        store.rebuild_mirror(XLSX)
    else:
        wb = openpyxl.load_workbook(XLSX)
        try:
            yield wb
            save(wb)
        finally:
            wb.close()


def update(p):
    with editing_workbook() as wb:
        update_row(p, wb)


def rounds(p):
    if not sqlite_mode():
        raise ValueError('Interview rounds are saved once the tracker uses the database.')
    if p.get('action') not in ('add', 'edit'):
        raise ValueError('Unknown round action.')
    storage = store.Store(store.db_path(XLSX))
    wb = None
    try:
        backup_database(storage)
        with storage.db:
            storage.db.execute('BEGIN IMMEDIATE')
            wb = storage.workbook()
            sheet, row = p.get('sheet'), p.get('row')
            if sheet not in wb.sheetnames:
                raise ValueError('unknown sheet')
            try:
                row = int(row)
            except (ValueError, TypeError):
                raise ValueError('bad row')
            ws = wb[sheet]
            head = header(ws)
            if 'My status' not in head:
                raise ValueError('not a job sheet')
            if row < 2 or row > ws.max_row:
                raise ValueError('bad row')
            expect_row(ws, head, row, p)
            job = storage.db.execute('SELECT id, status, company FROM jobs WHERE sheet=? AND row=?',
                                     (sheet, row)).fetchone()
            if not job or not job['company']:
                raise ValueError('bad row')
            values = {'date': date.today().isoformat(), 'type': '', 'note': ''}
            if p['action'] == 'add':
                if job['status'] != 'Interview':
                    raise ValueError('Add a round while the job is in Interview.')
            else:
                old = storage.db.execute('SELECT date, type, note FROM interview_rounds WHERE id=? AND job_id=?',
                                         (p.get('id'), job['id'])).fetchone()
                if old is None:
                    raise ValueError('Unknown round for this job.')
                values = dict(old)
            for field, limit in (('date', 10), ('type', 60), ('note', 280)):
                if field not in p:
                    continue
                value = p[field]
                if not isinstance(value, str) or len(value) > limit or is_formula_text(value):
                    raise ValueError(f'Invalid round {field}.')
                if field == 'date' and value:
                    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                        raise ValueError('date must be YYYY-MM-DD')
                    date.fromisoformat(value)
                values[field] = value
            if p['action'] == 'add':
                storage.add_round(job['id'], values['date'] or date.today().isoformat(), values['type'], values['note'])
            else:
                storage.edit_round(p.get('id'), job['id'], values['date'], values['type'], values['note'])
            storage.set_meta('revision', int(storage.meta('revision')) + 1)
            storage.set_meta('mirror_stale', 1)
    finally:
        if wb is not None:
            wb.close()
        storage.close()
    store.rebuild_mirror(XLSX)


def update_row(p, wb):
    sheet, row = p["sheet"], int(p["row"])
    if sheet not in wb.sheetnames:
        raise ValueError("unknown sheet")
    ws = wb[sheet]
    head = header(ws)
    if row < 2 or row > ws.max_row:
        raise ValueError("bad row")
    expect_row(ws, head, row, p)
    if sheet == "Contacts":
        col = {"status": "Status", "date": "Date sent", "note": "Notes"}
    else:
        if "My status" not in head:
            raise ValueError("not a job sheet")
        col = {"status": "My status", "date": "Date applied", "note": "Notes"}
        if "status" in p and p["status"] != ws.cell(row, head.index("My status") + 1).value and not stages.allowed(ws.cell(row, head.index("My status") + 1).value, p["status"]):
            raise ValueError("bad status")
    if "date" in p and p["date"] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(p["date"])):
        raise ValueError("date must be YYYY-MM-DD")
    for key in ("status", "date", "note"):
        if key in p:
            if col[key] not in head:
                raise ValueError(f"column {col[key]} missing")
            val = p[key]
            if key == "date" and val:
                val = date.fromisoformat(val)
            # assign .value directly: ws.cell(r, c, None) silently ignores None, so clearing would not work
            dst = ws.cell(row, head.index(col[key]) + 1)
            dst.value = val if val not in ("", None) else None
            if isinstance(val, str) and is_formula_text(val):
                dst.data_type = "s"


def add(p):
    with editing_workbook() as wb:
        result = add_row(p, wb)
    return result


def add_row(p, wb):
    sheet = p["sheet"]
    if sheet not in wb.sheetnames:
        raise ValueError("unknown sheet")
    ws = wb[sheet]
    head = header(ws)
    if "My status" not in head:
        raise ValueError("not a job sheet")
    f = {k: str(p.get(k) or "").strip() for k in ADD_FIELDS}
    if not f["Company"] or not f["Role"]:
        raise ValueError("Company and role are required")
    f["My status"] = f["My status"] or stages.NEW_ROW
    if f["My status"] not in STATUSES:
        raise ValueError("bad status")
    if f["Link"] and not re.match(r"https?://", f["Link"]):
        raise ValueError("Link must start with http:// or https://")
    ci = head.index("Company") + 1
    last = 1
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, ci).value not in (None, ""):
            last = r
    new = last + 1
    # next "#" id, keeping the sheet's prefix (1, 2.. or N1, N2.. or E1, E2..)
    if "#" in head:
        hi, prefix, numeric = 0, "", True
        for r in range(2, last + 1):
            m = re.fullmatch(r"(\D*)(\d+)", str(ws.cell(r, head.index("#") + 1).value or ""))
            if m:
                prefix, numeric = m.group(1), isinstance(ws.cell(r, head.index("#") + 1).value, int)
                hi = max(hi, int(m.group(2)))
        f["#"] = hi + 1 if numeric and not prefix else f"{prefix}{hi + 1}"
    for c, h in enumerate(head, start=1):
        if not h:
            continue
        dst = ws.cell(new, c)
        if last >= 2:
            src = ws.cell(last, c)
            if src.has_style:
                dst._style = copy(src._style)
        v = f.get(str(h), "")
        if v and h == "Date applied":
            v = date.fromisoformat(v)
        if v not in ("", None):
            dst.value = v
            if isinstance(v, str) and is_formula_text(v):
                dst.data_type = "s"
    if last >= 2 and ws.row_dimensions[last].height:
        ws.row_dimensions[new].height = ws.row_dimensions[last].height
    sc = openpyxl.utils.get_column_letter(head.index("My status") + 1)
    for dv in ws.data_validations.dataValidation:
        if any(rng.min_col == rng.max_col == head.index("My status") + 1 for rng in dv.sqref.ranges):
            dv.sqref.add(f"{sc}{new}")
    saved = {str(h): cell(ws.cell(new, c).value) for c, h in enumerate(head, start=1) if h}
    return new, saved


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def local(self):
        if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):
            return False
        origin = self.headers.get("Origin")
        return not origin or re.fullmatch(r"https?://(127\.0\.0\.1|localhost)(:\d+)?", origin) is not None

    def do_GET(self):
        if not self.local():
            return self.send(404, "not found", "text/plain")
        path = self.path.split("?")[0]
        try:
            if path == "/api/data":
                with LOCK:
                    self.send(200, cached(), "application/json")
            elif path == "/api/version":
                self.send(200, json.dumps({"version": version()}), "application/json")
            elif path in ("/", "/index.html"):
                page = (HERE / "index.html").read_text(encoding="utf-8")
                page = page.replace("/*{{STAGES_JSON}}*/null", (HERE / "stages.json").read_text(encoding="utf-8").replace("<", "\\u003c"))
                self.send(200, hq_settings.render_page(page).encode("utf-8"), "text/html; charset=utf-8")
            else:
                self.send(404, "not found", "text/plain")
        except PermissionError:
            self.send(409, json.dumps({"error": f"Can't read {XLSX.name} right now. Try again in a moment."}), "application/json")
        except Exception as e:
            self.send(500, json.dumps({"error": str(e)}), "application/json")

    def do_POST(self):
        path = self.path.split("?")[0]
        if path not in ("/api/update", "/api/add", "/api/rounds") or not self.local():
            return self.send(404, "not found", "text/plain")
        try:
            p = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            with LOCK, workbook_lock(XLSX):
                if path in ("/api/update", "/api/rounds"):
                    (rounds if path == "/api/rounds" else update)(p)
                    out = {"ok": True}
                else:
                    row, saved = add(p)
                    out = {"ok": True, "row": row, "saved": saved}
                out["version"] = version()
            self.send(200, json.dumps(out), "application/json")
        except PermissionError:
            self.send(409, json.dumps({"error": f"Close {XLSX.name} in Excel and try again"}), "application/json")
        except Conflict as e:
            self.send(409, json.dumps({"error": str(e)}), "application/json")
        except Exception as e:
            self.send(400, json.dumps({"error": str(e)}), "application/json")


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    try:
        with LOCK:
            cached()  # warm the cache so the first page load is fast
    except Exception as e:
        print("Warning: could not read the workbook yet:", e)
    print(f"Tracker at http://127.0.0.1:{PORT}  ->  {XLSX}  (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        webbrowser.open(f"http://127.0.0.1:{PORT}")
    srv.serve_forever()
