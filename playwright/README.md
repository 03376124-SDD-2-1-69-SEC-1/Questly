# Playwright UI walk

`ui_walk.py` opens Questly pages in a real Chromium, at desktop (1280×800) and
mobile (390×844) width, and saves a full-page screenshot of each page plus a
video of the whole walk. Use it to check a UI change without clicking through
by hand, and attach the screenshots to the PR.

Playwright is **not** a project dependency. `uv run --with playwright` installs
it for that one command only, so `pyproject.toml` and `uv.lock` never change.
This is Python run on your machine, not JavaScript in a template, so the
no-JavaScript rule (AGENTS.md) does not apply to it.

## Run it

Two terminals, both from the repo root.

```bash
# terminal 1 — the demo server (in-memory fakes, no database needed)
uv run python -m scripts.demo
```

```bash
# terminal 2 — first time only: download the browser
uv run --with playwright playwright install chromium

# walk the default pages (/classes, /documents)
uv run --with playwright python -I playwright/ui_walk.py

# walk the pages you changed
uv run --with playwright python -I playwright/ui_walk.py /classes /admin

# T-06: also upload a sample PDF and screenshot the result
uv run --with playwright python -I playwright/ui_walk.py --upload-pdf
```

`-I` runs Python isolated from the repo folder and your environment
variables, so the script sees only the installed `playwright` package. The
folder is named `playwright/` but has no `__init__.py`, so it never shadows the
package either way.

## Options

| Option | Default | Meaning |
|---|---|---|
| `PATH ...` | `/classes /documents` | pages to open, in order |
| `--as EMAIL` | `somchai.p@kmitl.ac.th` (Instructor) | demo account from the "log in as" list on `/login`, e.g. `admin@kmitl.ac.th` |
| `--base-url URL` | `http://127.0.0.1:8000` | server to walk |
| `--out DIR` | `playwright/out` | where screenshots and videos go |
| `--upload-pdf` | off | upload a sample PDF on `/documents` (T-06) after the walk |

## Output

Everything lands in `playwright/out/`, which is git-ignored:

- `desktop-<page>.png`, `mobile-<page>.png` — one full-page screenshot per page
- `desktop-documents-after-upload.png`, `mobile-…` — with `--upload-pdf`
- `video-desktop/*.webm`, `video-mobile/*.webm` — the whole walk

On macOS: `open playwright/out`.

## What to look at

- Desktop: rows that should be side by side are not stacked. If they are, the
  Tailwind classes are probably missing from `app.css`; rebuild it
  (README "Styles").
- Mobile: nothing overflows sideways, and buttons are not cut off.
- Colours, cards, badges and the page header match the other pages.
