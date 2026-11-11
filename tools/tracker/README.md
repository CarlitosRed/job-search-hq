# Local tracker



The local web page reads and writes a workbook at `work/applications-tracker.xlsx`.
Keep personal lead and contact data out of version control.

- From the repository root, run `tools\tracker\open-tracker.bat` or `python -I tools/tracker/server.py`. The default address is http://127.0.0.1:8765.
- Override the workbook with `TRACKER_XLSX` and the port with `TRACKER_PORT`. For testing, select a disposable fictional workbook.
- The page records progress. A person submits applications and sends messages.
- Follow [selftest.md](selftest.md) to check counts, flow and percentages.