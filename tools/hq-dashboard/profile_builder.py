# Generic hosted profile and workbook seed builder.
import hashlib, json, re, sys
import importlib.util
from datetime import date
from pathlib import Path

import openpyxl

#
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lead_policy as policy
_settings_spec = importlib.util.spec_from_file_location('profile_owner_settings', Path(__file__).with_name('hq_settings.py'))
hq_settings = importlib.util.module_from_spec(_settings_spec)
_settings_spec.loader.exec_module(hq_settings)
RUBRIC_VERSION = 'v3.2'
if RUBRIC_VERSION != policy.RUBRIC_VERSION:
    raise ValueError('Rubric version mismatch: profile bank')

ROOT = hq_settings.data_root()
OUT = ROOT / "work" / "hq-seed"
STATUS = re.compile(r"\s*(OPEN|CLOSED|REDIRECTED|UNKNOWN)", re.I)


def cell(v):
    if v is None:
        return ""
    return v.isoformat()[:10] if hasattr(v, "isoformat") else str(v).strip()


def doc_id(sheet, row):
    return re.sub(r"[^A-Za-z0-9_-]+", "-", f"{sheet}-{row}").strip("-")[:120]


def newest_first(heads):
    # "Open check <date>" columns, newest date first; columns with no date come last, rightmost first.
    dated = [(re.search(r"\d{4}-\d{2}-\d{2}", h), n, h) for n, h in enumerate(heads) if h.startswith("Open check")]
    return [h for _, _, h in sorted(dated, key=lambda x: (x[0].group(0) if x[0] else "", x[1]), reverse=True)]


def workbook():
    return openpyxl.load_workbook(ROOT / "work" / "applications-tracker.xlsx", read_only=True)


def meta():
    # The tracker's "Add lead" dialog needs each lead sheet's columns (some have no Source column).
    sheets = []
    for ws in workbook():
        head = [str(h) for h in next(ws.iter_rows(max_row=1, values_only=True), ()) if h]
        if "My status" in head:
            sheets.append({"name": ws.title, "columns": head})
    return {"sheets": sheets, "updated": date.today().isoformat()}


def contacts():
    wb = workbook()
    if "Contacts" not in wb.sheetnames:
        return
    rows = list(wb["Contacts"].iter_rows(values_only=True))
    head = [str(h) if h else "" for h in rows[0]] if rows else []
    for n, r in enumerate(rows[1:], start=2):
        d = {h: cell(v) for h, v in zip(head, r) if h}
        if d.get("Person"):
            yield doc_id("Contacts", n), {
                "person": d["Person"], "whereNow": d.get("Where now", ""), "howKnown": d.get("How you know them", ""),
                "status": d.get("Status", ""), "dateSent": d.get("Date sent", ""), "reply": d.get("Reply", ""),
                "notes": d.get("Notes", ""), "row": n,
            }


def jobs():
    wb = workbook()
    for ws in wb:
        rows = list(ws.iter_rows(values_only=True))
        if not rows or "My status" not in rows[0]:
            if rows and any(str(h or "").strip().lower() == "my status" for h in rows[0]):
                print(f"WARNING: sheet {ws.title!r} skipped: its My status header has extra spaces", file=sys.stderr)
            continue
        head = [str(h) if h else "" for h in rows[0]]
        checks = newest_first(head)
        for n, r in enumerate(rows[1:], start=2):
            d = {h: cell(v) for h, v in zip(head, r) if h}
            if not d.get("Company"):
                continue
            # A "not re-checked" line says nothing, so a real result in an older column wins over it.
            latest = next((h for h in checks if STATUS.match(d.get(h, ""))), None) or next((h for h in checks if d.get(h)), checks[0] if checks else "")
            raw = d.get(latest, "")
            m = STATUS.match(raw)
            yield doc_id(ws.title, n), {
                "num": d.get("#", ""), "company": d.get("Company", ""), "role": d.get("Role", ""), "where": d.get("Where", ""),
                "posted": d.get("Posted", ""), "score": d.get("Score", ""), "verdict": d.get("Verdict", ""),
                "status": d.get("My status", "") or "Suggested (not applied)", "dateApplied": d.get("Date applied", ""),
                "cvUsed": d.get("CV used", ""), "source": d.get("Source", ""), "link": d.get("Link", ""),
                "notes": d.get("Notes", ""), "check": m.group(1).upper() if m else "", "checkNote": raw[:300],
                "checkDate": (re.search(r"\d{4}-\d{2}-\d{2}", latest) or [""])[0] if latest else "",
                "sheet": ws.title, "row": n,
            }


def strip_open_section(md):
    # Drop the reviewer notes block ("Open before this can go out") and keep the CV body.
    return re.split(r"\n---\n", md, maxsplit=1)[-1].strip()


def profile():
    # Read configured rules and evidence together.
    # ai_tasks defines its queue class on import; no worker or database starts until requested.
    spec = importlib.util.spec_from_file_location("hq_profile_ai_tasks", Path(__file__).with_name("ai_tasks.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    evidence = module.evidence_bank(ROOT)
    entries = evidence["entries"]
    blocks = [f"[{i}] ({e['level']}) {e['text']} | Limits: {e['limits']} | Publication: {e['publicationConditions']}"
              for i, e in sorted(entries.items())]
    bank = evidence["rules"] + "\nEVIDENCE BANK (eligible entries only)\n" + "\n\n".join(blocks)
    return {"rubric_version": RUBRIC_VERSION, "entries": entries, "scoring_prompt": policy.SCORING_PROMPT,
            "analysis_schema": policy.extraction_schema(), "bank": bank, "updated": date.today().isoformat(), "source": "evidence/ (eligible entries) + AGENTS.md rules + jobs/scoring-rubric.md"}


def unique(docs, what):
    # Two sheet names can clean to the same id ("New (A)", "New {A}"): the second lead gets its own id, not the first one's.
    seen = set()
    for i, d in docs:
        if i in seen:
            print(f"WARNING: {what} id {i} repeats; {d.get('sheet', '')} row {d['row']} gets a longer id", file=sys.stderr)
            i = f"{i}-{hashlib.sha1(str(d.get('sheet', d['row'])).encode('utf-8')).hexdigest()[:6]}"
        seen.add(i)
        yield i, d


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # Read everything first: if the workbook can't be read, the old seed stays as it was and this script exits non-zero.
    job_docs, contact_docs, sheets, bank = list(unique(jobs(), "job")), list(unique(contacts(), "contact")), meta(), profile()
    for pattern in ("job-*.json", "contact-*.json", "batch-*.json"):
        for old in OUT.glob(pattern):
            old.unlink()
    (OUT / "profile-main.json").write_text(json.dumps(bank, ensure_ascii=False, indent=1), encoding="utf-8")
    entries = []
    for i, d in job_docs:
        f = OUT / f"job-{i}.json"
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        entries.append({"op": "set", "collection": "jobs", "doc_id": i, "file_path": str(f)})
    jobs_n = len(entries)
    for i, d in contact_docs:
        f = OUT / f"contact-{i}.json"
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        entries.append({"op": "set", "collection": "contacts", "doc_id": i, "file_path": str(f)})
    (OUT / "meta-main.json").write_text(json.dumps(sheets, ensure_ascii=False), encoding="utf-8")
    for k in range(0, len(entries), 50):
        (OUT / f"batch-{k // 50}.json").write_text(json.dumps(entries[k:k + 50], indent=0), encoding="utf-8")
    print(jobs_n, "jobs;", len(entries) - jobs_n, "contacts;", (len(entries) + 49) // 50, "batches; profile", len(bank["bank"]), "chars")


if __name__ == "__main__":
    main()
