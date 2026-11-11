# Builds a hosted sandbox with fictional model responses.
# Reads local seed files under work/hq-seed; use fictional data for browser QA.
# Output is work/sandbox or SANDBOX_OUT. Serve on 127.0.0.1:8799 for the mobile smoke check.
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SEED = ROOT / "work" / "hq-seed"
OUT = ROOT / (os.environ.get("SANDBOX_OUT") or "work/sandbox")

# Fictional v3.2 model shape;
# evidence IDs not in the real eligible bank are intentionally downgraded to gaps.
sys.path.insert(0,str(HERE.parent))
from lead_policy_fixtures import fictional_result
FIT = fictional_result()
FIT['summary'] = 'Fictional sandbox answer; no real qualifications asserted.'
CV = {"cv_markdown": "# Alex Example\n[email] | [phone] | [LinkedIn URL]\n## Summary\nSandbox CV {n} with a **bold** word and a long line that should wrap on a phone screen without pushing the page wider than the screen.\n## Experience\n- First bullet\n- Second bullet",
      "cv_edits": ["Led with operations work"], "sources": ["E-001", "E-002"], "flags": []}
FILLER = " ".join(["Sandbox filler sentence that stands in for real evidence-backed content in this letter."] * 13)
LETTER = {"cover_letter_markdown": "Dear hiring team,\n\nSandbox letter {n}, tone: {tone}.\n\n" + FILLER + "\n\nKind regards,\nAlex Example",
          "sources": ["E-001", "L:example"], "flags": []}
POSTING_TEXT = "AI Operations Analyst at Photo Corp. Sandbox transcription of the photo. Remote in Latin America. You will run reporting in SQL, keep stakeholders updated in English and script small automations in Python."
# A posting saved before the analyze / create split: CV and letter came in one call, no sentiment on record.
OLD_FIT = {**FIT, "cv_markdown": CV["cv_markdown"].replace("{n}", "(old)"), "cover_letter_markdown": "Dear hiring team,\n\nSandbox cover letter paragraph one.\n\nKind regards,\nAlex Example",
           "sources": ["E-001", "E-002"], "cv_edits": ["Led with operations work"]}


def load(pattern, cut):
    return {f.name[cut:-5]: json.loads(f.read_text(encoding="utf-8")) for f in sorted(SEED.glob(pattern))}


def main():
    cols = {"jobs": load("job-*.json", 4), "contacts": load("contact-*.json", 8), "requests": {}, "postings": {}, "profile": {}, "cvfiles": {}, "meta": {}}
    cols["meta"]["main"] = json.loads((SEED / "meta-main.json").read_text(encoding="utf-8"))
    if (SEED / "meta-cvs.json").exists():
        cols["meta"]["cvs"] = json.loads((SEED / "meta-cvs.json").read_text(encoding="utf-8"))
        cols["cvfiles"] = {f.name[7:-5]: json.loads(f.read_text(encoding="utf-8")) for f in SEED.glob("cvfile-*.json")}
    cols["profile"]["main"] = {"bank": "SANDBOX EVIDENCE BANK", "updated": "2026-11-03"}
    cols["requests"]["sbx-r1"] = {"kind": "search", "what": "AI operations roles", "where": "Remote LatAm", "level": "Mid", "notes": "", "status": "done",
                                  "result": "Added 6 leads to New (2026-11-04).", "createdAt": "2026-11-03T12:00:00Z"}
    cols["postings"]["sbx-p1"] = {"company": "Sandbox Corp", "role": "AI Operations Analyst", "link": "https://example.com/job", "text": "x" * 300,
                                  "jobId": "", "fit": OLD_FIT, "status": "draft", "reviewed": False, "createdAt": "2026-11-03T13:00:00Z"}
    cols["postings"]["sbx-p2"] = {"company": "Analyzed Only Ltd", "role": "Revenue Operations Analyst", "link": "", "text": POSTING_TEXT, "source": "photo",
                                  "jobId": "", "fit": FIT, "status": "draft", "reviewed": False, "createdAt": "2026-11-03T14:00:00Z"}
    page = (ROOT / "tools" / "hq-dashboard" / "index.html").read_text(encoding="utf-8")
    seed = json.dumps(cols).replace("</", "<\\/")
    head = ('<!doctype html><html><head><meta charset="utf8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            "<style>:root{color-scheme:light;padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom)}body{margin:0;font:14px system-ui}"
            "img{max-width:100%}[hidden]{display:none!important}</style>"
            f"<script>window.__SEED={seed};window.__FIT={json.dumps(FIT)};window.__CV={json.dumps(CV)};window.__LETTER={json.dumps(LETTER)};window.__POSTING_TEXT={json.dumps(POSTING_TEXT)};</script><script>{(HERE / 'mock.js').read_text(encoding='utf-8')}</script></head><body>")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(head + page + "</body></html>", encoding="utf-8", newline="\n")
    # claude.ai shows the page inside an iframe under its own top bar; frame.html imitates that (approximation, not the real app).
    (OUT / "frame.html").write_text('<!doctype html><html><head><meta charset="utf8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                                    "<style>html,body{margin:0;height:100%;background:#f5f4ef;font:14px system-ui}header{height:48px;display:flex;align-items:center;padding:0 16px;border-bottom:1px solid #ddd}"
                                    "iframe{display:block;border:0;width:100%;height:calc(100% - 49px)}</style></head><body><header>claude.ai (sandbox frame)</header>"
                                    '<iframe src="index.html" title="Job Search HQ"></iframe></body></html>', encoding="utf-8", newline="\n")
    print("wrote", OUT / "index.html", "and frame.html")


if __name__ == "__main__":
    main()
