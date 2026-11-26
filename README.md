# Job Search HQ

[![tests](https://github.com/CarlitosRed/job-search-hq/actions/workflows/tests.yml/badge.svg)](https://github.com/CarlitosRed/job-search-hq/actions/workflows/tests.yml) [![release](https://img.shields.io/github/v/release/CarlitosRed/job-search-hq)](https://github.com/CarlitosRed/job-search-hq/releases) [![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A private, password-protected job-search desk that runs on your Windows PC and opens on your phone through optional HTTPS hosting.
It keeps your leads in SQLite after migration, with an automatic Excel copy. Without a database, it uses Excel directly.
The qualification judge uses **your own evidence** to assess saved postings. You can also ask an AI assistant to draft a one-page CV or cover letter for you to review.

> Built by directing AI agents (Claude and Codex) with a human owner who set the goals and approved
> every change. The code is small on purpose and it ships with offline tests that use only fictional data.

## What it does

- **Board, list, overview and contacts** over your tracker, with durable saves. Edits queue and conflicts are shown instead of overwritten. In Excel mode, edits wait while the workbook is open.
- **Search and review** reads public employer systems, web search and job feeds. The owner gets a newest-first To review list of matching, open postings dated within 14 days. Yes adds a job as Tracked with “Being scored” in Notes. No records a permanent skip and can include a note.
- **Scores and stars.** List, Board and Top opportunities show a 1.0 to 5.0 score and an action. The bands start at 4.5 for Apply now, 4.0 for Worth applying, 3.5 for Only with a reason, and 3.0 for Worth a look. Lower scores and postings outside your work rules show Skip. Unscored jobs show Not scored yet. The drawer shows quoted reasons and main requirements with evidence titles. Highest score first puts starred jobs first, then sorts by score and date. With SQLite you can star or unstar a job.
- **Review a posting.** Paste text or add photos, review requirements and gaps, then ask for a CV or letter.
- **Evidence first**: the model is asked to use entries in `evidence/` that are marked `verified` or `stated`, `public-safe: yes`
  and have CV-ready wording, and to list each factual claim with its evidence IDs. The app checks that cited IDs are eligible
  and each listed claim string appears in the document. It does not check that every factual sentence is listed or that the
  cited evidence supports a claim. A human must review every line before using the draft.
- **Easy to read**: CV Word files aim for one page, with clear section headings and dates on the right,
  with Summary, optional Technical Skills, Experience, Selected Projects and fixed education/language sections in that order.
  Profile links come from `owner_links` and are clickable in Word; they never enter the AI prompt.
  The default `cv_skills_placement: "tools_tested"` shows Technical Skills after Summary only when at least
  `cv_tools_tested_min` (default 2) must requirements mention skills from `cv_tool_groups` (default Data and Tools).
  Use `"end"` for the previous Skills placement. Projects may supply a checked `- project: Title` evidence line.
  Trimming to one page deletes whole lines or Summary sentences only (page 2 only as a last resort), never rewording anything.
- **One page**: the AI is asked for at most 520 words and the app rejects anything over 600. `cv_onepage.py` trims an
  existing CV toward one page by deleting lines or Summary sentences only (nothing is reworded, and a check refuses anything that was not already there). A second page is possible as a last resort; inspect the exported layout.
- **Cover letters need a sentiment** (or an explicit choice to go without), and the sentiment only changes tone.
- **Documents**: one tab lists your CVs (`work/CV_*.docx`), cover letters (`work/CL_*.docx`) and reports (`work/report-*.md`)
  plus separate CV rationale notes (`<CV file stem>_notes.md`, using the existing `report` kind).
  Notes describe posting priorities, section choices and evidence gaps and have their own download beside the CV;
  older CV records without rationale still export without notes. Notes never enter the CV.
  Downloads work on phones; reports can be read on the page and downloaded as .md or .docx. Exported files are clean
  (no header, banner or note lines; letters are plain paragraphs), files that still contain notes are labelled, and a job's
  detail links its CV and letter and has **Write a cover letter**. See `examples/` for a fictional letter and report.
- **No auto-apply, no sending.** It never submits applications or messages. You press the buttons.

## Safety model (read this before putting it on the internet)

| Piece | How it is handled |
| --- | --- |
| Network | Server listens on `127.0.0.1` only. Remote access is through a tunnel you control (Tailscale Funnel is described in `SETUP.md`). |
| Login | One password you choose, stored only as a salted scrypt hash outside the repo; account-wide lockout after five failures, `--unlock` clears it locally. |
| Browser | Secure, HttpOnly, SameSite=Strict cookie; exact Host and Origin checks on every write; a content security policy (same-origin resources only, inline scripts allowed because the page is one file); no directory serving. |
| AI | Runs the Codex CLI in a read-only sandbox with shell, plugins, hooks and web search off, using your own ChatGPT login. API-key environment variables are removed from the child process. Submitted posting data and eligible evidence are sent to OpenAI. The job judge can use the Claude Code CLI instead (`daily_run.judge_ai`: `claude`), on your own Claude sign-in with no tools, MCP servers or skills; its posting data and evidence then go to Anthropic. |
| Data | The tracker data (`work/`), passwords and generated files live outside Git. Your real `evidence/` files and `rules.md` are git-ignored too; only the fictional `*-example-*` files and `rules.example.md` are tracked. |

Known limits: one pending edit at a time; a changed tracker produces a conflict message instead of merging;
AI tasks that were running at a restart are marked interrupted rather than rerun (queued tasks can still run);
page fit uses font metrics and needs visual review in Word or Pages.

## Search and To review

Set `search.review_queue` to `true` in your profile and sign in as its owner. The default is `false`. An existing `search.ai_shadow` setting supplies the default when `review_queue` is absent. The daily run records “Jobs reach the tracker only when you say yes” with either setting. With the queue off, the existing board search still supplies re-check snapshots.

When review is enabled, the daily run uses two local times from `daily_run.times` in private `settings.json`. The defaults are `["06:00", "22:00"]`. Start the scheduler using the existing data root.

```powershell
python -I tools/tracker/daily_run.py --watch
```

Leave the scheduler running while the PC is on, or schedule the same script without `--watch` at both configured times. An invocation checks the most recent due slot. `daily_run.enabled: false` pauses scheduled runs. Run once with `python -I tools/tracker/daily_run.py --trigger manual` to start a search immediately.

Each run makes one search AI attempt, up to two attempts per profile each day. It uses the profile, evidence, owner choices and skip notes, tracker outcomes and yesterday’s engine counts. The search AI suggests role queries, places, employers and filters. Search runs automatically. Suggested filters take effect after Approve, and Undo approval removes the filter. During an outage the last valid plan can be reused once, then the profile’s lanes and words are used. The judge has a separate budget of up to 20 calls a day. It fills in the score and reasons when it can reach the AI.

### Search engines

Configured boards are a starting set. Public web results, named employers and aggregator leads can surface new employer boards. The search remembers boards whose public API responds and stops using a discovered board after 30 days of errors or empty results. Employer pages that require sign-in or a bot check, or return HTTP 403 or 429, are skipped with a reason. Aggregator leads require independent confirmation from the employer. Engine counts and hidden reasons are saved in `work/search-backlog.sqlite3`.

Set credentials in the private `search_engines` settings object. This example lists the supported keys with empty values.

```json
{
  "search_engines": {
    "web_search": {"provider": "brave", "key": ""},
    "adzuna": {"id": "", "key": "", "country": "us"},
    "jooble": {"key": ""},
    "careerjet": {"key": "", "locale": "en_US"},
    "linkedin": {"path": ""}
  }
}
```

Brave, Adzuna, Jooble and Careerjet are off until their credentials are supplied. Remotive, Remote OK, Arbeitnow, employer careers pages, the configured board readers, Workable, Himalayas and Jobicy are enabled for review by default. Set an engine’s `enabled` to `false` to switch it off. Engine IDs include `careers_page`, `configured_boards`, `workday`, `workable_search`, `himalayas`, `jobicy`, `remotive`, `remoteok` and `arbeitnow`.

The LinkedIn reader goes live with the email feature when you provide a local folder of alert `.eml` files, an `.eml` file, or an exported mbox file. The path is empty by default. It reads the export locally and never signs in to or scrapes LinkedIn.

The top-level `no_automatic_read_hosts` setting in private `settings.json` defaults to `["amazon.jobs", "amazon.com", "smartrecruiters.com"]`. It applies to posting links, re-checks, employer careers pages and public board requests. Each entry is a bare host name and covers its subdomains. Set `"no_automatic_read_hosts": []` to allow HQ to attempt public reads from those hosts. Pages that require sign-in, present a bot check or block access are still skipped.

The legacy `search.landing_score` and `search.daily_land_cap` values are still validated for profiles that contain them. The review flow does not use them.

### Replay a copy of the tracker

Point `JOB_SEARCH_HQ_DATA` at a test data folder containing a profile and a migrated SQLite tracker under `work/`. Enable `search.review_queue` in that profile and start the site using the same data root.

```powershell
python -I tools/hq-dashboard/ai_shadow.py --replay --days 14
```

Omit `--days` to consider all active rows. The 14-day filter still applies. Replay reads the saved tracker data, ignores the already-on-tracker rule, uses saved posting status where available and writes only to the review queue. A Yes on an existing tracker row reuses it. No records a skip. Replay makes no network or AI calls.

A removed or archived job may return once when the employer publishes a new posting. To record a removal separately, use the original link and removal date.

```powershell
python -I tools/hq-dashboard/ai_shadow.py --company "Fable Workshop" --title "Research Analyst" --link "https://example.invalid/posting/1" --removed-on 2026-11-18
```

## Quick start

Follow [SETUP.md](SETUP.md) for Python 3.12 installation, a fictional workbook, the PC page build, password setup, and the Windows server on `127.0.0.1:8770`. It also covers HTTPS phone access, backups, startup tasks, and known gaps. Tracker writes keep up to seven daily pre-write backups of the active database or workbook; these do not cover evidence or state. The bundled `examples/applications-tracker.xlsx` contains three fictional jobs and one fictional contact; the setup guide also provides a fixture snippet.

For optional posting-link rendering on the PC, run `pip install playwright` in the Python environment used by HQ. The reader tries installed Google Chrome first, then installed Microsoft Edge, then Playwright's bundled Chromium. Run `python -m playwright install chromium` in the same environment if you want the bundled fallback. Sites that show a bot check need pasted posting text or a photo.

For UI work, use [the contributor quickstart](docs/CONTRIBUTOR-QUICKSTART.md). macOS/Linux can build the HTML and preview the original tracker without writes; full server operation and complete validation require Windows. Read [the architecture overview](docs/ARCHITECTURE.md) for component and data boundaries.

## Repository map

- `tools/hq-dashboard/server.py`: the protected local server and API; `write_queue.py`: durable spreadsheet saves;
  `ai_tasks.py`: the typed task queue and Codex worker; `pc_files.py`: CV, cover-letter and report downloads and Word export; `docx_export.py`: the Word converters and marker rules; `cv_onepage.py`: one-page trimmer; `cv_layout.py`: the shared CV Word style.
- `tools/tracker/`: the page source (`index.html`) and the plain tracker server; `port_tracker.py --backend pc` builds the page the server serves.
- `tools/hq-dashboard/hosted/`: page pieces, including styles, views, `tools.js`, `lead-policy.js`, `pc-data.js` and `pc-tools.js`.

## Tests

```powershell
python -I tools/hq-dashboard/test_ai_tasks.py
python -I tools/hq-dashboard/test_cv_onepage.py
python -I tools/hq-dashboard/test_pc_server.py
python -I tools/hq-dashboard/test_job_search.py
python -I tools/hq-dashboard/test_lead_policy.py
python -I tools/hq-dashboard/test_lead_ranker.py
python -I tools/hq-dashboard/test_link_fetch.py
node tools/hq-dashboard/test_importance.js
python -I tools/hq-dashboard/test_profile.py
python -I tools/hq-dashboard/test_rubric_versions.py
python -I tools/hq-dashboard/test_data_root.py
python -I tools/hq-dashboard/test_cv_quality.py
python -I tools/hq-dashboard/test_cv_fix.py
python -I tools/hq-dashboard/test_strong_cv.py
python -I tools/hq-dashboard/test_state_path.py
python -I tools/hq-dashboard/test_search_profile.py
python -I tools/hq-dashboard/test_daily_search.py
python -I tools/hq-dashboard/test_discovery_sources.py
python -I tools/hq-dashboard/test_ai_shadow.py
python -I tools/hq-dashboard/test_review_search.py
python -I tools/hq-dashboard/test_board_reads.py
python -I tools/tracker/test_store.py
python -I tools/tracker/test_stages.py
python -I tools/tracker/test_rounds.py
python -I tools/tracker/test_fresh.py
python -I tools/tracker/test_archive.py
python -I tools/tracker/test_age_rollover.py
python -I tools/tracker/test_migrations.py
python -I tools/tracker/test_land_search.py
python -I tools/tracker/test_daily_run.py
python -I tools/tracker/test_recheck.py
python -I tools/tracker/test_judge.py
```

The suites use fictional fixtures and never call a live model. First run `python tools/hq-dashboard/port_tracker.py --backend pc`. Node is needed for the JavaScript suite. The two browser cases need Playwright, WebKit, and Git for Windows OpenSSL at the fixed path described in [SETUP.md](SETUP.md#5-build-the-page-and-run-tests); CV fitting needs Windows Arial fonts. Runtime validation of this documentation change is Unverified.

## Contributing

Want to improve the look and feel? See [CONTRIBUTING.md](CONTRIBUTING.md): it says which files are safe for design work and which (logic, backend, safety checks) need the owner.

## License

MIT. Use it, change it, tell me what broke.
