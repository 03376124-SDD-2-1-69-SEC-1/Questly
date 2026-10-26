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
| S-02 | `student/s02_solve.html` | Solve, TestResult | adapted | Full-height split pane under the header. No JS line numbers. No "Test" button. Results keep the per-test table under the pass/fail summary. Under 900px the description stacks above the editor instead of collapsing into a `<details>`. A pass/fail toast (`toast` in `_components/ui.html`, top right, fades out by CSS) shows after Run, and after Submit via `?submitted=1`. |
| S-02g | `student/s02g_history.html` | SubmissionHistory | adapted | Attempts table plus a dark code preview for the chosen attempt. |
| T-02 / 02a / 02b / 02c | `instructor/t02_*.html` | Results | adapted | The T-02a code view is a side panel, not a modal, still opened by `?student=`. No CSV export or comment box (no backend). Failing-test table moved to the side (or below when a student is open). |
| T-03 / 03d | `instructor/t03_generate.html` | CreateProblem (prompt) | adapted | Documents are check cards (field names unchanged). Topic select instead of the Figma language select. Error alerts keep their English text (tests assert it). |
| T-03a | `instructor/t03a_draft.html` | CreateProblem (draft) | adapted | Full citations panel added (Figma has none). |
| T-03c | `instructor/t03c_generating.html` | none | no mock | Progress card restyled. |
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
- Tailwind generates utilities from every `--container-*` and `--spacing-*` token, so layout helper classes are named `.settings-wrap` and `.review-wrap` to avoid colliding with `w-settings` and `w-review`.

## Open items

- Mobile checks were done by loading each page in 390px and 900px frames and checking for horizontal overflow (none found), not on a physical device.
