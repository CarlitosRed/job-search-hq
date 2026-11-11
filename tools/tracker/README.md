# Local tracker



The local web page reads and writes the tracker at `work/applications-tracker.xlsx`,
or its neighboring SQLite store after migration.
Keep personal lead and contact data out of version control.

- From the repository root, run `tools\tracker\open-tracker.bat` or `python -I tools/tracker/server.py`. The default address is http://127.0.0.1:8765.
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
