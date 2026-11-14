# Architecture

Job Search HQ uses SQLite as the tracker source of truth when a database exists beside the workbook, and otherwise uses Excel. A generated browser page reads it through a protected loopback server. Optional analysis and drafting run through a separate task journal and Codex CLI. The application never applies for jobs or sends messages.

```mermaid
flowchart LR
    Source[Tracker HTML source and hosted pieces] --> Build[port_tracker.py]
    Build --> PC[PC pc-index.html]
    Browser[PC or phone browser] --> Tunnel[Optional public HTTPS Funnel]
    Tunnel --> Server[Protected loopback server :8770]
    Browser --> Server
    PC --> Server
    Server --> Queue[Durable single-edit queue]
    Queue --> Lock[Windows workbook mutex]
    Lock --> Workbook[Excel workbook and backups]
    Server --> Tasks[AI task journal]
    Evidence[Eligible evidence and rules] --> Tasks
    Tasks --> CLI[Restricted Codex CLI]
    CLI --> OpenAI[OpenAI service]
    Server --> Files[Allowlisted work documents and state exports]
```

## Components and page builds

`tools/tracker/index.html` supplies the core views, styles, and screen behavior. `tools/hq-dashboard/port_tracker.py` combines it with `hosted/` fragments and builds only the main PC page, for both desktop and phone. `--backend pc` remains accepted; any other backend is refused.

The build writes `tools/hq-dashboard/pc-index.html`. It substitutes `pc-data.js`, adds `pc-tools.js` and `pc.css`, removes the CDN dependency, and connects to same-origin PC APIs. The generated output is ignored by Git. Exact string and pattern replacements deliberately fail when source structure drifts. The entire block between the tracker `data` and `writes` markers is replaced; view functions needed after the build must live outside it.

`tools/tracker/stages.json` is the only stage definition: Tracked, Review, Apply, Skipped, Applied, Interview, Offer, Rejected. The tracker server and page builder inline this file into one HTML placeholder. Python consumers use `stages.py`; new leads use its `NEW_ROW`. Every current status has one bucket, with Other for unknown values. Offer is green, Rejected red, and the remaining stages gray. The server accepts Rejected only from Applied, Interview, Offer or itself. The board also hides Skipped as a move target after applying.

With the server stopped, preview `python -I tools/tracker/migrate_stages.py --workbook work/applications-tracker.xlsx --dry-run`, review counts, then use `--apply`. The migration reads the neighboring database when present, otherwise Excel, and preserves contacts, other sheets and archived jobs. Apply creates a `logs/*-before-stages-*` backup, checks row counts, records database moves as `stages-01`, and rebuilds the mirror. Rebuild the PC page and restart the server afterward.

`tools/hq-dashboard/server.py` handles authentication, allowlisted routes, bounded requests, workbook access, and component loading. It reads the generated PC file on page requests. `tools/tracker/server.py` contains workbook parsing/writing and a separate plain loopback tracker on port 8765. That plain server is not a substitute for protected remote hosting. Full HQ startup takes a Windows-only instance lock; it cannot run on macOS/Linux as written.

## State and persistence

`tools/tracker/store.py` preserves ordered sheet layouts, typed rows and original row numbers in a neighboring `.sqlite3` file; `migrate_to_sqlite.py` offers a disk-free dry run and a backed-up, count-checked migration. The server selects storage on each call, uses database revisions for caching, and commits row changes and status history together. In SQLite mode Excel becomes a read-only mirror rebuilt through a verified temporary file: an Excel lock defers rebuilding until a later write, external Excel edits are copied into `logs/` with a warning, and a missing mirror is recreated. Daily `.tracker-backups` copies and workbook modification-time conflicts apply only in Excel mode; the existing writer mutex still coordinates both modes.

### Keep your data outside the app folder (optional)

Put the app in a folder named `app` inside your data folder, then create an empty `.job-search-hq-data` file in the data folder, or set `JOB_SEARCH_HQ_DATA` to that folder. Your `settings.json`, `rules.md`, `work/` and `evidence/` then live in the data folder; bundled defaults and tools stay in the app folder.

`hq_settings.data_root()` resolves private data from `JOB_SEARCH_HQ_DATA`, a `.job-search-hq-data` marker above the app, or the app folder; bundled files always use `app_root()`.

`tools/hq-dashboard/daily_search.py --profile P --output today.json` reads the profile's public boards into a SQLite backlog (default `work/search-backlog.sqlite3` in the data folder) and a new JSON landing set; `--backlog B` overrides the backlog path and `--mark-landed` records a successful landing. The Search tab uses the same board adapters and the data folder's optional `profile.json`.

| Location | Contents |
| --- | --- |
| Repository `work/` | Default `applications-tracker.xlsx`, `.tracker-backups/`, existing `CV_*.docx`, `CL_*.docx`, `report-*.md`, and optional scan logs. Ignored by Git. |
| `evidence/`, `rules.md` | Local facts and drafting rules. Only fictional `*-example-*.md` evidence is tracked. Missing rules fall back to tracked `rules.example.md`. |
| `%LOCALAPPDATA%\JobSearchHQ` by default | Salted scrypt password hash, persistent login throttle, pending write journal, career task journal, uploaded images/task files, generated Word exports, and optional startup settings/logs. `--state` must be outside the repository. |
| Browser | Session cookie; localStorage for theme/filter preferences, an unfinished posting form, and sentiment. Photos remain in memory until submitted; submitted photos are stored with tasks. |

`write_queue.py` accepts one unresolved tracker edit at a time and deduplicates request IDs. Before writing, the server checks the tracker version and expected row identity under a Windows mutex. In Excel mode, Excel or another writer holding the file leaves the edit waiting; retries run every five seconds. A changed tracker version becomes a conflict rather than a silent merge. An interrupted applying operation becomes `needs_check`; a human must inspect the tracker before dismissal or repetition.

In Excel mode the writer saves a neighboring temporary workbook, reopens it to check sheet names, makes a daily backup, and atomically replaces the original. It retains seven backup files. These backups do not cover evidence or state. The mutex coordinates cooperating writers only; it is not a general lock on Excel reading.

## Trust boundaries

The protected server binds only to `127.0.0.1` (default port 8770). Optional Funnel terminates public HTTPS and forwards to that listener. Funnel makes sign-in public; it does not authenticate application users. All authenticated visitors share one password and the same read/write/AI authority.

Requests require an exact Host; POSTs also require the configured Origin. Cookies are Secure, HttpOnly, and SameSite=Strict. Sessions last 12 hours in memory; the account-wide five-failure/15-minute throttle persists on disk. Local unlock clears it. A same-origin content security policy, route allowlists, document path/size checks, request limits, and formula/text checks constrain inputs. They do not protect against a compromised local account or replace private backups. Loopback HTTP cookie behavior needs browser verification.

Posting text, photos, and sentiment are untrusted input. `ai_tasks.py` builds typed prompts from eligible evidence and rules and runs one task at a time. The CLI uses ChatGPT authentication, a read-only sandbox, disabled shell/plugins/hooks/web search, and ephemeral execution. API-key environment variables are removed from the child. Rules, eligible evidence, posting data, photos, and sentiment still leave the PC for OpenAI. CLI compatibility, usage, and per-task cost require installation-specific validation.

## Evidence-first drafting and limits

Only eligible `E-*.md` entries marked verified/stated and public-safe, with usable CV-ready wording, enter the prompt. Optional `- role: Title | Company | Dates` lines supply job headings alongside the CV-ready wording. This filters the owner's labels; it does not verify those labels or facts. Draft validation checks cited-ID eligibility and the presence of listed claim strings. It does not establish complete factual-claim coverage or whether evidence supports a claim. Offline tests explicitly demonstrate acceptance of unlisted factual prose. Every draft needs human truth review.

Running AI tasks become interrupted after restart and are not blindly rerun; already queued tasks can still run. Word export code removes defined note markers and keeps results as drafts in state. Existing document listings read `work/` separately; exports are not automatically enrolled there. CV fitting uses Windows Arial metrics and deletes lines/sentences without rewording; actual Word/Pages pagination still needs visual review. On-demand PC search and a daily scan runner are not supplied.

## Test strategy

Windows CI uses Python 3.12, installs requirements plus Playwright/WebKit, builds PC HTML, and runs `test_ai_tasks.py`, `test_cv_onepage.py`, and `test_pc_server.py`. These cover fake-provider task validation, claim-map limits, document conversion/layout invariants, queue recovery, authentication, Host/Origin checks, request bounds, workbook conflicts, downloads, and browser flows. Browser geometry is checked at four widths including 375 px; local HTTPS tests require a fixed Git for Windows OpenSSL executable. CV fitting loads fixed Windows font paths.

No suite requires a live model or private career data. `check_codex_worker.py` is a separate live, usage-consuming check. Tests do not establish public tunnel behavior, boot recovery, real-device usability, or actual Word rendering. Runtime checks for this documentation change are **Unverified**; see [SETUP Known gaps](../SETUP.md#known-gaps).
