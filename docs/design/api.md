# API reference

## Now: protected PC server

**Now:** [PC server](../../tools/hq-dashboard/server.py) serves the real page. Except login, routes require a session cookie. Host must match configuration; POST requires matching Origin and JSON (login uses a form). Keep contracts intact in visual PRs.

**Now:** Examples use the bundled example workbook from [try it](try-it.md), with prerequisites below. Responses are excerpts. Substitute latest `/api/data` version for `V`, fresh canonical UUID for `R` (reuse only for identical retry), returned posting `_id` for `P`, and returned document/report ids for `D`/`K`. Never publish passwords.

**Now:** Sign in first, logout last. Copy the demo report as in try it. CV downloads and AI link/export need fictional completed analysis/drafts from offline fake-provider tests; fresh demo data has none. Submit means accepted, not completed; avoid live AI/Search for visual work. Dismiss only an inspected waiting/conflict/needs-check/failed write, using its returned id for `R`.

| Method and route | Purpose; example request → response |
| --- | --- |
| GET `/login` | Sign-in page; no body → 200 HTML form. |
| GET `/`, `/index.html` | Built page; no body → 200 HTML, or 303 to `/login` without a session. |
| POST `/api/login` | Local sign-in; form `password=<entered locally>` → 303 to `/`, session cookie. Failure: 401; throttled: 429. |
| POST `/api/logout` | End session; `{}` → 200 `{"ok":true}`, cleared cookie. |
| GET `/api/data` | Tracker snapshot; no body → 200 `{"jobs":[{"Company":"Example Cloud Co","Role":"Workflow Automation Analyst","_sheet":"Jobs","_row":2,"_id":"Jobs|2"}],"version":"V","store":"sqlite"}`. |
| GET `/api/version` | Refresh token; no body → 200 `{"version":"V"}`. |
| GET `/api/writes` | Recent saves; no body → 200 `{"writes":[]}`. |
| GET `/api/health` | Local status; no body → 200 `{"server":"running","workbookAvailable":true,"pendingWrites":0,"scan":{"state":"unknown"}}`. |
| GET `/api/ai/state` | Task/posting state; no body → 200 `{"rubric_version":<current value>,"tasks":[],"postings":[]}`. |
| GET `/api/search/state` | Recent searches and source labels; no body → 200 `{"searches":[],"sources":[{"id":"gitlab","label":"GitLab"},...]}`. `sources` is the built-in set of public company job boards defined by `SOURCES` in `tools/hq-dashboard/job_search.py`; these are real company names. |
| GET `/api/cvs` | CV list; no body → 200 `{"files":[]}`. |
| GET `/api/cvs/D` | CV download by returned id; no body → 200 Word bytes. |
| GET `/api/docs` | Documents list; no body → 200 `{"cvs":[],"letters":[],"reports":[],"evidence_notes":[]}`. |
| GET `/api/docs/D` | CV or letter by returned id; no body → 200 Word bytes. |
| GET `/api/reports/K` | Report for reading; no body → 200 `{"id":"K","kind":"report","blocks":[...]}`. |
| GET `/api/reports/K/md` | Report download; no body → 200 Markdown bytes. |
| GET `/api/reports/K/docx` | Converted report; no body → 200 Word bytes. |
| POST `/api/add` | Add lead; `{"requestId":"R","version":"V","sheet":"Jobs","Company":"Example Cloud Co","Role":"Workflow Automation Analyst"}` → 200 `{"ok":true,"row":5,"id":"Jobs|5","saved":{"My status":"Tracked"},"version":"V2","writeId":"R"}`. Row 5 is the first add in a fresh demo; use returned values afterward. |
| POST `/api/update` | Edit row; `{"requestId":"R","version":"V","sheet":"Jobs","row":2,"expect":{"Company":"Example Cloud Co","Role":"Workflow Automation Analyst"},"note":"Saved fictional note"}` → 200 `{"ok":true,"version":"V2","writeId":"R"}`. |
| POST `/api/rounds` | Add round (after moving that job to Interview): `{"requestId":"R","version":"V","sheet":"Jobs","row":3,"expect":{"Company":"Sample Data Inc","Role":"Operations Analyst"},"action":"add"}` → 200 `{"ok":true,"version":"V2","writeId":"R"}`. First POST `/api/update` with the same sheet/row/expect and `status:"Interview"`, its own fresh `R` and current `V`; refresh `V` before adding the round. Requires SQLite. |
| POST `/api/writes/dismiss` | Resolve inspected pending edit; `{"id":"R"}` → 200 `{"write":{"id":"R","status":"cancelled"}}`. This dismisses a save, not a job. |
| POST `/api/search/submit` | Start manual search; `{"requestId":"R","query":"Workflow Automation Analyst","location":"","remote":true}` → 202 `{"id":"R"}`. |
| POST `/api/ai/submit` | Queue analysis; `{"requestId":"R","kind":"analyze","posting":{"company":"Example Cloud Co","role":"Workflow Automation Analyst","link":"https://example.com/jobs/1","text":""},"images":[]}` → 202 `{"id":"R"}`. The demo URL is fictional; acceptance does not mean it can be fetched or analyzed. |
| POST `/api/ai/link` | Link reviewed posting to job; `{"id":"P","jobId":"Jobs|2"}` → 200 `{"ok":true}`. |
| POST `/api/ai/export` | Download created draft; `{"id":"P","kind":"cv"}` → 200 Word bytes; `kind:"letter"` exports its letter. Missing/invalid draft: 400. |

## Now: fields and save rules

**Now:** `/api/data` also returns `sheets` (`name`, `columns`), `statuses`, `today`, `file`, `mirrorWarning`. Preserve workbook column names. Add requires Company/Role; omitted status becomes Tracked.

**Now:** Preserve returned metadata, including `_sheet`, `_row`, `_id`, `_check`, `_checkdate`, `_rounds`. Browser-only `_open`, `_sc`, `_v` are not write fields.

**Now:** The Contacts view reads `Person`, `Where now`, `How you know them`, `Status`, `Date sent`, `Reply`, `Notes`; Board cards also read a contact's `Company` and `Role`. The bundled `Contacts` sheet has only `Person`, `Company`, `Role`, `Channel`, `Status`, `Notes`, so its card looks sparse and choosing Sent fails because `Date sent` is missing. The fictional contact may appear in screenshots.

**Now:** Update accepts `status`, `date`, `note`: jobs map to `My status`, `Date applied`, `Notes`; contacts to `Status`, `Date sent`, `Notes`. Contacts use `sheet:"Contacts"` and `expect.Person` read from demo data. Update/rounds require row, sheet, current version and matching expect. Rounds edit uses `action:"edit"`, round `id`, optional ISO `date`, `type` (60 characters), `note` (280). Only adding requires Interview.

**Now:** Add/update/rounds may return 202 queued; poll writes. Version/identity conflict or `needs_check` returns 409: inspect before retry/dismiss. Queue states: `waiting`, `applying`, `saved`, `conflict`, `needs_check`, `failed`, `cancelled`. One unresolved edit blocks another. Queued is not saved.

**Now:** After analysis, AI submit also accepts `kind:"cv"` or `kind:"letter"` with `postingId`, `requestId`; letters need `sentiment` or `withoutSentiment:true`. Analyze allows `posting.jobId` and up to five base64 JPEG/PNG images. Task and posting ids differ. Search accepts only `requestId`, `query`, `location`, `remote`.

**Now:** Draw saved, waiting, conflict, needs check, failed, signed out and unreachable states, with visible recovery messages. Downloads use returned ids, not guessed filenames: `/api/docs/<name>` resolves a hashed `id`.

## Your report's name -> real name

| Your report's name | Real name and state |
| --- | --- |
| `/api/auth/login` | **Now:** `/api/login` |
| `/api/auth/logout` | **Now:** `/api/logout` |
| `/api/auth/register`, `/api/user/me` | **Now:** Neither exists. None planned: local profiles on one install, no email accounts. **Coming:** Profile picker. |
| `/api/search/run` | **Now:** `/api/search/submit` |
| `/api/doc/<id>` | **Now:** `/api/docs/<name>` (returned id in practice), `/api/cvs` and `/api/cvs/<id>` |
| `/api/report/<id>` | **Now:** `/api/reports/<key>` |
| `/api/delete` | **Now:** None by design. |
| `/api/search/config` | **Coming:** Profile/search-settings screen; no route yet. |

**Now:** A [plain local tracker server](../../tools/tracker/server.py) also exists for a quick four-tab preview; it has no sign-in, Search or Documents.

**Coming:** Profiles, search settings, provider connections and daily scheduling have no public route contract here. Do not invent one.
