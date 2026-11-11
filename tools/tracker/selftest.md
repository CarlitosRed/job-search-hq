# Tracker self-test (counts, flow, percentages)

 Use a disposable fictional workbook.

## 1. Run a dev server against a copy (PowerShell)

```powershell
# From the repository root:
Copy-Item examples\applications-tracker.xlsx $env:TEMP\tracker-selftest.xlsx
$env:TRACKER_XLSX = "$env:TEMP\tracker-selftest.xlsx"; $env:TRACKER_PORT = "8790"
python -I tools/tracker/server.py --no-browser
```

## 2. (Optional) Put every kind of status into the copy

In a second PowerShell window, use a fictional workbook with at least six job rows (rows 2 through 7). Add rows to the disposable copy if needed before starting the server:

```powershell
$u = "http://127.0.0.1:8790/api/update"
foreach ($s in @(@{row=2;status="Offer"},@{row=3;status="Rejected"},@{row=4;status="Interview"},@{row=5;status="Message drafted"},@{row=6;status="Skipped"})) {
  Invoke-RestMethod $u -Method Post -ContentType "application/json" -Body (@{sheet="Jobs"} + $s | ConvertTo-Json) }
python -I -c "import openpyxl,os;p=os.environ['TEMP']+r'\tracker-selftest.xlsx';wb=openpyxl.load_workbook(p);ws=wb['Jobs'];h=[c.value for c in ws[1]];ws.cell(7,h.index('My status')+1).value='On hold (recruiter)';wb.save(p)"
```

The last line writes an unknown status directly into the copy, the way an external importer could. The page must count it under "Other status".

## 3. Run the check

Open http://127.0.0.1:8790, press F12, and in the Console run:

```js
trackerSelfTest()
```

It prints `SELF-TEST PASSED` or `SELF-TEST FAILED` with one line per check. It runs on all jobs, on the current filters, and on a built-in synthetic set (one job of every status, plus an unknown status and a blank one):

- the Tracked tile equals the sum of the stage tiles, which equals the number of jobs
- the flow's first column adds up to Tracked, and its second column adds up to Applications (Applied + Interviewing + Offer + Rejected)
- the stage tile percentages and the region bar percentages each total 100.0% within rounding (each % is shown to one decimal; allowance 0.1, or 0.05 per non-zero part if larger)

Every Overview number comes from one function, `bucket()` in index.html: Wishlist, Ready to send, Applied, Interviewing, Offer, Rejected, Skipped, Other status.
