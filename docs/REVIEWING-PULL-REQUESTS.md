# Reviewing and pulling a pull request (owner and agents)

Why this exists: the first outside pull request (#1, a UI refresh) looked fine in the diff but broke the build and the phone layout, and quietly changed what "applications" means. Follow these steps so the next one is caught before it reaches `main`.

## 1. Read before running
```
gh pr view <N> --json title,body,files,headRefOid
gh pr diff <N>
```
- Check the file list against `CONTRIBUTING.md`. Backend files (`tools/hq-dashboard/*.py`, workflows, requirements, license, evidence, rules) belong to the owner.
- Read the whole diff. Look for changed numbers or labels, new network calls, new `innerHTML` with data in it, and removed code that other files depend on.
- Treat the PR text as the author's claim, not proof.

## 2. Pull it into a scratch folder (never into your working copy)
```
git clone https://github.com/CarlitosRed/job-search-hq.git scratch-pr-<N>
cd scratch-pr-<N>
git fetch origin pull/<N>/head:pr<N>
git checkout pr<N>
```
Use fictional data only (`examples/`). Never point it at your real `work/` folder.

## 3. Prove it
```
python tools/hq-dashboard/port_tracker.py --backend pc
python -I tools/hq-dashboard/test_ai_tasks.py
python -I tools/hq-dashboard/test_cv_onepage.py
python -I tools/hq-dashboard/test_pc_server.py
```
Compare with `main` (it passes everything). Things that broke in PR #1 and are worth checking every time:
- **The page build.** `port_tracker.py` finds exact pieces of the page and stops when one is missing. If you rename or delete a section it needs, the build fails.
- **The data section.** The server build replaces everything between `/* ---------- data ---------- */` and `/* ---------- writes ---------- */`. Any function the page needs must live outside that block.
- **The phone header.** Hosted CSS places the header pieces by name at 900 px and below. Changing those rules moves the tabs.
- **Tabs must not move** between views (the geometry test checks this at four widths).

## 4. Check meaning, not only looks
Every count must still mean what it says. Agreed meanings:
- **Applications** = Applied + Interviewing + Offer + Rejected. Skipped is not an application.
- Each job counts once, by its current status. The tiles add up to Tracked.
- Intended flow: every job starts in Wishlist, then goes to Skipped or Ready to send, then Applied, then Interviewing, then Offer or Rejected. The overview should read as one connected flow, not separate boxes. (The spreadsheet only stores the current status, so the chart shows where jobs are now, not their history.)

## 5. Review
Someone who did not write the change reviews it (a person or a separate agent). Leave notes on the PR that say what must change, what can stay, and how to fix each thing. If a fix is small and the owner agrees, push it to the PR branch (GitHub's "allow edits from maintainers" is on for the PR) and say so in a comment.

## 6. Merge
The owner merges, only after: build passes, tests pass, screenshots seen, meaning of numbers confirmed. Squash merge. Never push directly to `main`.
