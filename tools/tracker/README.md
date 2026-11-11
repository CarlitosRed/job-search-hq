# Local tracker



The local web page reads and writes the tracker at `work/applications-tracker.xlsx`,
or its neighboring SQLite store after migration.
Keep personal lead and contact data out of version control.

- From the repository root, run `tools\tracker\open-tracker.bat` or `python -I tools/tracker/server.py`. The default address is http://127.0.0.1:8765.
- Override the workbook with `TRACKER_XLSX` and the port with `TRACKER_PORT`. For testing, select a disposable fictional workbook.
- The page records progress. A person submits applications and sends messages.
- Follow [selftest.md](selftest.md) to check counts, flow and percentages.

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
