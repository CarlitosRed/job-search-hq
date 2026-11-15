# Design partner guide

## The app in five lines

**Now:** A job-search page runs on the person's own computer.
**Now:** A local SQLite file holds tracker data after migration; an Excel copy is kept automatically.
**Now:** Without that database, the server still reads and writes Excel directly.
**Coming:** The person connects their own AI tool to do the AI work, with no provider assumed by the design.
**Now:** A phone can use the same page through optional protected HTTPS access while the computer stays on.

## Your part

**Now:** Own the look, layout, colours, wording, motion, phone fit and accessibility. Keep routes, data shapes, stage names, counting rules and save behavior intact. The separate prototype will not be ported. Take one idea from it at a time and rebuild it on the real page in small public PRs, using fictional data only.

**Now:** Edit [the tracker page](../../tools/tracker/index.html) and [hosted page pieces](../../tools/hq-dashboard/hosted/). After every edit run `python -I tools/hq-dashboard/port_tracker.py --backend pc`. Never hand-edit generated `pc-index.html`. The builder makes exact-text replacements and stops if an anchor changes, for example `port: expected 1 match(es), found 0`. Undo the edit or report the quoted anchor to the owner; do not change `port_tracker.py`. Everything between the `data` and `writes` comment markers is replaced by the build.

**Now:** Start with [contributing](../../CONTRIBUTING.md) and [architecture](../ARCHITECTURE.md); use [screens](screens.md), [API](api.md), [rules](stages-and-rules.md) and [try it](try-it.md) for this brief.

**Now:** Stage wording in [the quickstart](../CONTRIBUTOR-QUICKSTART.md), [review guide](../REVIEWING-PULL-REQUESTS.md) and [SETUP](../../SETUP.md) is out of date: [tools/tracker/stages.json](../../tools/tracker/stages.json) and [stages-and-rules.md](stages-and-rules.md) are the truth; follow [try it](try-it.md) for the demo, not SETUP step 3.

## Where the look lives

**Now:** Base styles are one `<style>` block at the top of `tools/tracker/index.html`. The build adds `tools/hq-dashboard/hosted/hosted.css` and `pc.css`; Search and Documents markup is in `hosted/views.html`. There is no CSS framework or external font; the base system font is 14px.

**Now:** CSS custom properties on `:root` include `--bg`, `--card`, `--ink`, `--ink2`, `--mute`, `--line`, `--soft`, `--hover`; `--blue`, `--green`, `--amber`, `--red` with `-ink` and `-soft` variants; `--r` (16px), `--r-sm` (10px), `--shadow`, `--shadow-lg`. Stage colours use `--tc-<stage>` for tiles and `--conn-<stage>` for chart lines. Change tokens rather than individual colour rules. Keep matching tokens consistent across the four blocks: light, system dark, default dark and forced light. The builder also copies the system-dark block.

**Now:** Dark is the default. The header button remembers light/dark locally. Screenshot switches are `?theme=light`, `?theme=dark` and `?noanim`; combine them as `?theme=light&noanim`.

**Now:** Tracker width rules are 1240px (one-column Overview), 900px (wrapped header and drawers), 560px (phone), and 640px or coarse pointer (44px targets, 16px inputs). Hosted styles also use 900px, 560px, 360px and 170px; short-screen rules use height at most 500px with width at most 900px or coarse pointer, including a 561–900px landscape layout. Preserve no-hover/coarse-pointer behavior and `prefers-reduced-motion`.

## Answers to your four questions

| Question | Answer |
| --- | --- |
| Database? | **Now:** SQLite is implemented, with an automatic Excel mirror. It is selected when the neighboring database exists. No Postgres. |
| AI provider? | **Coming:** No provider is chosen for the finished design; the person connects their own AI tool. **Now:** The bundled worker specifically uses Codex CLI with ChatGPT authentication. Provider choice/settings are not yet a screen. Do not make that current adapter a design requirement. |
| Job sources? | **Now:** Manual Search queries public Greenhouse and Lever APIs. LinkedIn is links only; no LinkedIn or Indeed scraping. **Coming:** Ashby and Workday public APIs, plus a daily search at a fixed early-morning time. |
| Stack? | **Now:** A Python standard-library HTTP server runs on the person's computer; SQLite, local files and plain HTML/CSS/JavaScript support the page. Workbook, Word and image handling use installed packages. No hosted app or Docker is needed. Optional phone forwarding is setup, not a separate app. |

## Decided for the finished site

- **Coming:** Several local profiles on one install, a profile picker and the person's name in the header. No email sign-up or register screen. **Now:** Sign-in uses one shared local password, not separate profile accounts.
- **Now / Coming:** Preserve records: use Skipped for a decision not to apply and archive for old jobs. No delete route exists; do not add deletion controls.
- **Now:** Overview keeps the Sankey as its main chart. **Coming:** An activity-over-time chart from stored stage history sits beside it later. Sankey first.
