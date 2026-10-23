# Copilot instructions — Questly

`AGENTS.md` is the full rulebook and `docs/task-scope.md` is the task list.
This file is the short checklist of the rules that past pull requests broke
most often. When this file and `AGENTS.md` disagree, `AGENTS.md` wins.

## Before you write any code

1. `git fetch origin && git switch -c <type>/<TASK-ID>-<slug> origin/dev`
   (type: `feat`, `fix`, `chore`, `docs`, `refactor`). Never work on `main` or `dev`.
2. Open `docs/task-scope.md` and find the row for your TASK-ID. That row is the
   spec. Its "May touch" column is the complete list of paths you may change;
   `creates:` lists the new files you may add.
3. If the work seems to need a file that is not in the row, stop and ask พาย
   in the PR. Do not edit it and explain afterwards.

## Never do these

- **Never weaken a test to make it pass.** Do not delete, skip, move, rename,
  loosen an assertion in, or add an allowlist or exception to an existing
  test — above all anything under `tests/architecture/`. A failing
  architecture test means the code is wrong, not the test.
- **No JavaScript in templates.** No `<script>`, no `javascript:` URLs, no
  `onclick=`/`onchange=`/any `on*=` attribute in `src/questly/web/templates/`.
  If a design seems to need JavaScript, build it without, or ask.
- **No role checks in routes or pages.** `routes.py` and `pages.py` never read
  `actor.role` or branch on it. The service authorizes and raises
  `PermissionDeniedError`.
- **Do not edit another person's row or branch**, and do not edit
  `docs/task-scope.md` unless your row lists it.
- **Do not touch locked paths:** `src/questly/database/`, `alembic/`, `.env`,
  `.env.example`, `pyproject.toml`, `uv.lock`, `.github/`, `AGENTS.md`.
- No `typing.Any`, no `async def`, no `value or []`, no `getattr(x, "f", default)`.

## Templates and CSS

- Build inputs and buttons only with the macros in
  `web/templates/_components/forms.html`, and headers, badges, empty states
  with `_components/ui.html`. `field()` already renders its own `<label>` —
  do not add a second one.
- Every POST form renders `{{ csrf_field() }}`.
- Added or changed Tailwind classes in a template? Rebuild the CSS and commit
  `app.css` in the same PR, or the classes do not exist in the browser:
  `tailwindcss -i src/questly/web/static/css/input.css -o src/questly/web/static/css/app.css`
  (Tailwind **v4.3.3** standalone binary, see README "Styles"). Never hand-edit `app.css`.
- Copy pages that already exist (`instructor/t01_*.html`, `t03_generate.html`)
  for layout, the `page_header(..., eyebrow=...)` call and the language of the copy.

## Tests

- Anything changed under `src/` needs a new or changed test under `tests/`.
- Page routes: one integration test per role (allowed role 200, Student 403,
  non-member 404).
- `tests/db/` and `tests/r2/` tests are skipped by CI. They do not count as the
  only proof; also add a unit or integration test that CI runs.

## Before you open the pull request

```bash
git fetch origin && git rebase origin/dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

- Base branch is `dev`, not `main` (GitHub pre-fills `main`; change it).
- In the description, list:
  - every file you changed that is outside your row (should be none),
  - every existing test you changed and why,
  - any rule in this file or `AGENTS.md` you could not follow (should be none).
- Quote the CI result for the PR's head commit, not only a local run.
