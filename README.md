# Job Search HQ

[![tests](https://github.com/CarlitosRed/job-search-hq/actions/workflows/tests.yml/badge.svg)](https://github.com/CarlitosRed/job-search-hq/actions/workflows/tests.yml) [![release](https://img.shields.io/github/v/release/CarlitosRed/job-search-hq)](https://github.com/CarlitosRed/job-search-hq/releases) [![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A private, password-protected job-search desk that runs on your own PC and opens on your phone.
It keeps your leads in an ordinary Excel file you already own, and can ask an AI assistant to score a posting
against **your own evidence** and draft a one-page CV or cover letter. Every draft stays a draft until you check it.

> Built by directing AI agents (Claude and Codex) with a human owner who set the goals and approved
> every change. The code is small on purpose and it ships with offline tests that use only fictional data.

## What it does

- **Board, list, overview and contacts** over your spreadsheet (`work/applications-tracker.xlsx`), with durable saves:
  edits queue safely while Excel or a scan has the file open, conflicts are shown instead of overwritten.
- **Review a posting**: paste text or add photos, get a fit score and an honest gaps list, then ask for a CV or letter.
- **Evidence first**: the model is asked to use entries in `evidence/` that are marked `verified` or `stated`, `public-safe: yes`
  and have CV-ready wording, and to list each factual claim with its evidence IDs. The app checks that cited IDs are eligible
  and each listed claim string appears in the document. It does not check that every factual sentence is listed or that the
  cited evidence supports a claim. A human must review every line before using the draft.
- **Easy to read**: CV Word files are laid out for quick reading (one page, clear section headings, dates on the right),
  and trimming to one page deletes whole lines or Summary sentences only (page 2 only as a last resort), never rewording anything.
- **One page**: the AI is asked for at most 520 words and the app rejects anything over 600. `cv_onepage.py` trims an
  existing CV to one page by deleting lines only (nothing is reworded, and a check refuses anything that was not already there).
- **Cover letters need a sentiment** (or an explicit choice to go without), and the sentiment only changes tone.
- **Documents**: one tab lists your CVs (`work/CV_*.docx`), cover letters (`work/CL_*.docx`) and reports (`work/report-*.md`)
  with phone-friendly downloads; reports can be read on the page and downloaded as .md or .docx. Exported files are clean
  (no header, banner or note lines; letters are plain paragraphs), files that still contain notes are labelled, and a job's
  detail links its CV and letter and has **Write a cover letter**. See `examples/` for a fictional letter and report.
- **No auto-apply, no sending.** It never submits applications or messages. You press the buttons.

## Safety model (read this before putting it on the internet)

| Piece | How it is handled |
| --- | --- |
| Network | Server listens on `127.0.0.1` only. Remote access is through a tunnel you control (Tailscale Funnel is described in `SETUP.md`). |
| Login | One password you choose, stored only as a salted scrypt hash outside the repo; account-wide lockout after five failures, `--unlock` clears it locally. |
| Browser | Secure, HttpOnly, SameSite=Strict cookie; exact Host and Origin checks on every write; a content security policy (same-origin resources only, inline scripts allowed because the page is one file); no directory serving. |
| AI | Runs the Codex CLI in a read-only sandbox with shell, plugins and web search off, using your own ChatGPT login. No API keys are read. |
| Data | The spreadsheet (`work/`), passwords and generated files live outside Git. Your real `evidence/` files and `rules.md` are git-ignored too; only the fictional `*-example-*` files and `rules.example.md` are tracked. |

Known limits: one pending edit at a time; a changed workbook produces a conflict message instead of merging;
AI tasks interrupted by a restart are not rerun; page layout of exported Word files was only checked with a Python reader.

## Quick start (Windows, Python 3.12)

```
pip install -r requirements.txt
python -I tools/hq-dashboard/server.py --set-password
python tools/hq-dashboard/port_tracker.py --backend pc
mkdir work
copy examples\applications-tracker.xlsx work\applications-tracker.xlsx
python -I tools/hq-dashboard/server.py
```

Open http://127.0.0.1:8770 (the default origin) and sign in. `SETUP.md` covers phone access and starting it at boot.
Replace the fictional files in `evidence/` with your own (name them anything not containing `-example-`, so Git ignores them)
and copy `rules.example.md` to `rules.md` before generating anything real.

## Repository map

- `tools/hq-dashboard/server.py`: the protected local server and API; `write_queue.py`: durable spreadsheet saves;
  `ai_tasks.py`: the typed task queue and Codex worker; `pc_files.py`: CV, cover-letter and report downloads and Word export; `docx_export.py`: the Word converters and marker rules; `cv_onepage.py`: one-page trimmer; `cv_layout.py`: the shared CV Word style.
- `tools/tracker/`: the page source (`index.html`) and the plain tracker server; `port_tracker.py --backend pc` builds the page the server serves.
- `tools/hq-dashboard/hosted/`: page pieces. `data.js` and `tools.js` come from the original claude.ai-hosted version of this page;
  the PC build swaps the data layer for `pc-data.js` and `pc-tools.js`.

## Tests

```
python -I tools/hq-dashboard/test_ai_tasks.py
python -I tools/hq-dashboard/test_cv_onepage.py
python -I tools/hq-dashboard/test_pc_server.py   # needs Playwright for the phone-browser case
```

The suite never calls a live model and never reads real career data.

## Contributing

Want to improve the look and feel? See [CONTRIBUTING.md](CONTRIBUTING.md): it says which files are safe for design work and which (logic, backend, safety checks) need the owner.

## License

MIT. Use it, change it, tell me what broke.
