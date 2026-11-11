# Moves edits between the hosted Job HQ page and the tracker workbook.
# The headless run lists the page's collections with ArtifactData out_dir, this script does the matching, and the run
# uploads the manifests it writes. Rows, styles, dropdowns, real dates, clearing and "#" ids come from tools/tracker/server.py.
#   prep DIR  empties DIR (only under work/hq-seed). Run it before listing, so no old doc file is read twice.
#   pull DIR  page edits into the xlsx. Reads DIR/jobs, DIR/contacts, DIR/requests. Writes DIR/pull-batch-N.json.
#   push DIR  xlsx into the page, after the profile builder. Reads DIR/jobs, contacts, meta, profile, requests.
#             Writes DIR/push-batch-N.json, then DIR/push-cleanup-N.json (deletes): upload them in that order.
# A doc file may be the plain doc or a {id, version, data} wrapper. Any entry with "if_version": null needs the version
# from the list result. Exit 0 = fine, 1 = finished with items to look at, 2 = stopped, nothing to upload.
# Run: python -I tools/hq-dashboard/sync_page.py pull work/hq-seed/sync/in
import hashlib, importlib.util, json, os, re, shutil, sys
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

_settings_spec = importlib.util.spec_from_file_location('sync_owner_settings', Path(__file__).with_name('hq_settings.py'))
hq_settings = importlib.util.module_from_spec(_settings_spec)
_settings_spec.loader.exec_module(hq_settings)
ROOT = hq_settings.data_root()
APP = hq_settings.app_root()
SEED = ROOT / "work" / "hq-seed"
_spec = importlib.util.spec_from_file_location("tracker_server", APP / "tools" / "tracker" / "server.py")
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

MAX_ENTRIES, MAX_BYTES, MAX_NOTE = 50, 900_000, 32767
STALE = timedelta(hours=12)   # a request still "running" after this was left behind by a run that stopped
MAX_TRIES = 3
JOB_ALWAYS = ("check", "checkNote", "checkDate", "score", "verdict", "posted", "source", "row")  # "#" stays as first seeded: 122 writes for a label
JOB_STATUS = ("status", "dateApplied")
JOB_NO_FLAG = ("cvUsed", "link", "role", "where")
CON_ALWAYS = ("whereNow", "howKnown", "reply", "notes", "row")
CON_STATUS = ("status", "dateSent")
TEXT = set()   # strings this run writes; a save must keep them as text even when they start with "="


class Stop(Exception):
    pass


def keep_text_save(save):
    def wrapped(wb):
        for ws in wb:
            for row in ws.iter_rows():
                for c in row:
                    if c.data_type == "f" and c.value in TEXT:
                        c.data_type = "s"
        save(wb)
    return wrapped


srv.save = keep_text_save(srv.save)


def clean(v):
    s = str(v if v is not None else "").replace("\x0b", "\n").replace("\x0c", "\n")
    return re.sub("[\ud800-\udfff]", "", ILLEGAL_CHARACTERS_RE.sub("", s))


def norm(v):
    return re.sub(r"\s+", " ", clean(srv.cell(v))).strip().casefold()


def val(v):
    return "" if v is None else str(v)


def short(i):
    return hashlib.sha1(str(i).encode("utf-8")).hexdigest()[:6]


def read_doc(f):
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Stop(f"{f} can't be read: {e}")
    if not isinstance(raw, dict):
        raise Stop(f"{f} is not a JSON object")
    if isinstance(raw.get("data"), dict) and ("version" in raw or "id" in raw):
        d, v, i = dict(raw["data"]), raw.get("version"), raw.get("id") or f.stem
    else:
        d, v, i = dict(raw), raw.get("version"), f.stem
    d["_id"], d["_v"] = str(i), v
    return d


def load(folder, name, required=True):
    p = folder / name
    files = sorted(p.glob("*.json")) if p.is_dir() else []
    if not files and required:
        raise Stop(f"nothing in {p}: list collection {name!r} there with out_dir {folder} first")
    return {d["_id"]: d for d in map(read_doc, files)}


def once(folder, step):
    mark = folder / f".{step}-done"
    if mark.exists():
        raise Stop(f"{folder} was already used for {step}. Run prep, list again, then {step}.")
    return mark


def entry(op, collection, doc_id, version=None, **kw):
    e = {"op": op, "collection": collection, "doc_id": doc_id, **kw}
    if version is not None or op != "set":
        e["if_version"] = version
    return e


def patch(folder, collection, doc_id, version, data):
    f = folder / "patch" / f"{collection}-{doc_id}.json"
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return entry("update", collection, doc_id, version, file_path=str(f))


def size(e):
    return Path(e["file_path"]).stat().st_size if "file_path" in e else len(json.dumps(e))


def manifests(folder, prefix, entries):
    for old in folder.glob(f"{prefix}-*.json"):
        old.unlink()
    out, cur, n = [], [], 0
    for e in entries:
        if cur and (len(cur) >= MAX_ENTRIES or n + size(e) > MAX_BYTES):
            out.append(cur)
            cur, n = [], 0
        cur.append(e)
        n += size(e)
    if cur:
        out.append(cur)
    names = []
    for k, b in enumerate(out):
        f = folder / f"{prefix}-{k}.json"
        f.write_text(json.dumps(b, ensure_ascii=False, indent=0), encoding="utf-8")
        names.append(f.name)
    return names


def sheet_rows(sheet):
    wb = openpyxl.load_workbook(srv.XLSX)
    if sheet not in wb.sheetnames:
        return None
    ws = wb[sheet]
    head = srv.header(ws)
    idx = {h: head.index(h) + 1 for h in ("Company", "Role", "Link", "Person") if h in head}
    return [{k: norm(ws.cell(r, c).value) for k, c in idx.items()} | {"row": r} for r in range(2, ws.max_row + 1)]


def locate(sheet, doc, keys):
    # The row the doc names, if it still holds this lead; else the only row that does. None when there is no such row.
    rows = sheet_rows(sheet)
    if rows is None:
        return None, "sheet gone"
    want = {k: norm(doc[f]) for k, f in keys.items()}
    # Two postings can share company and role: when both sides have a link, the link must agree too.
    link = norm(doc.get("link"))
    same = lambda r: all(r.get(k) == v for k, v in want.items()) and (not link or not r.get("Link") or r["Link"] == link)
    at = next((r for r in rows if str(r["row"]) == str(doc.get("row"))), None)
    if at and same(at):
        return at["row"], ""
    hits = [r["row"] for r in rows if same(r)]
    if len(hits) == 1:
        return hits[0], "moved"
    return None, "several rows match" if hits else "no matching row"


def write(p):
    TEXT.update(v for v in p.values() if isinstance(v, str))
    return srv.update(p)


def flags_off(doc, *names):
    return {n: {"__delete__": True} for n in names if doc.get(n)}


def apply_job_edits(doc, row, sheet):
    p = {"sheet": sheet, "row": row}
    if doc.get("statusChangedInPage"):
        p["status"], p["date"] = clean(doc.get("status")), clean(doc.get("dateApplied"))
    if doc.get("notesChangedInPage"):
        p["note"] = clean(doc.get("notes"))
    if len(p) > 2:
        write(p)
    return len(p.get("note", "")) > MAX_NOTE


def pull_added(doc, say, problems):
    sheet = clean(doc.get("sheet"))
    rows = sheet_rows(sheet)
    if rows is None:
        problems.append(f"Page-added {doc.get('company')} / {doc.get('role')}: sheet {sheet!r} no longer exists. Doc left on the page; add it again on another sheet.")
        return False
    c, r, l = norm(doc.get("company")), norm(doc.get("role")), norm(doc.get("link"))
    hit = next((x for x in rows if x.get("Company") == c and x.get("Role") == r and (not l or not x.get("Link") or x["Link"] == l)), None)
    if hit:
        apply_job_edits(doc, hit["row"], sheet)
        say.append(f"Page-added {doc.get('company')} / {doc.get('role')}: already in {sheet} row {hit['row']}, not added again.")
        return True
    p = {"sheet": sheet, "Company": clean(doc.get("company")), "Role": clean(doc.get("role")), "Where": clean(doc.get("where")), "Posted": clean(doc.get("posted")),
         "Score": clean(doc.get("score")), "Verdict": clean(doc.get("verdict")), "My status": clean(doc.get("status")), "Date applied": clean(doc.get("dateApplied")),
         "CV used": clean(doc.get("cvUsed")), "Source": clean(doc.get("source")), "Link": clean(doc.get("link")), "Notes": clean(doc.get("notes"))}
    TEXT.update(v.strip() for v in p.values())
    row, _ = srv.add(p)
    say.append(f"Added {p['Company']} / {p['Role']} to {sheet} row {row}.")
    return True


def pull_requests(reqs, writes, say):
    now, picked = datetime.now(), []
    for d in reqs.values():
        st = d.get("status") or "queued"
        tries = int(d.get("tries") or 0)
        if st == "running":
            try:
                fresh = now - datetime.fromisoformat(str(d.get("runningAt"))) < STALE
            except ValueError:
                fresh = False
            if fresh:
                continue
            tries = max(tries, 1)
            if tries >= MAX_TRIES:
                writes.append(entry("update", "requests", d["_id"], d["_v"], data={"status": "failed", "result": f"The daily search stopped before finishing this {tries} times. Add it again."}))
                say.append(f"Request {d['_id']}: failed after {tries} stopped runs.")
                continue
            say.append(f"Request {d['_id']}: left running by a run that stopped, running it again.")
        elif st != "queued":
            continue
        writes.append(entry("update", "requests", d["_id"], d["_v"], data={"status": "running", "runningAt": now.isoformat(timespec="seconds"), "tries": tries + 1}))
        picked.append(d)
    return picked


def pull(folder):
    mark = once(folder, "pull")
    jobs, contacts, reqs = load(folder, "jobs"), load(folder, "contacts"), load(folder, "requests", required=False)
    writes, say, problems = [], [], []
    picked = pull_requests(reqs, writes, say)
    done = {"added": 0, "edited": 0, "contacts": 0}
    lock = srv.XLSX.parent / ("~$" + srv.XLSX.name)
    wait = sum(bool(d.get("addedInPage") or d.get("statusChangedInPage") or d.get("notesChangedInPage")) for d in jobs.values()) + sum(bool(d.get("changedInPage")) for d in contacts.values())
    if srv.excel_open():
        old = datetime.now() - datetime.fromtimestamp(lock.stat().st_mtime) > timedelta(days=1)
        problems.append(f"The workbook is open in Excel: {wait} page edit(s) wait for tomorrow, flags left set. Skip the xlsx writes in step 3."
                        + (f" The lock file {lock.name} is over a day old: if Excel is closed, delete it." if old else ""))
    else:
        try:
            for d in sorted(jobs.values(), key=lambda x: str(x.get("createdAt", ""))):
                off = flags_off(d, "statusChangedInPage", "notesChangedInPage")
                try:
                    if d.get("addedInPage"):
                        if pull_added(d, say, problems):
                            done["added"] += 1
                            if off:
                                writes.append(entry("update", "jobs", d["_id"], d["_v"], data=off))
                    elif off:
                        sheet = clean(d.get("sheet"))
                        row, why = locate(sheet, d, {"Company": "company", "Role": "role"})
                        if row is None:
                            problems.append(f"{d.get('company')} / {d.get('role')} ({d['_id']}): {why}. Page edit not written, flag left set.")
                            continue
                        if why:
                            say.append(f"{d.get('company')} / {d.get('role')} moved to {sheet} row {row}.")
                        if apply_job_edits(d, row, sheet):
                            problems.append(f"{d.get('company')}: note is over {MAX_NOTE} characters; the spreadsheet keeps the first {MAX_NOTE}.")
                        writes.append(entry("update", "jobs", d["_id"], d["_v"], data=off))
                        done["edited"] += 1
                except (ValueError, KeyError, srv.Conflict) as e:
                    problems.append(f"{d.get('company')} / {d.get('role')} ({d['_id']}): {e}. Flag left set.")
            for d in contacts.values():
                if not d.get("changedInPage"):
                    continue
                try:
                    row, why = locate("Contacts", d, {"Person": "person"})
                    if row is None:
                        problems.append(f"Contact {d.get('person')} ({d['_id']}): {why}. Page edit not written, flag left set.")
                        continue
                    if why:
                        say.append(f"Contact {d.get('person')} moved to row {row}.")
                    write({"sheet": "Contacts", "row": row, "status": clean(d.get("status")), "date": clean(d.get("dateSent"))})
                    writes.append(entry("update", "contacts", d["_id"], d["_v"], data=flags_off(d, "changedInPage")))
                    done["contacts"] += 1
                except (ValueError, KeyError, srv.Conflict) as e:
                    problems.append(f"Contact {d.get('person')} ({d['_id']}): {e}. Flag left set.")
        except PermissionError:
            problems.append("The workbook can't be saved (open in another program?). Remaining page edits wait for tomorrow, flags left set. Skip the xlsx writes in step 3.")
    names = manifests(folder, "pull-batch", writes)
    mark.write_text(datetime.now().isoformat(), encoding="utf-8")
    print(f"pull: {done['added']} lead(s) added, {done['edited']} job edit(s), {done['contacts']} contact edit(s); {len(writes)} doc update(s) to upload in {names or 'no manifest'}.")
    for s in say:
        print(" -", s)
    for d in picked:
        print(f" - Search request {d['_id']}: what={d.get('what')!r} where={d.get('where')!r} level={d.get('level')!r} notes={d.get('notes')!r}")
    for s in problems:
        print(" ! ", s)
    return 1 if problems else 0


def pair(seeds, docs, key):
    # Doc ids hold the row a lead had when it was created, so match on the lead itself. Returns [(seed id, doc id)], free seeds, free docs.
    pairs, free_s, free_d = [], dict(seeds), dict(docs)
    for i in list(free_s):
        if i in free_d and key(free_s[i]) == key(free_d[i]):
            pairs.append((i, i))
            del free_s[i], free_d[i]
    by_d = {}
    for i, d in free_d.items():
        by_d.setdefault(key(d), []).append(i)
    by_s = {}
    for i, s in free_s.items():
        by_s.setdefault(key(s), []).append(i)
    for k, ids in by_s.items():
        if len(ids) == 1 and len(by_d.get(k, [])) == 1:
            pairs.append((ids[0], by_d[k][0]))
            del free_s[ids[0]], free_d[by_d[k][0]]
    return pairs, free_s, free_d


def diff(seed, doc, fields):
    return {k: seed.get(k, "") for k in fields if val(seed.get(k, "")) != val(doc.get(k, ""))}


def job_patch(s, d):
    held_s, held_n = d.get("statusChangedInPage"), d.get("notesChangedInPage")
    fields = list(JOB_ALWAYS)
    if not held_s:
        fields += JOB_STATUS
    if not (held_s or held_n):
        fields += JOB_NO_FLAG
    p = diff(s, d, fields)
    long_note = len(val(d.get("notes"))) > MAX_NOTE and val(d.get("notes")).startswith(val(s.get("notes")))
    if not held_n and not long_note and val(s.get("notes")) != val(d.get("notes")):
        p["notes"] = s.get("notes", "")
    return p


def seeds_of(prefix):
    out = {f.stem[len(prefix):]: f for f in SEED.glob(f"{prefix}*.json")}
    return out, {i: json.loads(f.read_text(encoding="utf-8")) for i, f in out.items()}


def free_id(i, taken, s):
    return i if i not in taken else f"{i}-{short(json.dumps(s, sort_keys=True))}"


def push(folder):
    mark = once(folder, "push")
    jobs, contacts = load(folder, "jobs"), load(folder, "contacts")
    meta, profile, reqs = load(folder, "meta"), load(folder, "profile"), load(folder, "requests", required=False)
    jfiles, jseed = seeds_of("job-")
    cfiles, cseed = seeds_of("contact-")
    main = SEED / "meta-main.json"
    if not jseed or not main.exists():
        raise Stop("no seed files: run the profile builder first")
    if main.stat().st_mtime < srv.XLSX.stat().st_mtime:
        raise Stop("the seed is older than the workbook: run the profile builder, and stop if it fails")
    writes, cleanup, say, problems = [], [], [], []
    added = {i: d for i, d in jobs.items() if d.get("addedInPage")}
    rows = {i: d for i, d in jobs.items() if i not in added}
    # Same doc id, same sheet and company, role edited in the xlsx: one lead, keep it paired.
    key = lambda x: (norm(x.get("sheet")), norm(x.get("company")), norm(x.get("role")), norm(x.get("link")))
    pairs, free_s, free_d = pair(jseed, rows, key)
    for i in [i for i in free_s if i in free_d and key(free_s[i])[:2] == key(free_d[i])[:2]]:
        pairs.append((i, i))
        del free_s[i], free_d[i]
    moved = 0
    for si, di in pairs:
        s, d = jseed[si], rows[di]
        if val(s.get("row")) != val(d.get("row")):
            moved += 1
            say.append(f"{s.get('company')} / {s.get('role')} moved from row {d.get('row')} to {s.get('row')} on {s.get('sheet')}.")
        p = job_patch(s, d)
        if p:
            writes.append(patch(folder, "jobs", di, d["_v"], p))
    created = {}
    taken = set(jobs)
    for i, s in free_s.items():
        n = free_id(i, taken, s)
        taken.add(n)
        created[n] = s
        writes.append(entry("set", "jobs", n, file_path=str(jfiles[i])))
    seed_list = list(jseed.values())
    for i, d in added.items():
        same = [s for s in seed_list if key(s) == key(d) and (not d.get("link") or not s.get("link") or norm(s["link"]) == norm(d["link"]))]
        if not same:
            problems.append(f"Page-added {d.get('company')} / {d.get('role')} has no row in the xlsx yet: doc kept for the next pull.")
        elif d.get("statusChangedInPage") or d.get("notesChangedInPage"):
            say.append(f"Page-added {d.get('company')} / {d.get('role')} was edited during the run: doc kept for the next pull.")
        else:
            cleanup.append(entry("delete", "jobs", i, d["_v"]))
    for d in free_d.values():
        problems.append(f"{d.get('company')} / {d.get('role')} ({d['_id']}) has no row in the xlsx: doc left alone.")
    ckey = lambda x: norm(x.get("person"))
    cp, cfree_s, cfree_d = pair(cseed, contacts, ckey)
    for si, di in cp:
        s, d = cseed[si], contacts[di]
        if val(s.get("row")) != val(d.get("row")):
            moved += 1
            say.append(f"Contact {s.get('person')} moved from row {d.get('row')} to {s.get('row')}.")
        p = diff(s, d, CON_ALWAYS + (() if d.get("changedInPage") else CON_STATUS))
        if p:
            writes.append(patch(folder, "contacts", di, d["_v"], p))
    ctaken = set(contacts)
    for i, s in cfree_s.items():
        n = free_id(i, ctaken, s)
        ctaken.add(n)
        writes.append(entry("set", "contacts", n, file_path=str(cfiles[i])))
    for d in cfree_d.values():
        problems.append(f"Contact {d.get('person')} ({d['_id']}) has no row in the xlsx: doc left alone.")
    m = json.loads(main.read_text(encoding="utf-8"))
    cur = meta.get("main")
    if not cur or cur.get("sheets") != m.get("sheets"):
        writes.append({**entry("set", "meta", "main", file_path=str(main)), **({"if_version": cur["_v"]} if cur else {})})  # existing doc: a write must carry its version
    pf = SEED / "profile-main.json"
    pm = json.loads(pf.read_text(encoding="utf-8"))
    cur = profile.get("main")
    if not cur or cur.get("bank") != pm.get("bank") or cur.get("source") != pm.get("source"):
        writes.append({**entry("set", "profile", "main", file_path=str(pf)), **({"if_version": cur["_v"]} if cur else {})})  # existing doc: a write must carry its version
    names = manifests(folder, "push-batch", writes)
    clean_names = manifests(folder, "push-cleanup", cleanup)
    mark.write_text(datetime.now().isoformat(), encoding="utf-8")
    print(f"push: {len(writes)} write(s) in {names or 'no manifest'}, of which {len(created)} new job doc(s); {moved} moved row(s); {len(cleanup)} page-added doc(s) to delete in {clean_names or 'no manifest'}, after the batches succeed.")
    for s in say[:20]:
        print(" -", s)
    if len(say) > 20:
        print(f" - and {len(say) - 20} more")
    for d in reqs.values():
        if d.get("status") == "running":
            print(f" - Close request {d['_id']} (version {d['_v']}): status done with a one-line result, or failed with the reason.")
    for s in problems:
        print(" ! ", s)
    return 1 if problems else 0


def prep(folder):
    if SEED.resolve() not in folder.resolve().parents:
        raise Stop(f"prep only empties folders under {SEED}")
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    print(f"prep: {folder} is empty")
    return 0


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    steps = {"prep": prep, "pull": pull, "push": push}
    if len(sys.argv) != 3 or sys.argv[1] not in steps:
        print("usage: sync_page.py prep|pull|push DIR", file=sys.stderr)
        return 2
    try:
        return steps[sys.argv[1]](Path(sys.argv[2]))
    except Stop as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
