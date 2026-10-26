---
status: accepted
date: 2026-09-23
task: OPS-14
---

# Classroom-centric flow

Questly moves from an instructor-only authoring tool (Dashboard → Generate →
Review → Save) to a classroom system in the shape of MS Teams: everybody logs
in, lands on a classroom picker, and sees a role-specific page inside each
classroom. Students now submit code that is judged, which the old flow never
had. This ADR records every decision from the OPS-14 interview. The UI
contract is `docs/wireframes/Questly wireframes - Prototype.html` (67 screens);
the "Page 1" frames of the Figma file are the old flow and are void.

Terms used below are defined in `CONTEXT.md`.

## 1. Accounts and roles

1. Sign-up is self-service with an `@kmitl.ac.th` email, a password, and an
   email verification link valid for 24 hours. Every account starts as a
   **Student**.
2. Ticking "I am an instructor" on G-03 files an **Instructor request**
   (`instructor_requests`: user, faculty, status, reviewed_by, reviewed_at,
   note). An Admin approves it in A-01, which sets the user's role. The G-03
   faculty field is always visible (no JS to toggle it) and required only
   when the box is ticked.
3. Roles are global: `users.role` in (student, instructor, admin). A
   Classroom has exactly one Instructor (`classrooms.instructor_id`);
   `classroom_members` holds Students only. No TAs.
4. Admin skips classrooms and lands on A-01 Settings.
5. Passwords are hashed with stdlib `hashlib.scrypt` and a per-user salt.
   Sessions are a random `secrets.token_urlsafe` id in an HttpOnly cookie,
   stored server-side. No new dependency.
6. A deactivated user cannot log in and their sessions are revoked.
   `last_active_at` is written at most once per hour.
7. Student ID is derived from the email's local part when it is all digits,
   otherwise shown as "—". It is not stored.

## 2. Classrooms

1. A Classroom has a course code, course name, section and semester (C-03,
   editable in T-01 Classroom settings). The course code is new: the
   wireframes display it everywhere but C-03 and T-01 need the field added.
2. Students join with a 6-character **Join code**. Joining is instant. The
   Instructor can regenerate the code (the old one stops working, existing
   members keep access), disable it, and remove Students.
3. **Archiving** is reversible: an archived Classroom is hidden from the
   Active filter, every Posting in it is closed, joins are refused, and
   everything stays readable. It replaces deletion.

## 3. Assignments, Postings, Versions

1. Code and API say **Assignment**; UI copy says "Problem" / "โจทย์".
2. An Assignment's content (title, statement, difficulty, one Topic, Test
   Cases, judging settings) is shared. It is published to one or more
   Classrooms; each publication is a **Posting** (`classroom_assignments`)
   with its own schedule and policy.
3. T-04 step 3 settings are split by meaning:
   - judging settings on the Assignment Version: time limit per test,
     language, show hidden test names;
   - schedule and policy copied into every Posting as defaults, editable per
     Classroom afterwards: deadline, max score, allow late, allow
     resubmission.
4. Language: one per Assignment, from a config allowlist (Python 3 first).
   The CodeRunner adapter maps it to Judge0 language ids.
5. Each Assignment has exactly one Topic (`assignments.topic_id`,
   nullable). Topics stay a global list.
6. Test Cases have a **kind** (sample, hidden, edge) and an optional
   **note** ("empty list"). Students see edge tests as hidden. The note is
   shown to students only when "show hidden test names" is on. This
   amends the locked schema rule "`test_cases` has no `title` column": the
   rule now reads "no `title`; the per-test label is `note`".
7. Editing a published Assignment creates a new immutable **Version**
   (`assignment_versions`: snapshot of statement, Test Cases, judging
   settings, and a required reason for change). Content edits apply to every
   Posting.
8. On save, T-05a asks whether to extend the deadline. It lists every
   Posting with a checkbox, the current Classroom pre-checked; only checked
   Postings get the new deadline.
9. Students whose counted Submission is on an older Version see
   **Resubmit needed** and get a notification carrying the reason.

## 4. Drafts and generation

1. A generation request carries `classroom_id` and a required
   `requested_by`. The resulting **Draft** is listed on T-01 and resumed in
   the T-04 stepper. Publishing removes it from the Drafts list.
2. Knowledge **Documents** are an instructor library at `/documents`
   (T-06), owned via `uploaded_by`. T-03 picks the subset a generation may
   read; the choice is stored on the request. This reverses the earlier
   "documents per classroom" answer, because T-06 and T-03 are drawn that
   way.
3. Publishing (T-04 step 4) is owned by the generation slice:
   `GenerationService` calls an `AssignmentPublisher` Protocol that
   `main.py` fills with `AssignmentService`.
4. Quota is **daily** per Instructor (reset at midnight Asia/Bangkok),
   charged only on a successful generation. Each successful "Regenerate this
   part" also counts 1. A-01 "Usage this month" is a report only. A-01 also
   sets max pages read per generation and whether citations are required.
5. One generation model, chosen by the Admin from a config allowlist. The
   wireframe's `claude-sonnet-4-5` is placeholder copy. The "Allow
   instructors to override the model" toggle is dropped.

## 5. Submissions and Runs

1. A **Submission** is one row (`submissions`: posting, student, version,
   code, language, submitted_at, is_late, status, score, attempt) plus one
   `submission_test_results` row per Test Case. Every Submission is kept.
2. The **counted Submission** is the latest, on any Version. It is never
   re-judged, so a lower resubmission lowers the grade.
3. A **Run** executes sample tests only, is not graded and is not stored.
   S-02 is one form with two buttons (`action=run|submit`); the Run result
   is rendered into the response page with the code kept in the textarea.
4. Reset reloads S-02 with the Student's latest submitted code, or empty.
   There is no starter code.
5. Judge0 CE (self-hosted) runs behind a `CodeRunner` Protocol,
   synchronously with a timeout.
   Superseded for Submit by ADR-0011: Submit is judged in the background.
6. **Late**: a Submission after the deadline on a Posting that allows late
   work. It is accepted and flagged, with no automatic penalty.
7. **Archived** Classrooms refuse Run and Submit (page: S-02f with an
   "archived" notice, 409; API: 409 `classroom_archived`), added 2026-09-24.
8. **Closed**: a Posting closes when the Instructor clicks Close
   submissions, or at the deadline when allow late is off. Allow
   resubmission off means one Submit only. A closed Posting shows S-02f,
   read-only.

## 6. Notifications

1. Stored notifications, shown in the G-02a dropdown and sent through an
   `EmailSender` Protocol: Assignment updated (with reason), Assignment
   published, T-02c "Notify students".
2. "Deadline tomorrow" is computed when the header renders (Posting due
   within 24 h, not yet submitted). It is not stored and not emailed, so no
   scheduler is needed.
3. "Score published" is dropped: S-02 shows the score immediately.
4. `EmailSender` is owned by the notifications slice. The auth slice sends
   verification mail through `NotificationService`, not its own Protocol.

## 7. Statistics

All computed on read from each Student's counted Submission.

- Pass rate = Students who passed all tests ÷ (Students × published
  Assignments).
- Fail rate (T-01 top 5) = Students who failed at least one test ÷ Students
  who submitted.
- Per-test "Failing" (T-02) = share of counted Submissions failing that test.
- Student "Solved" = submitted at least once; "Passed" = all tests passed.

Averages (Average score on T-01/S-01/T-02) are rounded to one decimal
half up (6.25 → 6.3), in Decimal, never with Python's round-half-to-even
(`core/rounding.py`, added 2026-09-24).

There is no admin dashboard.

## 8. Admin (A-01)

1. Users tab: pending Instructor requests (name, email, faculty,
   requested) and all users (search, role filter, 25 per page, deactivate).
2. AI tab: model, daily quota, max pages per generation, require citations,
   usage this month.
3. System status tab: six services (web application, database, PDF
   processing, AI generation, code execution sandbox, email delivery), each
   checked through its adapter. The tab refreshes with
   `<meta http-equiv="refresh" content="60">`.
4. While a core service is down, every Student page shows a banner.
   "Restart" and "View logs" (A-01a) are out of scope: a web request must not
   control infrastructure.

## 9. Web layer

1. No JavaScript, unchanged: the code editor is a `<textarea>`, tabs are
   URLs, stepper steps are separate form posts with the Draft saved on the
   server, modals use the HTML `popover` attribute. `<meta
   http-equiv="refresh">` is allowed.
2. Page handlers live in `core/<slice>/pages.py`, an APIRouter with no
   `/api` prefix, mounted in `create_app`. JSON handlers stay in
   `routes.py` under `/api/v1/`. Templates live in
   `web/templates/<group>/`, one file per page ID, `<group>` one of
   `shared` (G, C), `student`, `instructor`, `admin`.
3. For a URL shared by roles, the service returns a typed view
   (`InstructorClassroomView | StudentClassroomView`); the page picks the
   template with `isinstance`. The role rule stays in the service.
4. Handlers get the current user with `actor = current_actor(request)`,
   which reads the session cookie through `request.app.state.auth_service`,
   the same way `_service(request)` works today, never `Depends()`. It
   raises `NotAuthenticatedError`: pages redirect to `/login`, the API
   returns 401.
5. Authorization lives in services. Every use case that touches classroom
   data takes `actor: Actor` as its first argument and raises
   `PermissionDeniedError`. Routes and pages never read a role.
6. A non-member gets the same 404 as a missing id. A member in the wrong
   role (a Student on an instructor page) gets 403.
7. CSRF (added 2026-09-24): logged-in form posts carry the synchronizer
   token stored on the Session; anonymous forms (login, sign-up, resend
   link) carry a token from the `questly_csrf` cookie (double-submit). One
   check, `require_csrf`, runs first in every POST page handler; a mismatch
   is 403. The JSON API relies on FastAPI parsing bodies only with
   `Content-Type: application/json` and on allowing no CORS origins.
8. Cookies are `HttpOnly`, `SameSite=Lax` and `Secure`; only
   `scripts/demo.py` and the tests turn `Secure` off (plain HTTP).
9. `/` redirects a logged-in account to its landing; a visitor gets the
   public landing page G-00 (changed 2026-09-24; it used to redirect to
   `/login`).

## 10. Code structure

1. Slices under `src/questly/core/`:
   - new: `auth`, `classrooms`, `submissions` (`CodeRunner` port),
     `notifications` (`EmailSender` port), `admin`;
   - extended: `assignments` (Versions, Postings), `generation` (Drafts,
     quota, `AssignmentPublisher` port);
   - renamed: `uploads` → `documents` (API `/api/v1/documents`);
   - unchanged: `topics`. `ai/` is untouched.
2. Each slice copies `core/topics` as it is: `models.py`, `schemas.py`,
   `ports.py`, `service.py`, `routes.py`, plus `pages.py`. In-memory
   adapters live in `tests/fakes/<slice>.py`, never in `src/`.
3. Concrete Judge0 and email adapters live in a new
   `src/questly/integrations/` package. Stubs come first.
4. Until Phase 3 lands the tables, a new slice's `create_app` keyword
   defaults to `None`; with no adapter the service is not placed on
   `app.state` and its handlers return 503 "not persisted yet". Routers are
   still mounted, so `/docs` shows them.
5. Replaced endpoints are removed in the slice commits:
   `/api/v1/generations`, `/api/v1/uploads`, and `POST`, `PUT` and list on
   `/api/v1/assignments`.

## 11. Known gaps (deliberate)

- Submit and Generate are synchronous: no progress display and no Cancel
  (S-02a, T-03c). Generate has had both since ADR-0010. Submit has had a
  progress display since ADR-0011, and still has no Cancel.
  PDF ingestion is asynchronous and T-06 refreshes every 5 s with
  `<meta refresh>` while a document is processing (T-06b); its Cancel is not
  built.
- The T-04/T-05 cited-source preview shows the stored text snapshot, not a
  rendered PDF page.
- T-06 upload is a file input, not drag and drop.
- "Copy code" buttons (C-03a, T-01) are plain selectable text.
- Syntax highlighting in the editor is not available.

## Page route contract

One URL can render a different template per role.

| Route | Pages (states) | Who |
|---|---|---|
| / | G-00 public landing (hero, how it works, for instructors/students, static draft preview); a logged-in account is redirected to its landing | visitor |
| /login | G-01 (01a wrong password, 01b email not verified) | all |
| /signup | G-03 (03a not a KMITL email, 03b already registered) | all |
| /verify | G-04 (04a waiting, 04b verified, 04c link expired after 24h) | all |
| (every page) | G-02 header, 02a notifications dropdown, 02b mobile menu | logged in |
| /classes | C-01 picker: instructor and student variants, mobile, 01a/01b empty; C-02 join modal (02a invalid code, 6-char code); C-03 create modal (03a shows join code) | instructor, student |
| /classes/{id} | S-01 student: Problems + Summary tabs, 01a empty, 01b badges, mobile | student |
| /classes/{id} | T-01 instructor: Problems / Summary / Members / Classroom settings tabs, 01a no students, 01b no problems | instructor |
| /classes/{id}/assignments/{aid} | S-02 solve (02a running, 02b passed, 02c partial, 02d error/TLE, 02e problem updated, 02f closed, 02g submission history), mobile | student |
| /classes/{id}/assignments/{aid} | T-02 results (02a student code side panel, 02b version history, 02c no submissions) | instructor |
| /classes/{id}/assignments/{aid}/edit | T-05 edit published, 05a extend deadline? | instructor |
| /classes/{id}/generate | T-03 generate (03a draft result, 03b no documents, 03c generating, 03d failed, quota not charged) | instructor |
| /classes/{id}/drafts/{did} | T-04 stepper: steps 1–4, 04a required field missing, 04b published | instructor |
| /documents | T-06 my documents (06a empty, 06b processing/failed scanned PDF) | instructor |
| /admin/settings | A-01 Users / AI / System status tabs, 01a service down, 01b no pending requests | admin |

Tabs are separate URLs under these routes (e.g. `/classes/{id}?tab=members`
or a sub-path; the page module decides, one template per tab).

## API contract

All under `/api/v1/`, one router per slice.

**auth**: `POST /auth/signup` · `POST /auth/verify` ·
`POST /auth/verify/resend` · `POST /auth/login` · `POST /auth/logout` ·
`GET /auth/me` · `POST /auth/instructor-requests`

**classrooms**: `GET /classrooms` · `POST /classrooms` ·
`GET /classrooms/{id}` · `PATCH /classrooms/{id}` ·
`POST /classrooms/{id}/archive` · `POST /classrooms/{id}/unarchive` ·
`POST /classrooms/join` · `POST /classrooms/{id}/join-code` (regenerate) ·
`DELETE /classrooms/{id}/join-code` (disable) ·
`GET /classrooms/{id}/members` ·
`DELETE /classrooms/{id}/members/{user_id}` ·
`GET /classrooms/{id}/summary` · `GET /classrooms/{id}/summary.csv`

**assignments**: `GET /classrooms/{id}/assignments` ·
`GET /classrooms/{id}/assignments/{aid}` ·
`PATCH /classrooms/{id}/assignments/{aid}` (Posting settings) ·
`DELETE /classrooms/{id}/assignments/{aid}` (unpost) ·
`POST /classrooms/{id}/assignments/{aid}/close` ·
`POST /classrooms/{id}/assignments/{aid}/notify` ·
`GET /assignments/{id}` ·
`PATCH /assignments/{id}` (new Version; body carries `reason` and
`extend_deadline: {posting_id: datetime}`) ·
`GET /assignments/{id}/versions` · `GET /assignments/{id}/versions/{n}` ·
`/assignments/{id}/test-cases` CRUD (writes on a published Assignment create
a Version)

**generation**: `POST /classrooms/{id}/drafts` (generate, with
`document_ids`) · `GET /classrooms/{id}/drafts` · `GET /drafts/{id}` ·
`PATCH /drafts/{id}` (save one stepper step) · `DELETE /drafts/{id}` ·
`POST /drafts/{id}/regenerate` (`part`: title or description) ·
`POST /drafts/{id}/publish` (`classroom_ids`)

**submissions**: `POST /classrooms/{id}/assignments/{aid}/submissions` ·
`GET /classrooms/{id}/assignments/{aid}/submissions` (Instructor: all
Students; Student: own history) ·
`GET /classrooms/{id}/assignments/{aid}/submissions.csv` ·
`POST /classrooms/{id}/assignments/{aid}/runs` (not persisted) ·
`GET /submissions/{id}`

**notifications**: `GET /notifications` · `POST /notifications/{id}/read` ·
`POST /notifications/read-all`

**documents**: `POST /documents` · `GET /documents` ·
`GET /documents/{id}` · `DELETE /documents/{id}` ·
`POST /documents/{id}/retry`

**admin**: `GET /admin/users` (`q`, `role`, `page`) ·
`PATCH /admin/users/{id}` (role) · `POST /admin/users/{id}/deactivate` ·
`GET /admin/instructor-requests` ·
`POST /admin/instructor-requests/{id}/approve` ·
`POST /admin/instructor-requests/{id}/reject` · `GET /admin/ai-settings` ·
`PUT /admin/ai-settings` · `GET /admin/status`

Unchanged: `/api/v1/topics`, the AI vector endpoints.

## Schema changes for the Phase 3 OPS task

Not applied by this ADR. One hand-written Alembic migration, tested by CI on
its own Neon branch. All new ids are `BIGSERIAL`; no cross-schema foreign
keys. ADR-0008 records where OPS-15 departs from or adds to this list.

- `users`: role CHECK gains `student`, default becomes `student`; add
  `password_hash`, `email_verified_at`, `is_active`, `last_active_at`.
- new `email_verification_tokens` (user, token hash, expires_at, used_at).
- new `sessions` (id token hash, user, created_at, expires_at, revoked_at).
- new `instructor_requests` (user, faculty, status, reviewed_by,
  reviewed_at, note).
- new `classrooms` (instructor_id, course_code, course_name, section,
  semester, join_code UNIQUE nullable, archived_at).
- new `classroom_members` (classroom, student, joined_at; UNIQUE pair).
- `assignments`: add `topic_id` (FK topics, nullable), `owner_id`,
  `current_version`; topic, language and course leave `metadata`.
- new `assignment_versions` (assignment, number, statement snapshot, test
  case snapshot, time_limit_ms, language, show_hidden_names, reason,
  created_at; UNIQUE (assignment, number)).
- `test_cases`: add `kind` (sample, hidden, edge) and `note` nullable;
  `is_hidden` is replaced by `kind`. Still no `title`.
- new `classroom_assignments` (classroom, assignment, deadline, max_score,
  allow_late, allow_resubmission, published_at, closed_at; UNIQUE pair).
- new `submissions` and `submission_test_results` as in section 5.
- new `notifications` (user, kind, payload, created_at, read_at).
- `generation_requests`: add `classroom_id` NOT NULL, `requested_by` NOT
  NULL, `document_ids`; quota is counted from successful requests.
- new `ai_settings` (single row: model, daily_quota, max_pages,
  require_citations).
- `knowledge_documents`: `uploaded_by` becomes NOT NULL; add `page_count`,
  `progress`, `error_code`.

## Open questions

1. **What executes student code in production?** (raised 2026-09-24) The
   `CodeRunner` port is fixed; the adapter is not. Production wires
   `integrations/judge0.StubCodeRunner`, which runs nothing. The demo's
   `LocalUnsafeRunner` (in `tests/fakes/`, never shipped) runs code with the
   local Python behind a timeout, CPU/memory rlimits and a temp dir, and
   refuses to start when `ENV=production`; it is not a sandbox. Candidates:
   self-hosted Judge0 CE (§5.5), or another isolate/nsjail-based service.
   S-02a and asynchronous Submit wait for this decision. The demo runner is
   opt-in (`ALLOW_UNSAFE_RUNNER=1`) and refuses `ENV=production` either way.
2. **Per-Submission score: floor vs round half up?** (raised 2026-09-24,
   grading policy not decided) The code floors today: `max_score × passed
   ÷ total`, rounded down. The two differ when the fraction is .5 or more:
   2 of 3 tests at max 10 is 6.67 → 6 (floor) or 7 (half up); 1 of 2 at
   max 5 is 2.5 → 2 or 3. Changing it changes every stored score, so
   decide before OPS-15 persists Submissions. `score_for` in
   `core/submissions/service.py` is the one place to change.
   **Decided 2026-10-01 in ADR-0008: floor.**

## Consequences

- AGENTS.md changes its intro, Status, Schema rules, Terminology, Ports and
  adapters, and Definition of done; the new rules are enforced in
  `tests/architecture/`.
- Old FE-02/03/04 and CORE-07 rows are closed as superseded;
  `docs/task-scope.md` gains one row per new slice and one FE row per page
  group.
- The old "sync over HTTP only" wording assumed a separate AI service. The
  single-app decision of 2026-09-08 replaces it: `core/` and `ai/` are modules
  of one app and call each other through Python interfaces (`typing.Protocol`),
  not over HTTP. AGENTS.md "Schema rules" now says so.
