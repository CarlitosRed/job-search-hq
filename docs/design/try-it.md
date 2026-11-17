# Try it with fictional data

## Now: about ten minutes after dependencies

**Now:** Use a fresh contribution clone, Windows and Python 3.12. Follow [setup](../../SETUP.md#2-create-a-virtual-environment-and-install-dependencies) for the environment, packages and Playwright/WebKit. Downloads and installation may take longer than ten minutes. No AI login is needed for visual work or offline tests. Do not start live AI tasks or run the live worker check.

**Now:** From the repository root, with the environment active, copy the bundled workbook in a fresh clone only. These PowerShell commands refuse to overwrite existing demo data. Keep only the tracked `evidence/*example*` files. Copy the bundled report to populate Documents.

```powershell
if (Test-Path work/applications-tracker.xlsx) { throw 'Use a fresh fictional clone.' }
New-Item -ItemType Directory -Force work | Out-Null
Copy-Item examples/applications-tracker.xlsx work/applications-tracker.xlsx
Copy-Item examples/report-example-shortlist.md work/report-example-shortlist.md
python -I tools/tracker/migrate_to_sqlite.py --workbook work/applications-tracker.xlsx --dry-run
python -I tools/tracker/migrate_to_sqlite.py --workbook work/applications-tracker.xlsx --apply
python -I tools/tracker/migrate_stages.py --workbook work/applications-tracker.xlsx --dry-run
python -I tools/tracker/migrate_stages.py --workbook work/applications-tracker.xlsx --apply
python -I tools/hq-dashboard/port_tracker.py --backend pc
python -I tools/hq-dashboard/server.py --set-password # 16 characters minimum
python -I tools/hq-dashboard/server.py
```

**Now:** Inspect each dry-run result before applying; stop on a failure. The database gives the demo round saving and stage history; the stage migration removes legacy fixture labels. Open the local address printed by the server and sign in with the password entered locally. The password prompt is private; never record it. The workbook has three fictional jobs and one fictional contact. The bundled contact is fictional and may appear in screenshots. Its card is sparse; choosing Sent fails because the demo lacks `Date sent` (see [API](api.md)).

**Now:** Visit Overview, List, Board, Contacts, a job drawer, Add lead, Search and Documents. Without a profile, Search names the built-in public company boards from `SOURCES` in `tools/hq-dashboard/job_search.py`, even before a search. These names come from public code, not any person's data, so they may appear in screenshots; identify them as the built-in board list in the PR. Search requests are live and may return real jobs: use fictional fixtures for results and offline tests. Review forms and empty/failed AI states need no AI login; fake providers cover completed behavior in tests.

**Now:** After every page edit, rebuild and refresh. If the build stops, report its quoted anchor; do not change the builder. Edit source only, never generated HTML. For Mac's limited read-only tracker preview, local cookie issues and actual phone connection, follow [setup](../../SETUP.md) and [the quickstart](../CONTRIBUTOR-QUICKSTART.md); use [the rules](stages-and-rules.md) for stage details. Do not expose the plain tracker.

## Now: checks before a PR

**Now:** CI runs all of these on your PR; locally they need Windows. This is the build and full offline command list from [tests.yml](../../.github/workflows/tests.yml), after dependency installation. Node is also needed for the JavaScript check. Report actual outcomes and platform limits. [gitleaks.yml](../../.github/workflows/gitleaks.yml) separately runs the secret scan in CI.

```powershell
python tools/hq-dashboard/port_tracker.py --backend pc
python -I tools/hq-dashboard/test_ai_tasks.py
python -I tools/hq-dashboard/test_cv_onepage.py
python -I tools/hq-dashboard/test_pc_server.py
python -I tools/hq-dashboard/test_job_search.py
python -I tools/hq-dashboard/test_lead_policy.py
python -I tools/hq-dashboard/test_lead_ranker.py
python -I tools/hq-dashboard/test_link_fetch.py
node tools/hq-dashboard/test_importance.js
python -I tools/hq-dashboard/test_profile.py
python -I tools/hq-dashboard/test_rubric_versions.py
python -I tools/hq-dashboard/test_data_root.py
python -I tools/hq-dashboard/test_cv_quality.py
python -I tools/hq-dashboard/test_cv_fix.py
python -I tools/hq-dashboard/test_strong_cv.py
python -I tools/hq-dashboard/test_state_path.py
python -I tools/hq-dashboard/test_search_profile.py
python -I tools/hq-dashboard/test_daily_search.py
python -I tools/tracker/test_store.py
python -I tools/tracker/test_stages.py
python -I tools/tracker/test_rounds.py
python -I tools/tracker/test_fresh.py
python -I tools/tracker/test_archive.py
python -I tools/tracker/test_age_rollover.py
python -I tools/tracker/test_migrations.py
python -I tools/tracker/test_land_search.py
```

## Now: screenshots and PR proof

**Now:** In Chrome/Edge DevTools, enable the device toolbar, choose Responsive, then capture the changed view at **1440 × 900** and **375 × 900**. Use the command menu's **Capture screenshot**. Include affected drawers/dialogs and light/dark themes when relevant. Inspect keyboard focus, touch controls, stable tabs and horizontal overflow. A responsive capture checks phone fit; label an actual phone capture separately if tested.

**Now:** Capture page content only, using fictional demo records, including the fictional contact. The built-in public board names are allowed; personal or real tracker data never may appear. Exclude browser address bars, private paths, passwords and machine details. Attach screenshots to a small public PR against `main`, fill in the repository PR template and include build/test output. Say which checks were not run. The owner reviews and merges; see [contribution guidance](../../CONTRIBUTING.md).

**Coming:** Profile switching, CV onboarding, provider settings, sent history, card fit updates and the activity chart need later implementation; screenshots of mockups must be labelled Coming.
