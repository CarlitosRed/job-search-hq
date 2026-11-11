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
