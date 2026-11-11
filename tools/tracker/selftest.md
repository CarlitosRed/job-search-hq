# Tracker self-test

Use a disposable fictional workbook. Desktop and phone use the same PC page,
which is built with `python -I tools/hq-dashboard/port_tracker.py --backend pc`.
The plain tracker server can also serve the core views locally.

## Preview and migrate the copy

With its server stopped, copy the fictional workbook and inspect the migration:

```powershell
Copy-Item examples\applications-tracker.xlsx $env:TEMP\tracker-selftest.xlsx
python -I tools/tracker/migrate_stages.py --workbook "$env:TEMP\tracker-selftest.xlsx" --dry-run
python -I tools/tracker/migrate_stages.py --workbook "$env:TEMP\tracker-selftest.xlsx" --apply
```

Review the old status, new stage and count per sheet. Input and output totals must
match; unknown values stay unchanged. Check the backup under the copy's `logs/`
folder. A second run must report zero changed statuses. If the copy has a
neighboring SQLite database, migrate that database and check its rebuilt mirror,
move history (`who=stages-01`), and untouched contacts/archive instead.

## Run the page check

```powershell
$env:TRACKER_XLSX = "$env:TEMP\tracker-selftest.xlsx"
$env:TRACKER_PORT = "8790"
python -I tools/tracker/server.py --no-browser
```

Open http://127.0.0.1:8790, press F12, and run this in the Console:

```js
trackerSelfTest()
```

The returned `ok` must be true and the console must say `SELF-TEST PASSED`.
Fixtures cover empty data, current jobs, every loaded stage, unknown statuses,
and unequal counts. They check tile totals, percentage rounding and flow node
counts in desktop and compact layouts. Counts come from `bucket()`.

The board order is Tracked, Review, Apply, Skipped, Applied, Interview, Offer,
Rejected, defined only in `stages.json`. All eight columns remain visible. Offer
is green, Rejected red, and the other six share one gray. Other status appears
only when unknown rows exist. Check a move from Review to Apply and Undo, then
Applied to Rejected. Review must not offer Rejected; Applied must not offer
Skipped on the board. The server allows backward moves, including out of closed
stages. Repeat on a phone viewport and check drawer steps, filters, status
selection, Add lead, and horizontal scrolling within the board.
