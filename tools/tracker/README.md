# Local tracker



The local web page reads and writes the tracker at `work/applications-tracker.xlsx`,
or its neighboring SQLite store after migration.
Keep personal lead and contact data out of version control.

- From the repository root, run `tools\tracker\open-tracker.bat` or `python -I tools/tracker/server.py`. The default address is http://127.0.0.1:8765.
  The plain tracker shows **Not scored yet** even on judged jobs, and the star is disabled.
  For saved scores and stars, use the [PC dashboard setup](../../SETUP.md),
  then launch it with `python -I tools/hq-dashboard/server.py`.
- Override the workbook with `TRACKER_XLSX` and the port with `TRACKER_PORT`. For testing, select a disposable fictional workbook.
- The page records progress. A person submits applications and sends messages.
- Follow [selftest.md](selftest.md) to check counts, flow and percentages.

## Board stages

`stages.json` defines Tracked, Review, Apply, Skipped, Applied, Interview, Offer,
Rejected, in board order. New rows start in Tracked. Offer is green, Rejected is
red, and the rest are gray. Unknown values stay in Other status. Rejected is
allowed only from Applied, Interview, Offer or itself; other backward moves are
allowed. The board also hides Skipped as a target after applying.

Desktop and phone use the same main page, built with
`python -I tools/hq-dashboard/port_tracker.py --backend pc`. This is the only
supported backend. Both this builder and the plain tracker server inline the
stage JSON into the shared HTML source.

Stop the server, preview the migration and review the per-sheet counts first:

```powershell
python -I tools/tracker/migrate_stages.py --workbook work/applications-tracker.xlsx --dry-run
python -I tools/tracker/migrate_stages.py --workbook work/applications-tracker.xlsx --apply
```

Dry run writes nothing. Apply backs up to the workbook folder's `logs/` directory
as `<stem>-before-stages-<time>.sqlite3` or `.xlsx`. The neighboring database wins
when present. Known legacy values are mapped using `stages.json`; an unscored
legacy review row becomes Tracked. Unknown statuses, contacts, other sheets and
archives stay unchanged. Input and output counts must match. Database changes
commit together, record one `stages-01` move per changed job and a new revision,
then rebuild the Excel mirror. Workbook changes use a verified temporary file.
A second run changes zero statuses. Rebuild the PC page and restart the server.

## SQLite store

Interview rounds are database only. Entering Interview adds Round 1 with today's
date if the job has no rounds yet. The drawer lets each round's date, type and
short note be edited in any stage; Add round is available while in Interview.
The Excel mirror has a last `Interview rounds` column on each job sheet, with
one readable line per round. This column is never read back into the tracker.

When a neighboring `applications-tracker.sqlite3` exists, it becomes the tracker
store automatically. Without that file, the tracker continues to use Excel.
Try migration on a disposable copy first, checking every input/output count:

```powershell
python -I tools/tracker/migrate_to_sqlite.py --workbook work/applications-tracker.xlsx --old work/applications-tracker-old.xlsx --dry-run
python -I tools/tracker/migrate_to_sqlite.py --workbook work/applications-tracker.xlsx --old work/applications-tracker-old.xlsx --apply
```

Omit `--old` if there is no archive. Dry run writes nothing. Apply backs up the
source workbooks into `logs/`, checks the database counts, and refuses to replace
an existing database. The source workbooks are left untouched during migration.

After migration, Excel is a read-only mirror of the site's data. Site writes
commit to SQLite, then rebuild the mirror. If Excel has the workbook open, the
mirror waits for the next site write. Edits made in Excel are preserved in a
dated `logs/*-edited-*.xlsx` copy on the next rebuild, and the data API includes
a warning naming that copy. Excel edits are never silently merged into SQLite.
Keep the database and the workbook backups outside version control.

## Job dates

First seen records when a job was added and never changes. Posted on is a real
date read from Posted, with its source: greenhouse, lever, ashby, linkedin, page
or first_seen when no posted date is available. Dates marked "about", LinkedIn,
Ashby and First seen are estimates. Age uses the older of Posted on and First
seen, so a repost cannot make an old job look new.

The scan window is 45 days, including day 45; unknown ages stay in the window.
Applied, Interview and Offer jobs are still rechecked outside it. The shared
duplicate key is company, role and place, normalized for Unicode, case, spaces
and punctuation. Only exact keys match, including keys from Old. Company and
role are required; an empty place is allowed and matches only another empty place.

Preview the date fill, then apply it after reviewing the JSON report:

```powershell
python -I tools/tracker/migrate_dates.py --workbook work/applications-tracker.xlsx --dry-run
python -I tools/tracker/migrate_dates.py --workbook work/applications-tracker.xlsx --apply
```

Dry run uses an in-memory database copy and writes nothing. Apply first backs
up the database to `logs/<stem>-before-dates-<time>.sqlite3`, then fills only
empty dates in jobs and Old in one transaction. First seen falls back through
the sheet date (From sheet for Old), earliest populated Open check date,
Posted on and today. The revision increases once if any values were filled;
a second run fills zero. The report counts jobs eligible for the 60-day archive
without moving them. Statuses and the Excel copy stay unchanged. This command
requires an existing database; Excel-only mode returns a message.

## Archive and Posted filter

Preview the archive move and review its JSON list before applying:

```powershell
python -I tools/tracker/archive_old.py --workbook work/applications-tracker.xlsx --dry-run
python -I tools/tracker/archive_old.py --workbook work/applications-tracker.xlsx --apply
```

Dry run is the default, uses an in-memory copy and writes nothing. Apply first
backs up the database to `logs/<stem>-before-archive-<time>.sqlite3`, then moves
jobs older than 60 days in Tracked, Review, Apply or Skipped in one transaction.
Applied, Interview, Offer and Rejected never move; exactly 60 days and unknown
ages stay. Dates, history, interview rounds and duplicate keys are kept. Source
row positions stay unchanged, archived ids remain reserved, and document records
follow the job to its Old row. Columns are matched by name, with missing columns
appended to Old. Nothing is deleted. Revision increases once only when jobs move;
the Excel mirror is then rebuilt with Old after all main sheets. The site never
shows Old, including in Excel-only mode. The archive command requires SQLite.
A second run moves zero jobs.

The Posted filter offers Last 3 days, 1 week, 2 weeks, 3 weeks, 4 weeks and All
(default), using age <= 3, 7, 14, 21 or 28 days. Unknown ages appear only under
All. The selection is saved, counted in the filter badge, and shown as a removable
chip; Clear filters resets it. It applies to Board, List and the filtered
Overview. Board and List cards show posting age, or First seen when no posted
date is available, with "about" for estimates. Unknown ages show no age line.

## Daily search runner

Run `python -I tools/tracker/daily_run.py` to search and land the latest daily
slot, normally at 06:00 local time. The data folder comes from
`JOB_SEARCH_HQ_DATA` or the usual data-folder marker. Configure `daily_run.at`,
`daily_run.before` and `daily_run.copies` in its `settings.json`; each step needs
a name, owner, destination and command as a list of strings. Commands run without
a shell, with a ten-minute timeout. A failed before step stops the search;
failed copies and unavailable individual boards produce warnings.

The runner keeps its phase record in `work/daily-runs.sqlite3` and search
snapshots in `work/daily-runs/`. An OS lock permits one runner at a time. A
successful slot is skipped; an interrupted or failed slot resumes, reusing a
completed search snapshot. `--force` starts another run, `--now` accepts a local
ISO date and time, and `--trigger` accepts scheduled, catch-up or manual. Run it
on PC startup with `--trigger catch-up` to catch up a missed slot. The Jobs view
shows the last search, missed days and any failure or warning. Judging runs
after the re-check (see the qualification judge below).

<!-- # Card 03: closing and re-check. -->
After landing, re-checking records board reads and posting observations. Two
misses on complete reads on different days close a posting; 404 or 410 closes
it immediately. Closing text needs two days. Sightings reopen postings. Stages
and job rows stay as they are, and nothing is deleted. Empty boards and drops
below half the last complete count provide no information. Boards without a
complete read for three days appear below the daily-run banner.

Set `daily_run.recheck` to `"preview"` for the first real run. It computes the
named results without saving checks or changing the backlog, and saves
`work/daily-runs/<run_id>-recheck-preview.json` for approval before switching to
`"on"` (the public default). To inspect a saved snapshot directly, run
`python -I tools/tracker/recheck.py --snapshot <file> --dry-run`.

## Qualification judge

After the re-check, the judge reads the full saved posting, one per AI call.
It suggests a score from 1.0 to 5.0, reasons with posting quotes and evidence,
and 6 to 10 main requirements. Code checks quotes and citations, counts whole
months across selected dated roles without double-counting overlaps, and
rounds the score to one decimal. The profile's optional work history takes
precedence over dated role lines in eligible evidence. Each `work_history`
item has `title`, `employer`, `start` and `end`. Dates accept `YYYY-MM-DD`
or `Mon YYYY`, and `end` can be `present`.

The action is **Apply now** at 4.5 or higher, **Worth applying** from 4.0 to
4.4, **Only with a reason** from 3.5 to 3.9, and **Skip** below 3.5. A saved
location outside your work rules forces **Skip**. Jobs with no scored judgement
show **Not scored yet**.

Migration 8 adds the score to `judgements`. The same transaction copies the
action to `jobs.fit`. The drawer shows reasons with evidence titles and a folded
list of main requirements marked Supported, Close, Missing or Unclear.
Only jobs in Tracked, Review and Apply are judged.

An unchanged posting, place result, profile, evidence set and judge version reuse
the saved judgement. Changes queue the job to be scored again. Each call, including the single retry, counts
toward the profile's `search.judge_budget` for the day in
`work/ai-usage.sqlite3`; dry runs, previews and the command below count too, and
a restart does not reset it. Jobs beyond the budget wait for the next day. One
unusable answer skips that job only, and that job is not tried again for seven
days. If the AI cannot be reached for two jobs in a row the judge stops: with
nothing judged the phase is skipped. After some progress it is partial and the
run finishes with a warning. Jobs without saved posting text report "No posting
text yet". With no usable evidence, the report says "Not enough about you yet".

Set `daily_run.judge` to `"preview"` to judge on an in-memory copy, save no
judgement and write `work/daily-runs/<run_id>-judge-preview.json`; `"on"` is the
public default. Preview results are kept in
`work/daily-runs/judge-preview-cache.json`, so a later preview only calls the AI
for jobs it has not seen; failures are kept there too, so a failing job waits
seven run days in previews as well. To judge one job by its database number:

```powershell
python -I tools/tracker/judge.py --job 12
python -I tools/tracker/judge.py --job 12 --apply
```

The default dry run prints the score, action, reasons with quotes and evidence titles, and main requirements.
The AI is reached through `tools/hq-dashboard/ai_seam.py`, `complete(prompt,
schema)`, which checks every answer against its schema.

## Document records

Opening a tracker database adds the missing `documents` table without changing
job rows, the revision or mirror flags. Site exports save CVs and cover letters
in `work/`, with their job, evidence sources, file hash and completed checks.
Names use `<prefix>CV_<Company>_<Role>.docx` or
`<prefix>CL_<Company>_<Role>.docx`; existing names receive a numeric suffix.
The marker and readability checks block unsuitable drafts before any file is
written. They never rewrite the draft.

Register existing documents as made outside the site with this command:

```powershell
python -I tools/tracker/backfill_documents.py --workbook work/applications-tracker.xlsx --work work --dry-run
python -I tools/tracker/backfill_documents.py --workbook work/applications-tracker.xlsx --work work --apply
```

The default is a dry run, which writes nothing. `--work` defaults to the workbook
folder; `--prefix` defaults to the app's `document_prefix` setting. Apply records
only unregistered, allowlisted CV and letter files directly in that folder,
using their existing modified dates and hashes. It never changes their bytes,
dates or names. Running apply again adds nothing for recorded file names.
The database must already exist. With Excel alone, files are listed as made
outside the site because there are no tracker document records.
