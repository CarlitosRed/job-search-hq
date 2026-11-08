# UI contributor quickstart

Use a fresh clone and fictional data. Full PC operation and validation require Windows; macOS supports the limited tracker preview described below. Read [CONTRIBUTING.md](../CONTRIBUTING.md) for scope and [SETUP.md](../SETUP.md) for copy-paste installation and demo commands. Runtime validation of these instructions is **Unverified** for this documentation change.

1. **Fork and clone.** Use GitHub's Fork button, then replace the account placeholder. These commands work in PowerShell, Git Bash, and macOS Terminal:

```bash
git clone https://github.com/YOUR-GITHUB-USER/job-search-hq.git
cd job-search-hq
git remote add upstream https://github.com/CarlitosRed/job-search-hq.git
git switch -c ui/describe-your-change
```

2. **Install and run the demo.** Follow [SETUP steps 2–4](../SETUP.md#2-create-a-virtual-environment-and-install-dependencies): create a Python 3.12 environment, install requirements and Playwright/WebKit, and create the fictional workbook using step 3's snippet. Windows uses the password-protected PC server on port 8770. Mac uses the original tracker on port 8765 for a read-only preview; it lacks the PC-only views. Do not use real evidence or enable live AI for UI work.
3. **Make a focused change.** Edit `tools/tracker/index.html` and, when needed, `tools/hq-dashboard/hosted/` page pieces. Preserve API calls and data behavior in `pc-data.js` and `pc-tools.js`. Never put a needed view function between the `data` and `writes` comment markers: that entire block is replaced by the build. Generated HQ HTML is ignored and must not be edited or committed.
4. **Rebuild and run all three suites.** Commands work in PowerShell and Bash with the environment active:

```bash
python -I tools/hq-dashboard/port_tracker.py --backend pc
python -I tools/hq-dashboard/test_ai_tasks.py
python -I tools/hq-dashboard/test_cv_onepage.py
python -I tools/hq-dashboard/test_pc_server.py
```

The build should print `wrote ... pc-index.html ... bytes`; suites should end in `OK`. Mac cannot supply full passing validation: mutexes, Arial metrics, and the TLS test's fixed Windows OpenSSL path require Windows. Record actual errors and ask the owner for Windows results. Do not hide failures or describe an unrun check as passed.

5. **Attach desktop and 375 px screenshots.** In Chrome or Edge on Windows, open the fictional demo and press F12; on Mac Chrome use Command+Option+I. Enable the device toolbar with Ctrl+Shift+M on Windows or Command+Shift+M on Mac. Choose Responsive and set 1440 × 900, then 375 × 900. Open the DevTools command menu with Ctrl+Shift+P or Command+Shift+P and choose **Capture screenshot** at each size. Capture the changed view and any affected drawer/dialog; include light/dark variants when relevant. A Mac tracker screenshot covers only the original UI. Changes to hosted/PC additions also need screenshots from the Windows PC build. Keep screenshots local or attach them to the PR; do not add private browser chrome, paths, or data.
6. **Open the PR against upstream `main`.** Fill in every section of `.github/pull_request_template.md`: change, files, build/test proof, screenshots, number meanings, and exclusions. If a check was not run, say so and why. Allow maintainer edits if you want the owner to finish small fixes on your branch. There are up to **three review rounds**; address each round's notes, rebuild, and provide updated proof. After the third round, the owner finishes remaining items on top of your commits. The owner decides when to merge; contributors do not merge or push to upstream `main`.

Before requesting review, check:

- Keyboard access, visible focus, labels, selected tab state, readable contrast, and sensible dialog focus/close behavior.
- No horizontal page overflow at 375 px; tabs stay in place across views; touch controls and drawers remain usable. Check unsaved notes and saving/conflict messages.
- **Applications = Applied + Interviewing + Offer + Rejected. Skipped is not an application.** Count each job once by current status. The workbook stores `Interview` for the UI's Interviewing stage.
- No third-party requests, libraries, fonts, analytics, or remote assets added. Preserve the PC build's same-origin behavior.
- Do not change backend `.py` files, tracker server/lock, workflows, requirements, license, evidence, or rules. The sole narrow exception is `port_tracker.py` when a string it expects must stay synchronized with a UI edit. Explain that exact synchronization in the PR and qualify the template's no-Python checkbox; it does not authorize backend behavior changes.
