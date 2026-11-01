# UI audit: Figma Make re-skin (UI-02)

Design source: Figma Make "Questly Classroom Web App (Copy)" (`ft2Fe3c5xrv3NO2zCq21wi`), local copy in `docs/ui-redesign/reference/figma-make/`. Design tokens: `docs/ui-redesign/tokens/theme.css`.

Result key: **matches** = same layout and components as Figma; **adapted** = Figma layout with a documented change; **no mock** = Figma has no screen, styled with the same design system.

| Page id | Template | Figma screen | Result | Notes |
|---|---|---|---|---|
| G-00 | `shared/g00_landing.html` | none | no mock | Thai landing in the auth brand look (pink gradient, "Learn. Code. Grow.", code card), laid out after the Edumark template: nav, hero, highlight strip, features, steps for both roles side by side (no JS toggle), sample problems, CTA band, footer credit to Doonminus2. CSS-only motion (load-in, floating code card, scroll reveal where `animation-timeline` is supported), off under `prefers-reduced-motion`. Sample problems are illustrative copy. |
| G-01 | `shared/g01_login.html` | Auth (login) | matches | Demo "log in as" list kept (`data-demo="log-in-as"`) under the divider, as a one-column list, not the two-button Figma pair. No "remember me" or "forgot password" (no backend). |
| G-03 | `shared/g03_signup.html` | Auth (signup) | adapted | Keeps the instructor checkbox and faculty select (not in Figma). |
| G-04 | `shared/g04_verify.html` | Auth (verify) | adapted | Waiting, verified, expired and invalid states all use the verify card. No "I verified" button (the link does it). |
| G-05 | `shared/g05_error.html` | none | no mock | One page for every HTTP error a browser can land on (400, 401, 403, 404, 405, 408, 410, 413, 429, 500 to 505, and a generic fallback), in the auth brand look with the gradient status number. Thai copy per status in `ERROR_COPY` (`core/auth/pages.py`); 5xx on a GET offers "ลองอีกครั้ง". `/api/`, `/health` and `/static/` keep their JSON body. Not in Figma. |
| G-02 / 02a / 02b | `shared/g02_header.html` | Header | adapted | Bell opens the existing `<details>` dropdown; unread count stays a `badge badge-accent` (tests parse it) instead of a dot. Mobile menu breakpoint is Tailwind `sm` (640px), not 600px. No "switch view" demo items. |
| C-01 student | `shared/c01_student.html` | Classrooms | adapted | Cards carry only real data (pending count, next deadline). No progress bar: the model has no per-classroom ratio for students. |
| C-01 instructor | `shared/c01_instructor.html` | Classrooms | matches | Progress bar is the real average pass rate. |
| C-02 | `shared/c02_join.html` | Join | adapted | Stays a popover modal, not a page. |
| C-03 | `shared/c03_create.html` | CreateClassroom | adapted | Stays a popover modal. Success state is a modal with the join code (no copy button: no JS; `select-all`). No description field (not in the model). |
| S-01 | `student/s01_*.html` | Classroom, StudentProblems, Summary | adapted | Stats strip from `summary()` (handler now passes it to the Problems tab). No search box (no server filter). No "Submissions" tab: history lives on each problem (S-02g). Summary shows one real donut, no weekly chart. |
| T-01 | `instructor/t01_*.html` | Classroom, TeacherProblems, Summary, Members, ClassSettings | adapted | Header now repeats on every tab. "Mini progress" shows submitted ratio, not pass ratio (pass ratio is not in the data). Status is open/closed. Settings has no left settings nav (nothing behind it). Archive/unarchive moved into a danger-zone card. Summary: most-failed list, one real donut and the score table; no fake weekly chart. |
| T-01 Drafts | `instructor/t01_drafts.html` | none | no mock | Table in the same style, citations as chips. |
| S-02 | `student/s02_solve.html` | Solve, TestResult | adapted | Full-height split pane under the header. No JS line numbers. No "Test" button. Results keep the per-test table under the pass/fail summary. Under 900px the description stacks above the editor instead of collapsing into a `<details>`. A pass/fail toast (`toast` in `_components/ui.html`, top right, fades out by CSS; under reduced motion it disappears at 6 s with no fade) shows after Run, and after Submit via `?submitted=1`. |
| S-02g | `student/s02g_history.html` | SubmissionHistory | adapted | Attempts table plus a dark code preview for the chosen attempt. |
| T-02 / 02a / 02b / 02c | `instructor/t02_*.html` | Results | adapted | The T-02a code view is a side panel, not a modal, still opened by `?student=`. No CSV export or comment box (no backend). Failing-test table moved to the side (or below when a student is open). |
| T-03 / 03d | `instructor/t03_generate.html` | CreateProblem (prompt) | adapted | Documents are check cards (field names unchanged). Topic select instead of the Figma language select. Error alerts keep their English text (tests assert it). |
| T-03a | `instructor/t03a_draft.html` | CreateProblem (draft) | adapted | Full citations panel added (Figma has none). |
| T-03c | `instructor/t03c_generating.html` | none | no mock | Progress card restyled. A twinkling `sparkles` icon (`loading_icon("generating")`, FE-12) sits beside the progress bar; it is decorative (`aria-hidden`) and still under `prefers-reduced-motion`. Cancel stays in the card. |
| T-04 steps 1-4 | `instructor/t04_*.html` | CreateProblem (review) | adapted | Step 1 has no input/output format fields (not in the model). Step 2 test rows show kind (sample/hidden/edge) instead of "Valid". Citations panel on step 1, chips on step 4. |
| T-04b | `instructor/t04b_published.html` | CreateProblem (published) | matches | |
| T-05 | `instructor/t05_edit.html` | none | no mock | Fields grouped in cards. |
| A-01 | `admin/a01_*.html` | Admin | adapted | The admin pages are real now (users, AI, status), so they are restyled with the Figma admin nav and keep all behaviour. No fake data added. |
| T-06 Documents | `instructor/t06_documents.html` | Documents | adapted | Built in FE-08 after CORE-16 and OPS-20, fixed in FE-11. Thai copy with an `INSTRUCTOR SPACE` eyebrow; upload field, status badges and empty state come from the shared macros, so the layout follows the app rather than the mock one to one. No JavaScript: the browser's file input shows the chosen file name. |

## Cross-cutting deviations

- No browser JavaScript anywhere: menus use `popover`, tabs and filters are links, the citation passage opens with `<details>`.
- Thai copy is used for headings, labels and primary buttons. Strings that tests assert on, and status badge labels (`Passed`, `Resubmit needed`, ...), stay English.
- Tests updated for changed copy: `test_drafts.py` (publish button text, row text), `test_problem_pages.py` (T-01 row text). Data assertions are untouched.
- `core/classrooms/pages.py` passes `summary` to the Problems tab so the stats strip uses real numbers. No other Python changed.
- Loading icons (FE-12): `loading_icon(kind)` in `_components/ui.html`, CSS only, keyframes inside `prefers-reduced-motion: no-preference`. Generating twinkles `sparkles`, ingesting slides `file`. Judging flips an `hourglass` icon that is **not in Figma**; it was added to `_components/icons.html` because no Figma icon reads as "waiting for a result".
- Tailwind generates utilities from every `--container-*` and `--spacing-*` token, so layout helper classes are named `.settings-wrap` and `.review-wrap` to avoid colliding with `w-settings` and `w-review`.

## Button loading spike (FE-12)

**Decision: fall back to polling pages only.** Run, Submit and Generate keep their current look, and the button loading work is not needed. No CSS-only mechanism passed all three rules in all three engines. Every mechanism that keeps a state until the response arrives depends on focus, and Chromium restores the page from the back-forward cache with the button still focused, so the spinner is still there after Back. On the spike server, which sets no cookies, `Cache-Control: no-store` did not stop Chromium 156 from caching the page either, so no bfcache-ineligible Chromium case was observed. The app sends no `Cache-Control` on pages.

The button state would be feedback only. The server already refuses a double click. Run and Submit return 409 `submission_pending` while a Submission is pending (`_require_none_pending`, test `test_api_refuses_submit_and_run_while_a_submission_is_pending` passes). A second Generate returns the job already running (`start_job`, test `test_a_second_start_returns_the_job_already_running` passes).

Method: a throwaway harness, not committed. The UI walk script was not used because it cannot time a pending navigation or detect a bfcache restore. A local server returns the S-02 markup (Run and Submit outside the form, joined by `form="solve-form"`) and the T-03 markup (Generate inside the form, `required` prompt), with `app.css`. The form POST waits 1.5 s and then returns 303. The harness used Playwright 1.64.1 with Chromium 156 (full build, bfcache re-enabled because Playwright turns it off), WebKit 27.2 and Firefox 157. Each run did an 80 ms mouse press, Tab followed by Enter or Space, and Back. A probe property was read on every frame and every 10 ms. Times are measured from the press.

Each mechanism was held to three rules. Rule 1: the button shows a loading state within about 100 ms of the press, by mouse click or by Enter or Space. Rule 2: the state is gone after navigating Back. Rule 3: a form that fails validation shows no state. A mechanism is rejected if any engine breaks any rule. The Result column names the rules each mechanism broke.

| Mechanism | Chromium | WebKit | Firefox | Result |
|---|---|---|---|---|
| A `.btn:focus:not(:focus-visible)` | 3 ms, held; Back: **stuck** | **never** (a click does not focus a button) | 14 ms, held | fail (rules 1, 2); no state on Enter or Space in any engine |
| A2 `.btn:focus` | 1 ms; Back: **stuck** | **never** on click | 14 ms | fail (rules 1, 2); also shows when you Tab past |
| B `.btn:active` | 1 ms, gone on release | 16 ms, gone | 14 ms, gone | fail (rule 1): Enter and Space never show it |
| G `<span tabindex="-1">` wrapper, `:focus` | 1 ms; Back: **stuck** | 14 ms, held (the wrapper takes focus) | 16 ms, held | fail (rules 1, 2): no state on Enter or Space in any engine |
| P / CMD `popovertarget` / `commandfor` on the submit button | never | never | never | fail (rule 1): a submit button with a form ignores both attributes |
| D cross-document view transition | starts at 1.6 s | starts at 1.6 s | not supported | fail (rule 1): starts when the new page is ready |
| C `form:not(:invalid)` guard | | | | rule 3 only: with it, no state on an invalid T-03; without it, one frame on press |

`:target` and checkbox tricks were not built. A submit cannot change the fragment, and a button inside a `<label>` does not toggle the checkbox. In Playwright, WebKit and Firefox reload the page on Back instead of restoring it from bfcache. Forcing bfcache on in Firefox breaks Playwright's page handle. So rule 2 was observed there only on the reload path. Playwright's builds and profiles are not the shipped browsers, so a pass in WebKit or Firefox is weaker evidence than a fail. The decision rests on the Chromium and WebKit failures, which were observed. Screenshots of the stuck Chromium Back case and the WebKit no-focus case were reviewed during the spike and are not committed.

## Front-end audit (FE-12)

**Method.** Two read-only passes over the code as of the audit. The code pass read every template in `shared/`, `student/`, `instructor/`, `admin/` and `_components/`, and `input.css`; contrast was computed from the hex tokens with the WCAG 2 formula. The browser pass ran demo mode in Chromium and opened every page reachable by link as anonymous, Student, Instructor and Admin, at 1280px and 390px, each with motion allowed and with reduced motion, plus scripted states (popovers, Run, Submit, Generate, S-02a judging, T-06 in every Document status). Not reached: G-04 verified and expired (they need a real token), and POST results that would change the seed (C-03a, A-01a, approve, reject, archive, new join code, remove member).

**Counts.** 56 raw findings (39 from the code pass, 17 from the browser pass) merge into 49. Where two describe one defect, the higher severity wins. **High 2, medium 20, low 27.** Eight are about loading or motion: three are fixed below (the A-01 clock, the toast under reduced motion, and the hover lift), one is accepted, and four are auto-refresh pages whose fix lives in `core/` (follow-ups i and ii).

### Fixed in FE-12

- `loading_icon(kind)` on every page that waits on the server: T-03c generating, S-02a judging, T-06 ingesting. CSS only, still under reduced motion, tested per role through ASGI.
- Favicon and home-screen icon from the logo Q, linked on every page through `asset_url`.
- A-01 status shows a still clock beside "Refreshes every 60 seconds." because the page is idle between reloads and a spinner would claim work that is not happening.
- S-02 toast under reduced motion: it now disappears at 6 s with a zero-length step and no movement. Before, its fade-out existed only with motion on, so it stayed over "Submission history" and the back link for good. Checked in Chromium at both widths: opacity 1 at 1 s, 0 and hidden at 8 s, in both motion settings.
- Hover lift on `.btn-primary` and `.class-card` moved inside the `no-preference` guard; color and shadow changes stay for everyone.

### Decided, not changed

- S-02g history shows a "Judging" badge, not a loading icon. That page never reloads, so an icon would keep moving after judging ends.
- Run, Submit and Generate get no button loading state: the spike above found no CSS-only mechanism that passes in all three engines. The button loading work is not needed.
- The auth brand panel loops (gradient 14 s, glow 9 s, drift 18 s, code card float 6 s) on G-01, G-03, G-04 and G-05, and the G-05 status number's 8 s gradient, are accepted UI-02 design. Every one sits inside `@media (prefers-reduced-motion: no-preference)` in `input.css`, so reduced motion turns them off.
- The C-02 and C-03 popover modals do not trap focus: Tab leaves for the cards behind the backdrop. A trap needs `showModal()` or script. **Decision needed:** accept this, or make C-02 and C-03 pages again.
- `/favicon.ico` still returns 404. Browsers use the `<link rel="icon">` every page now carries; only clients that ask for the path by name miss it.

### Needs owner action

Out of FE-12's scope. Each group is a proposed row; the owner is a suggestion.

- **(i) T-06 reloads forever while a Document is failed (high).** `has_refresh_state` in `core/documents/pages.py:75-79` counts `FAILED`, a terminal status, and a failed row has no delete button (`t06_documents.html:44` offers it only for `uploaded`). The 5 s reload also clears the chosen file: the probe saw one file selected, then none 6 s later. Fix: drop `FAILED` from the check and let a failed Document be deleted. Owner: นัด (CORE-16).
- **(ii) Auto-refresh drops keyboard focus and cannot be stopped.** T-03c reloads every 2 s, so focus on Cancel falls to the page body within 2.6 s (high). S-02a does the same while judging (medium). A-01 status does it every 60 s (low). The `aria-live` list on T-03c never fires because the whole page is replaced. Fix: a `?refresh=off` link or a longer interval, which needs a parameter in the `pages.py` of `core/generation`, `core/submissions` and `core/admin`. Owner: พาย.
- **(iii) Contrast tokens (medium and low). A design call: it changes the Figma look.** `--color-soft` #9ca3af is 2.54:1 on white and carries hints, table headers and stat labels. `--color-faint` #d1d5db is 1.47:1 and is the only S-02a judging status text, in `.console-empty`. Danger text on `.alert-danger` and `.badge-danger` is 4.41:1. The neutral `.badge` is 4.39:1, `.auth-brand p` 2.25 to 3.0:1, and the input and secondary button edge #e5e7eb 1.24:1. All in `input.css`. Fix: `--color-mid` #6b7280 (4.83:1) for text, #b91c1c (5.91:1) for danger text, a darker neutral badge text, and an input edge of at least 3:1. Owner: พาย (UI-02).
- **(iv) Invisible keyboard focus (medium).** `.input-error:focus` changes only a 1.18:1 halo. `.radio-seg` hides the radio and has no focus rule on its label, so on T-03 difficulty and G-03 affiliation a focused option looks the same as an unfocused checked one (rechecked in Chromium). T-04 step links are 1px wide under 640px (`t04_steps.html:8`), so a tap on a step circle does nothing and Tab lands on links nobody can see. The pink focus color on the pink landing hero and CTA is 1.0 to 1.71:1. Fix: a focus style on error inputs and on the segment label whose radio has focus, step links that stay full size with only their label text clipped, and a white focus color on the landing pink. Owner: พาย (UI-02).
- **(v) Enter submits the wrong button (medium).** The first submit button in a form wins. In the T-04 wizard it is Back (`t04_nav.html:4`), so Enter on step 1 or 3 goes back a step. On T-05 and T-04 step 2 it is "Add sample" (`t05_edit.html:45`, `t04_step2_tests.html:19`), so Enter adds an empty row instead of saving. Read from code, not run. Fix: an unseen Next or Save submit button first in each form. Owner: TBD, a template-only FE row.
- **(vi) Mobile layout (medium and low).** At 390px most data tables scroll sideways and the columns past the edge are the ones users act on: Remove, Approve and Reject, View code, Results, and the S-02 verdict and time. T-03a is 599px wide at 390px. Smaller: T-04 step 2 has about 56px side insets, the T-05 warning touches the card below, and the S-02a status line has no side gutter. Fix: on mobile, put the verdict or action column first or lay rows out as cards; for T-03a see the corrections below. Owner: TBD, one FE row with (x).
- **(vii) Accessible names and structure (medium and low).** Rows repeat one name ("Remove", "Reject", "View code", cell "Input"). The bell's `aria-label` hides the unread count (`g02_header.html:14`). `textarea_field` and `select_field` set no `aria-invalid` or `aria-describedby` (`forms.html:36-51`). Every page is `lang="th"`, English pages included (`base.html:2`). Also: headings skip levels, no skip link, empty action headers, unnamed progress bars, the server-rendered modal leaves the page behind it reachable. Fix: a row-specific `aria-label` per action, the unread count in the bell's label, the `field()` error wiring copied into both macros, and a `lang` block in `base.html` that English pages override. Owner: TBD, an FE row.
- **(viii) One-click destructive actions (medium).** Remove member (`t01_members.html:24`), Reject request (`a01_users.html:26`) and Delete document (`t06_documents.html:47`) post with no confirm, while Deactivate on A-01 already uses a `<details>` confirm. A-01 also offers Deactivate on the signed-in Admin's own row (not clicked, so whether the service refuses it is unknown). Fix: reuse the A-01 `<details>` confirm, and drop Deactivate from the actor's own row. Owner: TBD for the confirms (templates only); นัด (CORE-19) if the own-row rule belongs in `core/admin`.
- **(ix) Macro bypasses and dead CSS (medium and low).** T-01a builds its own empty state instead of `empty_state()` (`t01_problems.html:17-22`). Raw `badge badge-accent` spans, a hand-styled `<summary>`, an inline `style`, and breadcrumb and quota markup duplicated across two templates each. Unused classes in `input.css` such as `.page-narrow`, `.site-header`, `.btn-tile` and `.settings-nav`, and a spacer `<span>` in `t01_settings.html:40`. Fix: use `empty_state()` and `badge()`, move the shared breadcrumb and quota markup into `ui.html` macros, delete the unused classes and rebuild. Owner: TBD, an FE row.
- **(x) Touch targets under 24px at 390px (low).** The landing footer links, `.back-link`, the breadcrumb link, table links ("View code" 57x18, "Results" 41x18) and several check labels. Spacing to neighbours was not measured, so the WCAG 2.5.8 exception may cover some. Fix: a 24px minimum height on those links and labels. Owner: the same FE row as (vi).

Not grouped: on C-01 the Active and Archived filters show an empty list with no message (`c01_instructor.html:13`, medium; fix: an empty state for the filtered case). A-01 AI reads "1 generations" (low; fix: singular copy, or Thai).

**Corrections to the raw audits.** The code pass called layout at 390px clean and every touch target 24px or more; the browser pass measured T-03a at 599px and many links under 24px, because the code pass checked grid breakpoints and control classes only. The browser pass's fix for T-03a (wrap the tables in `.table-wrap`) is wrong: they are already wrapped (`t03a_draft.html:14`). The likely cause is that the grid children at `t03a_draft.html:42-44` keep their automatic minimum width, so they need `min-width: 0` (inferred from markup, not rendered). The browser pass also reported a visible change at every focus stop apart from the T-04 mobile step links; the radio segments in (iv) show none.

## Open items

- Mobile checks were done by loading each page in 390px and 900px frames and checking for horizontal overflow (none found), not on a physical device. The FE-12 browser pass later found T-03a overflowing at 390px; see (vi) above.
