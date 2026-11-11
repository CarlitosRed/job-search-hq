# Builds the hosted Job Search HQ page from the local tracker.
# One UI, two backends: tools/tracker/index.html stays the source of the views; this script swaps its local
# server calls for the claude.ai page store (hosted/data.js), adds the Search and Review views (hosted/views.html,
# hosted/tools.js, hosted/hosted.css) and writes tools/hq-dashboard/index.html, which is what gets published.
# Every replacement must match exactly the expected number of times, so a tracker change that breaks the port fails loudly.
# Run: python -I tools/hq-dashboard/port_tracker.py
# Resolve settings beside this module under isolated execution.
import importlib.util as _settings_util
from pathlib import Path as _SettingsPath
_settings_spec = _settings_util.spec_from_file_location('hq_owner_settings', _SettingsPath(__file__).with_name('hq_settings.py'))
hq_settings = _settings_util.module_from_spec(_settings_spec)
_settings_spec.loader.exec_module(hq_settings)
SETTINGS = hq_settings.load()

import re
import argparse
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "tracker" / "index.html"
OUT = HERE / "index.html"
ADD = HERE / "hosted"


def sub(html, old, new, count=1):
    n = html.count(old)
    if n != count:
        raise SystemExit(f"port: expected {count} match(es), found {n}: {old[:80]!r}")
    return html.replace(old, new)


def resub(html, pattern, repl):
    out, n = re.subn(pattern, repl, html, flags=re.S)
    if n != 1:
        raise SystemExit(f"port: pattern matched {n} times: {pattern[:80]!r}")
    return out


def main():
    # Keep the Claude output as the default.
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend', choices=('claude', 'pc'), default='claude')
    args = parser.parse_args()
    h = SRC.read_text(encoding="utf-8").replace("\r\n", "\n")
    read = lambda name: (ADD / name).read_text(encoding="utf-8").replace("\r\n", "\n")

    # The Artifact tool wraps the page in its own document: no doctype/html/head/body here.
    h = resub(h, r"\A.*?(?=<style>)", "<title>Job Search HQ</title>\n\n")
    # Dark tokens also follow the viewer's explicit theme choice.
    m = re.search(r'@media \(prefers-color-scheme:dark\)\{:root(?::not\(\[data-theme="light"\]\))?\{(.*?)\}\}', h, flags=re.S)
    if not m:
        raise SystemExit("port: dark token block not found")
    dark = m.group(1).rstrip().rstrip(";") + ";color-scheme:dark"
    h = h.replace(m.group(0), '@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){' + dark + "}}\n"
                  ':root[data-theme="dark"]{' + dark + "}")
    h = sub(h, ".top{position:sticky;top:0;", ".top{position:sticky;top:env(safe-area-inset-top,0px);")
    h = sub(h, "#toast{position:fixed;bottom:22px;", "#toast{position:fixed;bottom:calc(22px + env(safe-area-inset-bottom,0px));")
    h = sub(h, "</style></head><body>", read("hosted.css") + "</style>\n")
    h = sub(h, "</script></body></html>", "</script>\n")

    # Header, help and dialog
    h = sub(h, '<span class="l">Contacts</span></button>\n </nav>',
            '<span class="l">Contacts</span></button>\n'
            '  <button role="tab" data-v="search" aria-selected="false"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="6.5"/><path d="m20 20-4-4M11 8v6M8 11h6"/></svg><span class="l">Search</span></button>\n'
            '  <button role="tab" data-v="review" aria-selected="false"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 14l2 2 4-4"/></svg><span class="l">CVs</span></button>\n </nav>')
    h = sub(h, '<script>\n"use strict";', '<script src="https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js"></script>\n<script>\n"use strict";')
    h = sub(h, " close:'<svg", " dl:'<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M12 4v11M7 10l5 5 5-5M5 20h14\"/></svg>',\n close:'<svg")
    h = sub(h, 'title="Reload from the spreadsheet" aria-label="Reload from the spreadsheet"', 'title="Reload" aria-label="Reload"')
    h = sub(h, "<dt><kbd>1</kbd>–<kbd>4</kbd></dt><dd>Overview, List, Board, Contacts</dd>",
            "<dt><kbd>1</kbd>–<kbd>6</kbd></dt><dd>Overview, List, Board, Contacts, Search, CVs</dd>")
    h = sub(h, "<p>Every change saves straight into the spreadsheet and can be undone from the message at the bottom. This page only records; it never applies or sends anything.</p>",
            "<p>Every change saves online at once and reaches the spreadsheet at the daily {{SCAN_TIME}} sync. Undo from the message at the bottom. This page only records and drafts; it never applies or sends anything.</p>")
    h = sub(h, "<p class=\"sub\">Adds a new row to the spreadsheet. It's recorded only; applying stays yours.</p>",
            "<p class=\"sub\">Saved here now and added to the spreadsheet at the next {{SCAN_TIME}} sync. It's recorded only; applying stays yours.</p>")
    h = sub(h, 'id="addSave" type="button">Add to spreadsheet</button>', 'id="addSave" type="button">Add lead</button>')
    h = sub(h, '<main id="main"><div class="loading"><div class="sk-line"></div><div class="sk-line" style="height:280px"></div></div></main>',
            '<main id="main"><div class="loading"><div class="sk-line"></div><div class="sk-line" style="height:280px"></div></div></main>\n' + read("views.html").rstrip())

    # Data: the page store instead of the local server
    h = resub(h, r"/\* ---------- data ---------- \*/.*?(?=/\* ---------- writes ---------- \*/)", lambda _: read("data.js"))
    h = sub(h, 'const key=j=>j._sheet+"|"+j._row;', "const key=j=>j._id;")
    h = sub(h, 'S.q="";S.view="overview";S.attention=false;',
            'S.q="";S.view="overview";S.attention=false;{const v=location.hash.slice(1);if(["overview","list","board","contacts","search","review"].includes(v))S.view=v}')
    h = sub(h, "const p={sheet:j._sheet,row:j._row,status:s,expect:{Company:j.Company,Role:j.Role}};", "const p={id:j._id,sheet:j._sheet,status:s};")
    h = sub(h, 'post("/api/update",{sheet:j._sheet,row:j._row,status:e.status,date:e.date,expect:{Company:j.Company,Role:j.Role}})',
            'post("/api/update",{id:j._id,sheet:j._sheet,status:e.status,date:e.date})')
    h = sub(h, 'post("/api/update",{sheet:j._sheet,row:j._row,note:txt,expect:{Company:j.Company,Role:j.Role}})', 'post("/api/update",{id:j._id,sheet:j._sheet,note:txt})')
    h = sub(h, "if(ok){j.Notes=txt;j._noteSaved=true;", "if(ok){j.Notes=txt;j._noteSaved=true;NOTED.add(j._id);{const c=byKey(j._id);if(c){c.Notes=txt;c._noteSaved=true}}")
    h = sub(h, 'const p={sheet:"Contacts",row:c._row,status:t.value,expect:{Person:c.Person}};', 'const p={id:c._id,sheet:"Contacts",status:t.value};')
    h = sub(h, '$("#refresh").onclick=()=>load().then(()=>toast("Reloaded from the spreadsheet"));', '$("#refresh").onclick=reloadJobs;')
    h = sub(h, '$("#addDlg").close();await load();if(lost.length)', '$("#addDlg").close();if(lost.length)')
    h = sub(h, 'openJob(p.sheet+"|"+r.row)}', "openSoon(r.id)}")

    # Views
    h = sub(h, 'function renderInner(){const js=filtered();$$(".seg button").forEach(b=>b.setAttribute("aria-selected",b.dataset.v===S.view));\n'
               ' const ac=activeCount();$("#fcount").hidden=!ac;$("#fcount").textContent=ac;$("#fbtn").hidden=S.view==="contacts";\n'
               ' activebar();const m=$("#main");',
            'function renderInner(){const js=filtered();$$(".seg button").forEach(b=>b.setAttribute("aria-selected",b.dataset.v===S.view));\n'
            ' viewShown();const tool=TOOL_VIEWS.includes(S.view);$("#main").hidden=tool;$("#v-search").hidden=S.view!=="search";$("#v-review").hidden=S.view!=="review";$(".search").hidden=tool;\n'
            ' const ac=activeCount();$("#fcount").hidden=!ac;$("#fcount").textContent=ac;$("#fbtn").hidden=S.view==="contacts"||tool;\n'
            ' activebar();if(tool||!READY){save();return}const m=$("#main");')
    h = sub(h, '$("#activebar").innerHTML=ch.length&&S.view!=="contacts"?', '$("#activebar").innerHTML=ch.length&&S.view!=="contacts"&&!TOOL_VIEWS.includes(S.view)?')
    h = sub(h, 'if(/^[1-4]$/.test(e.key)){S.view=["overview","list","board","contacts"][+e.key-1];',
            'if(/^[1-6]$/.test(e.key)){S.view=["overview","list","board","contacts","search","review"][+e.key-1];')
    h = sub(h, "<dt>Spreadsheet</dt><dd>${esc(j._sheet)}, row ${j._row}${j[\"#\"]?` (#${esc(j[\"#\"])})`:\"\"}</dd>",
            "<dt>Spreadsheet</dt><dd>${j._added?`${esc(j._sheet)} · added here, goes in at the next {{SCAN_TIME}} sync`:`${esc(j._sheet)}, row ${esc(j._row)}${j[\"#\"]?` (#${esc(j[\"#\"])})`:\"\"}`}</dd>")
    h = sub(h, '<b>This posting looks closed.</b> Last open check: ${esc(j._check)}${j._checkdate?` (${esc(j._checkdate)})`:""}.',
            '<b>This posting looks closed.</b> Last check${j._checkdate?` (${esc(j._checkdate)})`:""}: ${esc(checkText(j))}.')
    h = sub(h, '<span class="chip ${j._open==="open"?"g":j._open==="closed"?"r":""}"><i class="dot"></i>${esc(j._check)}</span>',
            '<span class="chip ${j._open==="open"?"g":j._open==="closed"?"r":""}" title="${esc(j._check)}"><i class="dot"></i>${esc(checkText(j))}</span>')
    h = sub(h, 'No link saved</span>`}</div>',
            'No link saved</span>`}${cvFor(j)?`<button class="btn ghost" data-cv="${esc(cvFor(j).id)}">${IC.dl}${cvSameRole(j,cvFor(j))?"Download CV (.docx)":"CV made for another role here"}</button>`:""}'
            '<button class="btn ghost" data-act="review" data-id="${esc(j._id)}">${IC.star}Review fit + draft CV</button></div>')
    h = sub(h, 'if(v==="add")openAdd();', 'if(v==="add")openAdd();if(v==="review"){const j=byKey(a.dataset.id);if(j)reviewFromJob(j)}')
    h = sub(h, "note-${j._row}", "note-${esc(j._id)}", count=2)
    h = sub(h, '"Saved to the spreadsheet · ":"Saved to the Notes column · "', '"Saved · syncs at {{SCAN_TIME}}":"Notes"', count=2)
    h = sub(h, '"Unsaved changes · "', '"Unsaved changes"')
    h = sub(h, "<kbd>Ctrl</kbd>+<kbd>Enter</kbd></small>", '<span class="h-desk"> · <kbd>Ctrl</kbd>+<kbd>Enter</kbd></span></small>')
    h = sub(h, "Saved straight into the spreadsheet.</p>", "Saved online at once; the spreadsheet catches up at {{SCAN_TIME}}.</p>")
    # The viewer never shows confirm(): a second close discards unsaved notes instead.
    h = sub(h, "let lastFocus=null;", "let lastFocus=null,discardArm=false;")
    h = sub(h, """if(dirty&&!confirm("Discard unsaved notes?"))return false;""",
            """if(dirty&&!discardArm){discardArm=true;setTimeout(()=>discardArm=false,6000);toast("Unsaved notes. Save them, or close again to discard.",null,true);return false}discardArm=false;""")

    # Phones (all of this came from the 2026-11-03 phone QA).md, 2026-11-04.
    # Drawer: warn before notes are lost; a dirty note or focused field stays put while the rest refreshes; focus lands on Close.
    h = sub(h, "function openJob(k){const j=byKey(k);if(!j)return;S.sel=k;", "function openJob(k){const j=byKey(k);if(!j||keepNotes(k))return;S.sel=k;")
    h = sub(h, """const dr=$("#drawer");const ta=dr.querySelector("textarea[data-dirty='1']");if(ta&&dr.dataset.k===key(j))return;""", """const dr=$("#drawer");if(keepLive(dr,j))return;""")
    h = sub(h, """setTimeout(()=>$("#drawer .dr-close button")?.focus(),50)""", """setTimeout(()=>$('#drawer [data-act="close"]')?.focus(),50)""")
    # Toast: touch never holds it open (sticky :hover); dismissing drops focus so the next one can hide.
    h = sub(h, 'b.onclick=undo?()=>{t.classList.remove("on");undo()}:null;', "b.onclick=undo?()=>{tOff();undo()}:null;")
    h = sub(h, '$("#tx").onclick=()=>{clearTimeout(tT);t.classList.remove("on")};', '$("#tx").onclick=tOff;')
    h = sub(h, 'tMs=undo?10000:err?7000:3200;if(!t.matches(":hover,:focus-within"))tHide();else clearTimeout(tT)}',
            "tMs=undo?(wide()?10000:7000):err?7000:3200;if(tHold)clearTimeout(tT);else tHide()}")
    h = sub(h, '$("#toast").addEventListener("mouseenter",()=>clearTimeout(tT));$("#toast").addEventListener("mouseleave",tHide);\n'
               '$("#toast").addEventListener("focusin",()=>clearTimeout(tT));$("#toast").addEventListener("focusout",tHide);',
            'let tHold=false;const tOff=()=>{clearTimeout(tT);tHold=false;const t=$("#toast");t.classList.remove("on");if(t.contains(document.activeElement))document.activeElement.blur()};\n'
            '$("#toast").addEventListener("pointerenter",e=>{if(e.pointerType==="mouse"){tHold=true;clearTimeout(tT)}});$("#toast").addEventListener("pointerleave",e=>{if(e.pointerType==="mouse"){tHold=false;tHide()}});\n'
            '$("#toast").addEventListener("focusin",e=>{if(e.target.matches(":focus-visible")){tHold=true;clearTimeout(tT)}});$("#toast").addEventListener("focusout",()=>{if(tHold){tHold=false;tHide()}});')
    # Status: a second pick during a save runs right after it; the date field has one rule and refuses the future.
    h = sub(h, "const k=key(j);if(PENDING.has(k))return;\n", 'const k=key(j);if(PENDING.has(k)){if(date===undefined&&!undoing)NEXT.set(k,s);else{toast("Still saving the last change. Try again in a moment.");render()}return}\n')
    h = sub(h, 'if(!ok){cur["My status"]=prev.status;cur["Date applied"]=prev.date;render();return}\n const msg=', 'if(!ok){NEXT.delete(k);cur["My status"]=prev.status;cur["Date applied"]=prev.date;render();return}\n const msg=')
    h = sub(h, "pushUndo({k,company:j.Company,role:j.Role,status:prev.status,date:prev.date,msg});toast(msg,undoLast)}", "pushUndo({k,company:j.Company,role:j.Role,status:prev.status,date:prev.date,msg});toast(msg,undoLast);if(NEXT.has(k)){const n=NEXT.get(k);NEXT.delete(k);setStatus(cur,n)}}")
    h = sub(h, "const PENDING=new Set();", "const PENDING=new Set(),NEXT=new Map();")
    h = sub(h, 'if(t.hasAttribute("data-date")){const j=byKey(t.closest("[data-k]").dataset.k);setStatus(j,j["My status"],{date:t.value});return}', 'if(t.hasAttribute("data-date")){dateChanged(t);return}')
    h = sub(h, 'bad.push("a score like 3.6");', 'bad.push("a score like 3.6");if(p["Date applied"]>D.today)bad.push("a date applied that isn\'t in the future");')
    # Board: a direct way to move a card by touch, and wording that fits the device.
    h = sub(h, 'Also in ${j._sib.length} place${j._sib.length>1?"s":""}</span>`:""}</div></div>', 'Also in ${j._sib.length} place${j._sib.length>1?"s":""}</span>`:""}</div>${opts.drag?moveSel(j):""}</div>')
    h = sub(h, '<div class="col-empty">${s.id==="wish"?"No saved jobs":"Drag a card here"}</div>', '<div class="col-empty">${s.id==="wish"?"No saved jobs":`<span class="h-desk">Drag a card here</span><span class="h-touch">Nothing here yet</span>`}</div>')
    h = sub(h, '<p class="hint">Drag cards between', '<p class="hint h-desk">Drag cards between')
    h = sub(h, 'return`<div class="board" role="list">', 'return`<p class="hint h-touch">Scroll sideways for more stages. Tap a card for details, or use Move… on it.</p><div class="board" role="list">')
    # Help: phones get phone help, not keyboard shortcuts.
    h = sub(h, '<div id="help" popover><h2>Shortcuts</h2><dl>',
            '<div id="help" popover><h2 class="h-desk">Shortcuts</h2><h2 class="h-touch">Help</h2><p class="h-touch">Tap a job to open it. Change its status there, or with Move… on the Board. Scrolled down? Tap the open tab to go back up.</p><dl class="h-desk">')
    h = sub(h, "it never applies or sends anything.</p></div>", 'it never applies or sends anything.</p><button class="btn ghost h-touch" type="button" popovertarget="help" popovertargetaction="hide">Close</button></div>')
    # Rejected and Skipped tiles show exactly what they count, not the shared closed column.
    h = sub(h, 'if(skip!=="stages"&&S.stages.length&&!S.stages.includes(stageOf(j).id))return false;', 'if(skip!=="stages"&&S.stages.length&&!S.stages.includes(stageOf(j).id))return false;\n if(skip!=="stages"&&S.bkt&&bucket(j)!==S.bkt)return false;')
    h = sub(h, "+S.hideClosed+S.attention;", "+S.hideClosed+S.attention+!!S.bkt;")
    h = sub(h, "hideClosed:false,attention:false});", 'hideClosed:false,attention:false,bkt:""});')
    h = sub(h, 'S.stages.forEach(s=>add(STAGES.find(x=>x.id===s).label,"stages",s));', 'S.stages.forEach(s=>add(S.bkt?BUCKETS.find(b=>b[0]===S.bkt)[1]:STAGES.find(x=>x.id===s).label,"stages",s));')
    h = sub(h, 'const rm=t.closest("[data-rm]");if(rm){const f=rm.dataset.rm;', 'const rm=t.closest("[data-rm]");if(rm){const f=rm.dataset.rm;if(f==="stages")S.bkt="";')
    h = sub(h, 'if(t.dataset.f){const f=t.dataset.f;', 'if(t.dataset.f){const f=t.dataset.f;if(f==="stages")S.bkt="";')
    h = sub(h, 'S.view="list";if(v!=="all"){S.stages=[BUCKET_STAGE[v]]}render();return}', 'S.view="list";S.bkt="";if(v!=="all"){S.stages=[BUCKET_STAGE[v]];if(BUCKET_STAGE[v]==="closed")S.bkt=v}render();return}')
    # Add lead: the right message before data is in, no keyboard on open on a phone, and the real reason inside the dialog (the toast is behind it).
    h = sub(h, 'return toast("No job sheets found in the spreadsheet",null,true);', 'return toast(READY?"No job lists found.":$("#sync").textContent==="Not signed in"?"Not signed in. Open this page on claude.ai.":"Still loading your jobs. Try again in a moment.",null,true);')
    h = sub(h, '$("#addDlg").showModal();f.Company.focus()}', '$("#addDlg").showModal();if(wide())f.Company.focus();else{const d=$("#addDlg");d.tabIndex=-1;d.focus();d.scrollTop=0}}')
    h = sub(h, 'err.textContent="Not saved. See the message at the bottom of the screen."', 'err.textContent=$("#tmsg").textContent||"Not saved. Try again in a moment.";err.scrollIntoView({block:"center"})')
    h = sub(h, 'err.textContent="Please add "+bad.join(", ")+".";return}', 'err.textContent="Please add "+bad.join(", ")+".";err.scrollIntoView({block:"center"});return}')
    h = sub(h, "\nboot();\n</script>", "\n" + read("lead-policy.js").rstrip() + "\n" + read("tools.js").rstrip() + "\n\nboot();\n</script>")
    # Lanes use existing Verdict,
    # preserving legacy history and the workbook schema. Never hand-edit generated pages.
    h = sub(h, 'const VERD={', 'const VERD={READY:["Ready","g"],STRETCH:["Stretch","b"],VERIFY:["Verify","a"],MONITOR:["Monitor",""],ARCHIVE:["Archive",""],')
    h = sub(h, 'function fitText(sc,v){', 'function fitText(sc,v){if(["READY","STRETCH","VERIFY","MONITOR","ARCHIVE"].includes(v.key))return VERD[v.key][0];')
    h = sub(h, '<option>APPLY</option>', '<option>Ready</option><option>Stretch</option><option>Verify</option><option>Monitor</option><option>Archive</option><option>APPLY</option>')
    h = sub(h, 'function ring(sc,size=38){', 'function ring(sc,size=38,v={key:"NONE"}){')
    h = sub(h, 'ring(j._sc,opts.list?40:36)', 'ring(j._sc,opts.list?40:36,j._v)')
    h = sub(h, 'ring(sc,52)', 'ring(sc,52,v)')
    h = sub(h, 'function boardCard(j,s){', 'function boardCard(j,s){const v=j._v;')
    h = sub(h, 'Fit score ${esc(sc.raw)} of 5', '${["READY","STRETCH","VERIFY","MONITOR","ARCHIVE"].includes(v.key)?"Attainability":"Previous fit score"} ${esc(sc.raw)} of 5', count=4)

    for bad in ("fetch(", "confirm(", "/api/data", "/api/version", "await load("):
        if bad in h:
            raise SystemExit(f"port: leftover local-server call {bad!r}")
    target = OUT
    if args.backend == 'pc':
        h = sub(h, '<script src="https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js"></script>\n', '')
        h = sub(h, read('data.js'), read('pc-data.js'))
        h = h.replace('Every change saves online at once and reaches the spreadsheet at the daily {{SCAN_TIME}} sync.',
                      'Every saved change goes straight into your spreadsheet.')
        h = h.replace('Saved here now and added to the spreadsheet at the next {{SCAN_TIME}} sync.', 'Adds a row directly to your spreadsheet.')
        h = h.replace('Saved online at once; the spreadsheet catches up at {{SCAN_TIME}}.', 'Saved straight into your spreadsheet.')
        h = h.replace('Saved · syncs at {{SCAN_TIME}}', 'Saved to spreadsheet')
        h = h.replace('Draft: truth review at {{SCAN_TIME}}', 'Draft: awaiting separate truth review')
        h = sub(h, 'esc(c.updated)', 'esc(pcDate(c.updated))')
        # Documents tab (Claude · HQ Builder (Opus), 2026-11-05): CVs, cover letters and reports; a job links its files.
        h = sub(h, '<span class="l">CVs</span></button>\n </nav>', '<span class="l">Documents</span></button>\n </nav>')
        h = sub(h, '<dd>Overview, List, Board, Contacts, Search, CVs</dd>', '<dd>Overview, List, Board, Contacts, Search, Documents</dd>')
        h = sub(h, ' <div class="panel"><div class="panel-h"><h2>Your CVs</h2></div>\n'
                   '  <p class="lede">Your tailored CVs as Word files. On the iPhone a download lands in Files › Downloads. They\'re also in {{COPY_CVS}}, refreshed by the {{SCAN_TIME}} run.</p>\n'
                   '  <div id="cvs"><p class="mut">Loading…</p></div></div>',
                ' <div class="panel" id="docs"><div class="panel-h"><h2>Your documents</h2></div>\n'
                '  <p class="lede">Your CVs, cover letters and reports. Tap Download to save a file; on the iPhone it lands in Files › Downloads. Tap Read to open a report here.</p>\n'
                '  <h3 class="doc-sec">CVs</h3><div id="cvs"><p class="mut">Loading…</p></div>\n'
                '  <h3 class="doc-sec">Cover letters</h3><div id="cls"><p class="mut">Loading…</p></div>\n'
                '  <h3 class="doc-sec">Reports</h3><div id="reports"><p class="mut">Loading…</p></div>\n'
                '  <article id="report-view" class="report" hidden></article></div>')
        h = sub(h, '${IC.star}Review fit + draft CV</button></div>', '${IC.star}Review fit + draft CV</button>${pcJobDocs(j)}</div>')
        h = sub(h, '</style>', read('pc.css') + '</style>')
        h = h.replace('Claude reads it, scores it against your evidence bank and shows the fit.',
                      'Codex reads it, scores it against your evidence bank and shows the fit.')
        h = h.replace('They stay drafts until the {{SCAN_TIME}} truth review.',
                      'They stay drafts until a separate truth reviewer checks them.')
        # PC Search uses live public company boards; the hosted daily-request form stays unchanged.
        #
        h = h.replace('{{SEARCH_HELP}}',
                      'Search current openings on the company boards below. Review the results, then choose which leads to add to your tracker. Remote does not always mean you can work from your country.')
        h = resub(h, r'<form id="f-search".*?</form>', lambda _: '''<form id="f-search" class="form2" novalidate>
   <label class="f full" for="s-what"><span>Role keywords <span class="req">*</span></span><input class="inp" id="s-what" maxlength="300" required aria-describedby="s-query-help" placeholder="e.g. automation analyst"></label>
   <p id="s-query-help" class="mut full pc-search-help">Matches any keyword in the job title. Use fewer words for a broader search.</p>
   <label class="f full" for="s-where">Location contains (optional)<input class="inp" id="s-where" maxlength="120" aria-describedby="s-location-help" placeholder="e.g. Latin America"></label>
   <p id="s-location-help" class="mut full pc-search-help">Use one place name as written in the posting. Leave blank to see all locations, then check the eligible countries.</p>
   <label class="pc-search-check full"><input type="checkbox" id="s-remote">Remote only</label>
   <p id="s-sources" class="mut full">Loading the company boards…</p>
   <p id="s-err" class="banner full" role="alert" hidden></p>
   <div class="full tool-foot"><small id="s-msg" role="status"></small><button class="btn" id="s-go" type="submit">Search now</button></div>
  </form>''')
        h = sub(h, 'boot();\n</script>', read('pc-tools.js') + '\nboot();\n</script>')
        h = sub(h, '</style>', '</style></head><body>')
        h = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">' + h + '</body></html>'
        target = HERE / 'pc-index.html'
    target.write_text(hq_settings.render_page(h), encoding="utf-8", newline="\n")
    print("wrote", target, len(h.encode("utf-8")), "bytes")


if __name__ == "__main__":
    main()
