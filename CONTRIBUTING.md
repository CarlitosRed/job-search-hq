# Contributing

This guide covers design and UI work: layout, colors, spacing, and label wording. Follow [the contributor quickstart](docs/CONTRIBUTOR-QUICKSTART.md) for the fork, fictional demo, validation, screenshots, and PR workflow. [SETUP.md](SETUP.md) documents installation and Windows/Mac limits. Server behavior, saving, login, and AI-draft rules need the owner.

## Safe to change (UI only)

| Where | What it is |
| --- | --- |
| `tools/tracker/index.html` | The page: markup, CSS and the screen code. |
| `tools/hq-dashboard/hosted/` | Page pieces. `pc-data.js` and `pc-tools.js` connect the page to the server: restyle around them, do not change what they call. |

After editing the page, rebuild what the server serves:

```powershell
python -I tools/hq-dashboard/port_tracker.py --backend pc
```

## Needs the owner (do not change in a UI pull request)

- `tools/hq-dashboard/*.py` (server, login, saving, AI tasks, document export)
- `tools/tracker/server.py`, `workbook_lock.py`
- `.github/workflows/`, `requirements.txt`, `LICENSE`
- `evidence/`, `rules.example.md` (rules for what drafts may claim)

Private `rules.md` and all private data are also outside UI scope. Contributors must not edit `port_tracker.py`. If a UI edit changes an anchor string that the build expects, tell the owner exactly which string changed so the owner can adjust the script.

## Run it with fictional data

Follow [SETUP step 3](SETUP.md#3-fictional-demo-data-in-60-seconds). Use only fictional data and the example evidence. The bundled `examples/applications-tracker.xlsx` contains three fictional jobs and one fictional contact. Never commit a real spreadsheet, evidence file, password, token, `.env` file, or virtual environment. Generated HQ HTML is ignored; edit the source and rebuild.

## Before you open a pull request

A secret scan runs on every PR; a red `gitleaks` check means remove the secret and rotate it.

1. Run the three test commands in the README. They must pass without a live model.
2. Keep the change to UI files. Describe the change and attach desktop and 375 px screenshots with fictional data, including affected drawers or dialogs. See the quickstart for capture instructions on Windows and Mac.
3. Open a pull request against `main`. The owner reviews and merges; nothing is merged without that.

## What a good pull request looks like (from our first one)

- Start from the latest `main` and keep to the "safe to change" files above. Fill in the pull request template.
- Run the build and the tests before you open it, and paste the results. A PR whose own description says the build fails is not ready.
- Do not change what a number means (applications, tile counts, percentages) in a styling PR. If you think it should change, say so in the description and let the owner decide. Skipped jobs are not applications.
- Applications = Applied + Interviewing + Offer + Rejected. Count each job once by current status. The workbook stores `Interview` for the UI's Interviewing stage.
- Add no third-party requests, remote fonts, analytics, or dependencies. Preserve API behavior and same-origin PC resources.
- Keep functions the page needs out of the data section (see `docs/REVIEWING-PULL-REQUESTS.md`) and leave the phone header rules alone unless the geometry test still passes.
- Attach desktop and phone screenshots (fictional data).

The owner's review steps are in `docs/REVIEWING-PULL-REQUESTS.md`.

There are up to three review rounds. Address each round's findings, rebuild, rerun the suites, and update screenshots as needed. After the third round, the owner finishes remaining items on top of your commits. Allow maintainer edits if you want those fixes on your PR branch. The owner decides when to merge; never push directly to upstream `main`.

Mac contributors must report platform failures or unrun checks honestly and obtain Windows validation for PC behavior. A read-only tracker preview does not validate PC-only views, workbook writes, or the complete suite. Check keyboard access, visible focus, labels, contrast, dialog behavior, phone overflow, and stable tabs before requesting review.
