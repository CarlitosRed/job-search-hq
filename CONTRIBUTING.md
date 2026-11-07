# Contributing

Thanks for helping. This guide is for design and UI work (layout, colors, spacing, wording of labels). The server, saving, login and AI-draft rules are deliberately off limits unless the owner asks.

## Safe to change (UI only)

| Where | What it is |
| --- | --- |
| `tools/tracker/index.html` | The page: markup, CSS and the screen code. |
| `tools/hq-dashboard/hosted/` | Page pieces. `pc-data.js` and `pc-tools.js` connect the page to the server: restyle around them, do not change what they call. |

After editing the page, rebuild what the server serves:

```
python tools/hq-dashboard/port_tracker.py --backend pc
```

## Needs the owner (do not change in a UI pull request)

- `tools/hq-dashboard/*.py` (server, login, saving, AI tasks, document export)
- `tools/tracker/server.py`, `workbook_lock.py`
- `.github/workflows/`, `requirements.txt`, `LICENSE`
- `evidence/`, `rules.example.md` (rules for what drafts may claim)

## Run it with fictional data

Follow the Quick start in the README. Use only the fictional files in `examples/` and `evidence/*-example-*`. Never commit a real spreadsheet, evidence file, password, token or `.env` file.

## Before you open a pull request

1. Run the three test commands in the README. They must pass without a live model.
2. Keep the change to UI files. Say in the description what you changed and attach a screenshot (fictional data only).
3. Open a pull request against `main`. The owner reviews and merges; nothing is merged without that.

## What a good pull request looks like (from our first one)

- Start from the latest `main` and keep to the "safe to change" files above. Fill in the pull request template.
- Run the build and the tests before you open it, and paste the results. A PR whose own description says the build fails is not ready.
- Do not change what a number means (applications, tile counts, percentages) in a styling PR. If you think it should change, say so in the description and let the owner decide. Skipped jobs are not applications.
- Keep functions the page needs out of the data section (see `docs/REVIEWING-PULL-REQUESTS.md`) and leave the phone header rules alone unless the geometry test still passes.
- Attach desktop and phone screenshots (fictional data).

The owner's review steps are in `docs/REVIEWING-PULL-REQUESTS.md`.
