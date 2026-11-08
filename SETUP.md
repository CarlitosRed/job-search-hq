# Setup guide

Job Search HQ is a personal job tracker with a browser interface, an Excel workbook, and optional AI drafting. This guide is for someone running a private Windows installation or contributing UI changes with fictional data. It never applies for jobs or sends messages. UI contributors can use [the quickstart](docs/CONTRIBUTOR-QUICKSTART.md); [the architecture guide](docs/ARCHITECTURE.md) explains the data flow.

Run commands from the repository root. Choose your shell's block. Commands were checked against source, but are **Unverified** at runtime in this documentation change: the builder was instructed not to run Python or Git. See [Known gaps](#known-gaps).

| Requirement | Purpose and limits |
| --- | --- |
| Windows 11 | Target for the full protected server. Startup and workbook writer locks require Windows. Other Windows versions are Unverified. |
| Python 3.12 | Version used by Windows CI. Include the Python launcher on Windows. |
| Git | Clone, update, and contribute. Browser tests require Git for Windows at its default location, including `C:/Program Files/Git/usr/bin/openssl.exe`. |
| Modern browser | Local UI and phone access. Browser tests use WebKit. Local HTTP login depends on browser handling of Secure cookies on loopback. |
| Excel (optional) | Inspect or edit the workbook. Close it before saving through HQ. Not needed for workbook creation or tests. |
| Codex CLI with ChatGPT access (optional) | Live analysis and drafting. Not needed for UI work or offline tests. Uses account allowance; see step 7. |
| Tailscale (optional) | HTTPS phone access. Funnel exposes sign-in publicly; see step 8. |
| macOS / Linux | Can edit UI, build HTML, create fictional data, and preview the original tracker without writes. The protected server cannot start because its instance lock requires Windows. Full tests also need Windows locks, a fixed Windows OpenSSL path, and Windows Arial fonts. A Mac preview is not full PC validation. |

## 1. Get the code

For a private installation, in PowerShell:

```powershell
git clone https://github.com/CarlitosRed/job-search-hq.git
cd job-search-hq
```

Git Bash or macOS Terminal:

```bash
git clone https://github.com/CarlitosRed/job-search-hq.git
cd job-search-hq
```

For a contribution, first use GitHub's **Fork** button. Replace `YOUR-GITHUB-USER` with your account. These Git commands also work in PowerShell:

```bash
git clone https://github.com/YOUR-GITHUB-USER/job-search-hq.git
cd job-search-hq
git remote add upstream https://github.com/CarlitosRed/job-search-hq.git
git switch -c ui/describe-your-change
```

Keep contribution clones separate from private installations.

## 2. Create a virtual environment and install dependencies

PowerShell on Windows:

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Git Bash on Windows:

```bash
py -3.12 -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
```

macOS / Linux, with Python 3.12 installed:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Requirements are `openpyxl`, `python-docx`, and `Pillow`; versions are unpinned. Playwright is optional for the app but required for both browser cases in the full PC suite. These commands work in either shell:

```powershell
python -m pip install playwright
python -m playwright install webkit
```

Downloads need internet access. Activate the environment in each new terminal. `.venv/` is git-ignored here: never stage or commit it. If PowerShell blocks activation, use the interpreter directly without changing machine-wide policy:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Use that interpreter in place of `python` in later Windows commands if activation was skipped.

## 3. Fictional demo data in 60 seconds

This is the shortest documented route after installation; timing is **Unverified**. The bundled `examples/applications-tracker.xlsx` contains three fictional jobs and one fictional contact. No demo generator is bundled. As an alternative, this snippet creates the reader's expected layout and refuses to overwrite an existing workbook. Use a fresh clone containing fictional evidence only.

PowerShell:

```powershell
@'
from pathlib import Path
from datetime import date
from openpyxl import Workbook
p = Path('work/applications-tracker.xlsx')
assert not p.exists(), 'Workbook already exists; use a fresh demo clone.'
p.parent.mkdir(parents=True, exist_ok=True)
wb = Workbook()
ws = wb.active
ws.title = 'Fictional jobs'
ws.append(['#', 'Company', 'Role', 'Where', 'Posted', 'Score', 'Verdict', 'My status', 'Date applied', 'CV used', 'Source', 'Link', 'Notes'])
ws.append([1, 'Example Company', 'Example Analyst', 'Remote', '', '3.5', 'CONSIDER', 'Suggested (not applied)', '', '', 'Fictional example', 'https://example.com/job', 'Demo only'])
ws.append([2, 'Sample Company', 'Example Coordinator', 'Remote', '', '4.0', 'APPLY', 'Applied', date.today(), '', 'Fictional example', 'https://example.com/role', 'Demo only'])
contacts = wb.create_sheet('Contacts')
contacts.append(['Person', 'Where now', 'How you know them', 'Status', 'Date sent', 'Reply', 'Notes'])
contacts.append(['Example Contact', 'Example Company', 'Fictional example', '', '', '', 'Demo only'])
wb.save(p)
wb.close()
print('Created fictional workbook:', p)
'@ | python -
Copy-Item examples\report-example-shortlist.md work\report-example-shortlist.md
Copy-Item examples\CL_Example_Cloud_Co_Workflow_Automation_Analyst.docx work\CL_Example_Cloud_Co_Workflow_Automation_Analyst.docx
python -I tools/hq-dashboard/port_tracker.py --backend pc
```

Git Bash or macOS Terminal:

```bash
python - <<'PY'
from pathlib import Path
from datetime import date
from openpyxl import Workbook
p = Path('work/applications-tracker.xlsx')
assert not p.exists(), 'Workbook already exists; use a fresh demo clone.'
p.parent.mkdir(parents=True, exist_ok=True)
wb = Workbook()
ws = wb.active
ws.title = 'Fictional jobs'
ws.append(['#', 'Company', 'Role', 'Where', 'Posted', 'Score', 'Verdict', 'My status', 'Date applied', 'CV used', 'Source', 'Link', 'Notes'])
ws.append([1, 'Example Company', 'Example Analyst', 'Remote', '', '3.5', 'CONSIDER', 'Suggested (not applied)', '', '', 'Fictional example', 'https://example.com/job', 'Demo only'])
ws.append([2, 'Sample Company', 'Example Coordinator', 'Remote', '', '4.0', 'APPLY', 'Applied', date.today(), '', 'Fictional example', 'https://example.com/role', 'Demo only'])
contacts = wb.create_sheet('Contacts')
contacts.append(['Person', 'Where now', 'How you know them', 'Status', 'Date sent', 'Reply', 'Notes'])
contacts.append(['Example Contact', 'Example Company', 'Fictional example', '', '', '', 'Demo only'])
wb.save(p)
wb.close()
print('Created fictional workbook:', p)
PY
cp examples/report-example-shortlist.md work/report-example-shortlist.md
cp examples/CL_Example_Cloud_Co_Workflow_Automation_Analyst.docx work/
python -I tools/hq-dashboard/port_tracker.py --backend pc
```

On Windows, continue to step 4 to open the protected page. On macOS / Linux, preview the original four-view tracker instead:

```bash
python -I tools/tracker/server.py --no-browser
```

Leave it running. On macOS, open a second terminal:

```bash
open http://127.0.0.1:8765
```

On Linux, type that URL in the browser. Stop with Ctrl+C. This preview reads the workbook, but writes require Windows. It has no password and lacks PC Search and Documents additions. Keep it on loopback; never tunnel the plain tracker. Generated PC HTML requires its API and is not a standalone demo.

## 4. Set the password and start the Windows server

Choose a unique password of **16 to 512 characters**. The prompt asks twice without echoing it. Never put a password in a command, screenshot, or repository file.

```powershell
python -I tools/hq-dashboard/server.py --set-password
python -I tools/hq-dashboard/server.py
```

The server reports `HQ listening only on 127.0.0.1:8770. Expected browser origin: http://127.0.0.1:8770`. Leave it running. In a second PowerShell window:

```powershell
Start-Process 'http://127.0.0.1:8770'
```

In Git Bash, open that URL manually. Sign in and check for two fictional jobs. Ctrl+C stops the foreground server.

Default state is `%LOCALAPPDATA%\JobSearchHQ`, outside the checkout. `--state` selects another directory outside the repository; use it consistently for password setup, starting, and unlocking. `--workbook` selects another workbook. `--port` changes the listener but does not update the default origin.

An origin is the scheme, hostname, and optional port, without path, query, fragment, or credentials. The server requires exactly one Host matching its authority, and every POST needs that exact Origin. `localhost` and `127.0.0.1` differ. Non-loopback origins require HTTPS. For another local port, change both options:

```powershell
python -I tools/hq-dashboard/server.py --port 8771 --origin http://127.0.0.1:8771
```

Cookies are always Secure, HttpOnly, and SameSite=Strict, even for local HTTP. If the browser returns to sign-in, use step 8's HTTPS route; do not weaken cookie settings. Sessions last 12 hours and disappear on restart. Restart after changing the password.

Five failed logins cause a persistent, account-wide 15-minute lockout. It is not per IP; another visitor can lock everyone out. Wait, or clear it locally without restarting:

```powershell
python -I tools/hq-dashboard/server.py --unlock
```

## 5. Build the page and run tests

Run from the activated Windows environment. These commands also work in Git Bash:

```powershell
python -I tools/hq-dashboard/port_tracker.py --backend pc
python -I tools/hq-dashboard/test_ai_tasks.py
python -I tools/hq-dashboard/test_cv_onepage.py
python -I tools/hq-dashboard/test_pc_server.py
```

The build prints `wrote`, the `tools/hq-dashboard/pc-index.html` path, and a byte count. Each test command should exit successfully and end in `OK`, following `Ran ... tests in ...s`. Errors, missing dependencies, and failures are not passes. **Unverified** duration: no times were measured for this change. The PC suite deliberately waits for deadlines and browser interactions; record actual durations in the PR.

Tests use temporary fictional workbooks, evidence, documents, fake AI responses, and local HTTP/HTTPS. They never call a live model. Both `test_tab_geometry_is_stable` and `test_phone_browser` require Playwright, WebKit, and the fixed OpenSSL executable from the requirements table. Geometry is tested at 1440, 1280, 1024, and 375 px. CV fitting also loads Windows Arial files. Mac contributors should attempt the commands, report platform failures honestly, and obtain full Windows validation from the owner. A preview is not a passing PC suite.

## 6. Bring your own data

Back up first. Keep private data in a personal installation, not a contribution clone.

**Workbook.** Default: `work/applications-tracker.xlsx`. A job sheet has a row-1 header `My status`. Keep `#` first, `Company` second, and `Role` third: the reader skips rows with an empty second cell.

| Columns | Purpose |
| --- | --- |
| `#`, `Company`, `Role` | Identifier and job identity. Company and role are required when adding a lead. |
| `Where`, `Posted`, `Score`, `Verdict` | Display and filtering; use a numeric-looking score such as `3.5`. |
| `My status`, `Date applied`, `Notes` | Editable fields; dates use `YYYY-MM-DD`. |
| `CV used`, `Source`, `Link` | Document reference and posting context; links start with HTTP or HTTPS. |
| `Open check YYYY-MM-DD` (optional) | Reader selects the newest dated check matching a recognized status (OPEN, CLOSED, REDIRECTED, UNKNOWN), falling back to the newest nonempty value only when none match. |

Stored statuses are exactly `Suggested (not applied)`, `Message drafted`, `CV ready`, `Applied`, `Interview`, `Rejected`, `Offer`, and `Skipped`. UI Interviewing corresponds to stored `Interview`. Applications means Applied + Interviewing + Offer + Rejected. Skipped is not an application. Each job counts once by its current status; charts do not reconstruct history.

Contacts use a sheet named `Contacts`, with `Person` first, then `Where now`, `How you know them`, `Status`, `Date sent`, `Reply`, `Notes`. Keep headers unchanged. The protected writer rejects formula-like text starting with `=` after whitespace/control characters and text exceeding 32,767 characters or UTF-16 units.

**Evidence.** Remove or replace the entire fictional bank before drafting real career material, or eligible fictional entries will remain available to the model. Use unique `E-###` IDs and filenames such as `evidence/E-101-workflow.md`; the first five filename characters are the ID:

```markdown
# E-101 workflow
- level: stated
- public-safe: yes
- CV-ready bullets: [Replace with a reviewed factual sentence permitted in a public CV.]
- limit: [State exactly what this evidence does not establish.]
```

Replace bracketed instructions before use. The loader reads `E-*.md` only. It includes `verified` or `stated` entries with `public-safe: yes` and nonempty `CV-ready bullet:` or `CV-ready bullets:` wording that does not start with `Unknown` or `Not CV-ready`. `unverified` and `public-safe: no` exclude entries. Conditions after `yes` and optional limits accompany eligible wording. These labels record your assessment; the app does not verify the facts.

**Rules.** Copy the example and edit private instructions. PowerShell:

```powershell
Copy-Item rules.example.md rules.md
```

Bash:

```bash
cp rules.example.md rules.md
```

If `rules.md` is absent, the worker falls back to `rules.example.md`. Rules are sent with every analysis or drafting task.

**Documents.** The PC list reads files directly in `work/`: `CV_*.docx`, `CL_*.docx`, and `report-*.md`. Word files are limited to 5 MiB and reports to 1 MiB; symlinks are excluded. CV and letter suffixes establish pairs. AI exports instead go to the state folder's `generated-documents/` with `CV_...` or `Cover_Letter_...` names. They download from the drafting result and are not automatically added to the `work/` list. Review before deliberately copying/renaming a letter to `CL_...` for that list.

**Git exclusions.** `.gitignore` excludes `work/`, `.env*`, `*.sqlite3`, Python caches, generated HQ `index.html` and `pc-index.html`, `rules.md`, and evidence except `evidence/*-example-*.md`. Word files are ignored except `examples/*.docx`. A private evidence filename containing `-example-` can therefore be tracked: reserve that pattern for fiction. Ignoring a path does not untrack existing files. Never stage `.venv/`, private data, or credentials. Inspect staged changes and avoid blanket staging. Never open or commit `.env*` files.

## 7. Optional AI worker

No AI login is needed for UI work or offline tests. For live use, follow the Windows installation instructions in the [official Codex CLI guide](https://learn.chatgpt.com/docs/codex/cli) and choose **Sign in with ChatGPT**, using the Windows account that will run HQ. Restart the terminal/server after installation so Codex is on PATH. The repository does not pin a CLI version or choose a model; installed-release compatibility is **Unverified** until tested.

The worker invokes a read-only sandbox with shell tool, plugins, hooks, and web search disabled, using ephemeral execution. It strips `OPENAI_API_KEY` and `CODEX_API_KEY` from the child environment. It sends rules, eligible evidence wording/conditions/limits, posting fields and supplied photos, and letter sentiment to OpenAI through the CLI. Drafting also includes the saved posting record. It does not fetch posting links. These restrictions do not keep submitted data offline.

Each analysis, CV, or letter is a separate call. One task can wait or run at a time; subprocess timeout is 600 seconds. There is no application spending cap, usage meter, or cost estimate. Check your account's allowance and additional usage terms first. Exact cost per task is **Unverified** and depends on account, model, and input. This optional smoke check consumes live usage but bypasses the evidence bank and sends only a fictional connectivity prompt:

```powershell
python -I tools/hq-dashboard/check_codex_worker.py
```

Success prints `ChatGPT CLI worker connectivity: passed. No real posting, evidence or document was sent.` Do not include it in offline checks. Analysis requires 200 characters of text or photos. Server limits are five JPEG/PNG photos, at most 8 MiB and 16 million pixels each; the UI can resize or slice inputs first.

The app checks eligible cited IDs and that listed claim strings occur in the draft. It does not establish that every factual sentence is listed or that evidence supports each claim. Review every line. The CV prompt asks for at most 520 words; the validator rejects over 600. Page fit is an Arial-metric estimate, not a Word rendering guarantee. To trim an existing Markdown CV by deleting lines/sentences only:

```powershell
python -I tools/hq-dashboard/cv_onepage.py work/CV_example.md work/CV_example-1page.md work/CV_example-1page.docx
```

Supply your reviewed source file first; the demo does not create it. Inspect the exported layout before use.

## 8. Phone access with Tailscale and Funnel

Funnel exposes the app publicly over HTTPS. Anyone with the URL can reach sign-in. A signed-in visitor can read tracker data/documents, edit, and request AI tasks. There is one shared password, not separate accounts. Keep the listener on loopback; do not forward router ports or expose the plain tracker on 8765.

1. Install Tailscale on Windows, sign in, and keep it running. Phone installation is useful for private tailnet access; a public Funnel URL does not require the phone to join the tailnet.
2. Enable MagicDNS, HTTPS, and permission to use Funnel in the tailnet admin settings/policy. Follow the [official prerequisites](https://tailscale.com/docs/features/tailscale-funnel). HTTPS certificate names are public, so choose a device name suitable for publication.
3. In an Administrator PowerShell terminal, inspect `Self.DNSName`. Remove its final dot and substitute it for the placeholder below:

```powershell
tailscale status --json
```

4. Stop the local server with Ctrl+C. In the usual activated environment, run:

```powershell
python -I tools/hq-dashboard/server.py --origin https://YOUR-PC.YOUR-TAILNET.ts.net
```

5. In the Administrator terminal, enable forwarding and confirm the resulting URL:

```powershell
tailscale funnel --bg 8770
tailscale funnel status
```

6. Open that exact HTTPS address on the phone and PC, sign in, and inspect fictional jobs first. The proxy must preserve public Host and Origin. Loopback browser access now fails Host validation. Keep the PC awake and online.
7. After five failed logins, wait 15 minutes or run step 4's local unlock command against the same state directory. Throttling survives restarts; sessions last 12 hours. The health label saying AI is configured is not proof of connectivity.

The [Funnel CLI reference](https://tailscale.com/docs/reference/tailscale-cli/funnel) covers persistence and shutdown. Remove this forwarding rule with:

```powershell
tailscale funnel --bg 8770 off
tailscale funnel status
```

For tailnet-only HTTPS, consider Tailscale Serve and its separate policy/device setup. Fresh-device phone, proxy, and reboot behavior are **Unverified** for this documentation change.

## 9. Start at boot on Windows

The helper requires Tailscale at its normal Program Files location and signed in. It derives an HTTPS Tailscale origin; it is not a loopback startup wizard. Complete steps 4 and 8 first. Activate the virtual environment so the helper records its interpreter:

```powershell
& .\.venv\Scripts\Activate.ps1
& .\tools\hq-dashboard\prepare-startup.ps1
```

The helper writes `%LOCALAPPDATA%\JobSearchHQ\server-settings.json` and `work\pc-hosting\JobSearchHQ-Server.xml`. It does **not** register a task. If scripts are blocked, and your computer policy permits it, use a process-scoped policy and rerun:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
& .\tools\hq-dashboard\prepare-startup.ps1
```

Review the XML interpreter, working directory, identity, and arguments. In Task Scheduler, **Import Task**, select the XML, and keep the same Windows account used for HQ password setup and Codex. The definition uses password logon, least privilege, a boot trigger delayed 30 seconds, no execution time limit, and three one-minute restart attempts. Enter a Windows password only in Task Scheduler's native prompt.

Stop any foreground instance, then **Run** `JobSearchHQ-Server`. It launches `run-server.py` directly without PowerShell activation. Logs go to `%LOCALAPPDATA%\JobSearchHQ\server.log`; a log over 2 MiB rotates to `server.log.previous` at launch. Reboot and test phone sign-in before relying on it. Moving the checkout or recreating the environment requires preparing/importing the task again. Pre-login Tailscale and CLI readiness are **Unverified**.

## 10. Update, back up, and uninstall

Stop HQ and its scheduled task, close Excel, and inspect unresolved edits before updating or copying data.

| Back up privately | Contents |
| --- | --- |
| `work/` | Workbook, `.tracker-backups/`, CVs, letters, reports, logs, and any local automation you added. |
| `evidence/`, `rules.md` | Private facts and writing instructions. |
| `%LOCALAPPDATA%\JobSearchHQ\` or custom state | Copy the entire folder while stopped: `password.json`, `login-throttle.sqlite3`, `pending-writes.sqlite3`, `career-tasks.sqlite3`, `ai-tasks/`, `generated-documents/`, settings, logs, and any SQLite sidecar files. |
| Browser site data, if needed | Theme/filter preferences, unfinished review form, and sentiment use localStorage. They are not in the server backup; do not export them into Git. |

State includes sensitive photos, postings, drafts, and a password hash. Keep backups access-controlled. Codex manages sign-in separately; reauthenticate on a replacement computer rather than sharing credentials. Workbook saves make one pre-write backup per day and retain the latest seven backup files. That does not protect evidence or journals.

For a clean personal clone on `main`, after reviewing local changes:

```powershell
git status --short
git switch main
git pull --ff-only
python -m pip install -r requirements.txt
python -I tools/hq-dashboard/port_tracker.py --backend pc
python -I tools/hq-dashboard/test_ai_tasks.py
python -I tools/hq-dashboard/test_cv_onepage.py
python -I tools/hq-dashboard/test_pc_server.py
```

Restart after validation. Do not discard changes to force an update. Fork contributors should fetch upstream and integrate its `main` using their normal contribution workflow.

Optional export copying uses `HQ_COPY_DIR`, set in the environment of the process running HQ. A terminal setting does not configure an existing scheduled task:

```powershell
$env:HQ_COPY_DIR = 'C:\YOUR-PRIVATE-BACKUP\HQ'
```

Its parent directory must exist. New Word exports are copied under `Drafts from HQ` without overwriting existing copies. This is not a full backup.

To uninstall, disable the Funnel rule from step 8, end/delete `JobSearchHQ-Server` in Task Scheduler, and stop foreground instances. Back up, then remove the checkout, virtual environment, and selected state directory through File Explorer if no longer needed. Remove export copies and browser site data separately. Codex and Tailscale are independent installations; retain them if used elsewhere.

## 11. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| 404 even at sign-in | Host differs from configured origin, or route is unknown. | Use the exact configured URL; do not substitute localhost, LAN IP, or loopback after configuring HTTPS. |
| 403 on sign-in/save | Missing or wrong Origin. | Open HQ directly at the configured origin and preserve proxy Host/Origin headers. |
| Returns to sign-in | Secure cookie not sent on local HTTP, expired session, or restart. | Use configured HTTPS and sign in again. Browser-specific loopback behavior is Unverified. |
| Locked sign-in / 429 | Account-wide throttle after five failures. | Wait 15 minutes or use the local unlock command with the same state directory. |
| Password setup fails | Length or confirmation mismatch. | Use 16–512 characters and identical confirmation; restart after a change. |
| Waiting for Excel or scan | Excel lock file or another writer owns the workbook. | Close Excel or let the writer finish. Queue retries every five seconds; do not resubmit blindly. |
| Conflict / an edit is waiting | Workbook changed or unresolved edit blocks the queue. | Inspect the workbook, reload, dismiss the checked item, and re-enter the intended edit. |
| Save unconfirmed / interrupted | Shutdown during apply or ambiguous failure. | Inspect the workbook before dismissing/repeating; the edit may already be present. |
| Port in use / WinError 10048 | Another process uses 8770. | Stop the known instance or change both port and origin as in step 4. Update proxy/startup configuration too. |
| Writer lock error on launch | Duplicate instance with the same state, or non-Windows OS. | Stop duplicate HQ; use Windows for the full server or Mac's read-only preview. |
| No workbook / cannot read spreadsheet | Missing, damaged, inaccessible, or wrong path. | Copy the bundled `examples/applications-tracker.xlsx` to `work/applications-tracker.xlsx`, create step 3's fixture, or pass a valid workbook path. |
| First run port_tracker.py message | Generated PC HTML missing. | Run the build in step 5. |
| Build fails with expected-string error | Exact source marker/replacement changed. Errors include `port: expected ... match(es)` or `port: pattern matched ... times`. | Restore the expected source or tell the owner exactly which anchor string changed so the owner can adjust `port_tracker.py`. Contributors must not edit the script. Do not use stale generated HTML. |
| Playwright module/browser missing | Package or WebKit not installed in this environment. | Run both step 2 install commands with the same interpreter as the tests. |
| Browser TLS test cannot find OpenSSL | Fixed Git for Windows path missing. | Install Git for Windows in its default path. Mac does not satisfy this fixture. |
| Cannot open resource in CV tests | Fixed Windows Arial font paths unavailable. | Verify Arial on Windows. Cross-platform fitting needs an owner-approved code change. |
| Execution policy blocks scripts | PowerShell blocks activation/helper. | Use the environment interpreter directly; see step 9 for the helper and respect managed policy. |
| AI fails / no eligible evidence | CLI missing/incompatible, signed out, usage exhausted, or bank excluded. | Check step 7 and evidence format. Health is not a connectivity test. Do not bypass permissions. |
| Search disabled / scan status unknown | No on-demand search integration or bundled scan runner. | Manual tracking and posting review still work; automation needs separate setup. |
| Document missing | Wrong prefix/location, oversized file, symlink, or export only in state. | Follow step 6's limits; review before copying an AI export into `work/`. |

## Known gaps

Maintainer checklist before merge:

1. **Demo asset and launcher.** The bundled `examples/applications-tracker.xlsx` contains three fictional jobs and one fictional contact and can be copied to `work/applications-tracker.xlsx`. No single demo command exists. The inline fixture is an alternative; both shell variants and the 60-second target are **Unverified**. Run them in fresh clones and consider a supported generator separately.
2. **Runtime validation and timings.** Python and Git execution were prohibited for this change. Installation, build, tests, update, screenshots, and cleanup sequences are **Unverified** at runtime. Follow the guide in fresh Windows and Mac clones, record test counts/times, and verify local links before merge.
3. **Platform portability.** Windows mutexes, fixed Git OpenSSL, and fixed Windows Arial paths prevent complete Mac/Linux validation. Verify the limited Mac tracker preview and obtain Windows test/screenshot evidence for PC additions.
4. **Local HTTP login.** Cookies are always Secure; automated browser coverage uses HTTPS. Browser acceptance of loopback HTTP is **Unverified**. Verify supported browsers or provide a supported local HTTPS route separately.
5. **Dependencies and worker.** Packages and CLI are unpinned, while worker flags are specific. Compatible versions, live-call success on a fresh install, account allowance, and per-task cost are **Unverified**. Record tested versions; keep paid checks outside offline tests.
6. **Phone and boot.** Funnel commands follow official guidance, but fresh-device proxy headers, sign-in, reboot recovery, and pre-login Tailscale/Codex readiness are **Unverified** here. Confirm task interpreter and account.
7. **Automation and exports.** No daily scan runner or hosted-store sync service is included. On-demand PC search is disconnected. Export copying is not backup; AI exports do not automatically enter the `work/` Documents list.
8. **Draft truth and pagination.** Evidence-map checks do not prove claim coverage or support. Page fit uses font metrics; actual Word/Pages rendering is **Unverified** here. Human wording and layout review remain necessary.
9. **Contributor policy alignment.** The contributor guides now state three review rounds. The unchanged owner review guide and PR template do not state this; the maintainer should align those separate files before merge. Contributors must not edit `tools/hq-dashboard/port_tracker.py`. If a UI edit changes an anchor string that the build expects, report exactly which string changed so the owner can perform any port-script adjustment.
