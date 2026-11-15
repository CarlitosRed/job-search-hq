# Stages and rules

## Now: stage vocabulary

**Now:** [stages.json](../../tools/tracker/stages.json) is the shared definition. Keep this order and spelling. A new row starts at **Tracked**.

| Name | Meaning | Colour |
| --- | --- | --- |
| Tracked | Found, not looked at yet | Gray (`--mute`) |
| Review | Looking at it; Fit shows here | Gray (`--mute`) |
| Apply | I want to apply; make the CV | Gray (`--mute`) |
| Skipped | Not applying | Gray (`--mute`) |
| Applied | Sent | Gray (`--mute`) |
| Interview | In interviews | Gray (`--mute`) |
| Offer | Got an offer | Green (`--green`) |
| Rejected | They said no | Red (`--red`) |

**Now:** Rejected can follow Applied, Interview or Offer, or remain Rejected. The board also hides Skipped as a move target after Applied, Interview, Offer or Rejected. The server's rejection check is shared; the extra Skipped restriction is in the board UI.

## Now: legacy input

| Old value | Migration behavior |
| --- | --- |
| Suggested (not applied) | Review when fit exists; Tracked without fit |
| Message drafted | Apply |
| CV ready | Apply |
| Blank | Treated as Suggested (not applied), then follows its fit rule |
| Wishlist, Ready to send, Interviewing | Old prototype/guide wording; no aliases in the current migration map. Use Tracked, Apply, Interview in new designs; do not silently rewrite stored values. |

**Now:** Unknown values stay visible through the Other fallback; Other is not a writable stage. Migration is a separate maintained tool, not a designer's UI change.

## Now: posting checks

**Now:** The server reads dated `Open check…` columns, newest first, preferring a value beginning with `OPEN`, `CLOSED`, `REDIRECTED` or `UNKNOWN` (case-insensitive). It supplies `_check` and `_checkdate`; otherwise it falls back to a nonempty check value.

| UI state | Exact page interpretation |
| --- | --- |
| Closed (`closed`) | `_check` begins with `CLOSED`, or contains `expired` or `no longer`, case-insensitive |
| Open (`open`) | Otherwise, `_check` begins with `OPEN`, case-insensitive |
| Not checked (`unknown`) | Everything else, including empty, `REDIRECTED` and `UNKNOWN` |

**Now:** Posting state is separate from stage. Closed does not automatically mean Rejected. See [the five views](screens.md#what-the-five-views-mean).

## Please do not change

- **Now:** Stage names/order and the Overview Sankey. Count each job once by current stage. Applications = Applied + Interview + Offer + Rejected; Skipped is not an application. Sankey lines show possible steps, not historical traffic.
- **Now:** One page for phone and PC. Preserve routes, field names, version/identity checks and visible waiting/conflict states. Never claim a queued write is saved.
- **Now:** A cover letter always asks for sentiment first, with an explicit choice to proceed without one. Analysis alone does not create or send a letter.
- **Now:** CV and letter files contain no banners, draft markers or header/footer notes. Drafts require human truth and layout review. Use eligible evidence; do not invent experience or treat fit as proof of eligibility.
- **Now / Coming:** Nothing is deleted: preserve old records through archive and use Skipped for declined leads. No delete endpoint or UI action. Nothing is ever applied or sent automatically.
- **Now:** LinkedIn links only; no LinkedIn or Indeed scraping. Manual discovery uses approved public board APIs and only adds a job when the person chooses.
- **Now:** Escape all data text; never insert raw HTML from postings, notes, reports or AI output. Preserve same-origin resources; add no remote fonts, analytics, libraries or third-party page requests.
- **Now:** Use fictional fixtures in public PRs/screenshots; the built-in public board list is also allowed (see [try it](try-it.md)). Keep backend, builder, workflows, requirements, evidence and rules outside visual PRs. Build after every edit: `port_tracker.py` makes exact-text replacements and stops on changed anchors. Report the quoted anchor to the owner; do not change the builder (see [guide](README.md#your-part)).
- **Coming:** Add the activity-over-time chart beside the Sankey, using stored stage history. Do not replace the Sankey or imply complete history for jobs imported before tracking began.

**Now:** Full contribution boundaries are in [CONTRIBUTING.md](../../CONTRIBUTING.md); storage and trust limits are in [architecture](../ARCHITECTURE.md).
