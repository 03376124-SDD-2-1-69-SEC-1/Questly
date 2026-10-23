"""Walk Questly pages in a real browser and save screenshots and videos.

Runs against the demo server (`uv run python -m scripts.demo`), logs in through
the "log in as" buttons on G-01, opens each page at desktop and mobile width,
and writes everything under `playwright/out/` (git-ignored).

    uv run --with playwright python -I playwright/ui_walk.py
    uv run --with playwright python -I playwright/ui_walk.py /classes /documents
    uv run --with playwright python -I playwright/ui_walk.py --upload-pdf

Playwright is not a project dependency: `--with` installs it for this run only.
See playwright/README.md.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_ACCOUNT = "somchai.p@kmitl.ac.th"
DEFAULT_PAGES = ("/classes", "/documents")
OUT_DIR = Path(__file__).resolve().parent / "out"
VIEWPORTS = {"desktop": (1280, 800), "mobile": (390, 844)}

# Smallest PDF a browser upload accepts; enough for T-06 to store a document.
SAMPLE_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


def _slug(path: str) -> str:
    return path.strip("/").replace("/", "-") or "home"


def _log_in(page: Page, base_url: str, account: str) -> None:
    page.goto(f"{base_url}/login")
    page.locator(f"form:has(input[value='{account}']) button").click()
    page.wait_for_load_state()


def _upload_pdf(page: Page, base_url: str, pdf: Path, out: Path, label: str) -> None:
    page.goto(f"{base_url}/documents")
    page.set_input_files("input[type=file]", str(pdf))
    page.locator("form[action='/documents/upload'] button[type=submit]").click()
    page.wait_for_load_state()
    page.wait_for_timeout(1500)
    page.screenshot(path=out / f"{label}-documents-after-upload.png", full_page=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pages", nargs="*", default=list(DEFAULT_PAGES))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--as", dest="account", default=DEFAULT_ACCOUNT)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument(
        "--upload-pdf",
        action="store_true",
        help="also upload a sample PDF on /documents (T-06) and screenshot the result",
    )
    args = parser.parse_args()

    base_url: str = args.base_url.rstrip("/")
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    pdf = out / "sample.pdf"
    pdf.write_bytes(SAMPLE_PDF)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for label, (width, height) in VIEWPORTS.items():
            context = browser.new_context(
                viewport={"width": width, "height": height},
                record_video_dir=str(out / f"video-{label}"),
                record_video_size={"width": width, "height": height},
            )
            page = context.new_page()
            _log_in(page, base_url, args.account)
            for path in args.pages:
                page.goto(base_url + path)
                page.wait_for_timeout(1200)
                shot = out / f"{label}-{_slug(path)}.png"
                page.screenshot(path=shot, full_page=True)
            if args.upload_pdf:
                _upload_pdf(page, base_url, pdf, out, label)
            context.close()
        browser.close()
    print(f"done: {out}")


if __name__ == "__main__":
    main()
