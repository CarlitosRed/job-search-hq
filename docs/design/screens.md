# Screens

**Now** means present in the checked-in page/server; **Coming** means the finished-site plan. Tabs, drawers and dialogs belong to one page on phone and PC. Build the PC page before judging it: the builder replaces parts of the tracker and hosted fragments.

## Screen map

| Screen | Data shown | Actions | State |
| --- | --- | --- | --- |
| Sign-in | Local password field; failure/throttle message | Sign in; sign out from the page | **Now:** HTML/CSS lives in `tools/hq-dashboard/server.py`; owner-maintained, outside visual PRs. Propose a look in an issue. No email account or registration. |
| Overview | Stage tiles, current-status Sankey, needs-attention jobs | Open jobs and filter from summaries | **Now:** Sankey lines show possible steps, not recorded moves. |
| List | Jobs, score/verdict, stage, posting state, age; filters and sorting | Open drawer, move stage, filter | **Now:** Five views: To do, In progress, Skipped, Closed, All. |
| Board | Jobs grouped in stage order | Open drawer, move cards, filter | **Now:** Same five views as List. |
| Job drawer | Job facts, notes, stage, date applied, interview rounds, posting check, related documents | Save notes/stage/date; add or edit rounds; open posting; download documents; start posting review/letter | **Now:** Round writes require SQLite; add a round while in Interview. |
| Add lead | Job fields and destination sheet | Add a lead through the existing save flow | **Now:** Company and Role required; default stage Tracked. |
| Contacts | Contact-sheet records, status and notes | Filter records; change status | **Now:** Existing contact view, not the proposed add form. |
| Search | Keywords, location, remote filter; built-in public company board names, progress, results, source errors and previous searches | Search now, open posting, explicitly add result to tracker, reuse search | **Now:** The board list comes from `SOURCES` in `tools/hq-dashboard/job_search.py`, even before searching. Greenhouse/Lever only; partial results are labelled; no fit score yet. |
| Documents and posting review | CVs, letters, reports, evidence notes; posting text/photos/link; analysis and drafts | Read/download reports; analyze; Create CV; Create cover letter | **Now:** Letter creation first opens the sentiment step; explicitly choosing no sentiment is allowed. AI execution needs the current worker. |
| Job card fit update | Fit band, short “why”, “Fit pending” | Read the explanation before deciding | **Coming:** Existing numeric scores/verdicts are not this finished card design. |
| Sent history | What the person recorded as sent | Inspect history | **Coming:** No automatic sending. Current Date applied is not a full sent-history screen. |
| Profile and search settings | Local profile and search preferences | Edit settings | **Coming:** No settings route contract yet. |
| Profile picker | Local profiles; active person's name in header | Switch profile | **Coming:** Several profiles on one install, no email sign-up. |
| Activity chart | Activity over time from stored stage moves | Inspect alongside Sankey | **Coming:** Stage history exists in SQLite; chart comes later. |
| Contacts redesign | Draft fields: channel, status, date contacted, notes, link to the company's jobs | Add form; edit contact details | **Coming:** Your field list is the designer's starting draft; the owner confirms final fields before build. These are design labels, not API field names yet. |
| CV onboarding | A supplied CV and information to review | Start a local profile from a CV | **Coming:** Do not invent upload routes or completion states. |
| AI settings | The person's connected AI tool | Configure connection | **Coming:** Keep provider-neutral wording. |

## What the five views mean

**Now:** To do includes Tracked, Review, Apply and unknown stages whose posting is not closed. In progress includes Applied, Interview and Offer. Skipped includes Skipped. Closed includes Rejected and closed postings still in Tracked, Review or Apply. All includes every loaded job. A posting being closed does not itself change the job's stage. Archived jobs are excluded from the loaded tracker.

**Now:** Preserve readable empty, loading, failed, queued, saved and conflict states. Check keyboard focus, labels, contrast, touch targets and reduced-motion behavior. See [the rules](stages-and-rules.md) and [screenshot checks](try-it.md).
