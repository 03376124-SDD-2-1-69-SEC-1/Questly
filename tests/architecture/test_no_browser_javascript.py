"""Architecture guard for project-authored HTML."""

import re
from pathlib import Path

TEMPLATE_ROOT = Path("src/questly/web/templates")
FORBIDDEN = re.compile(r"<script\b|javascript:|\son[a-z]+\s*=", re.IGNORECASE)
JAVASCRIPT_TEMPLATE_ALLOWLIST = {"instructor/t06_documents.html"}


def test_project_templates_do_not_contain_browser_javascript_outside_t06() -> None:
    templates = list(TEMPLATE_ROOT.rglob("*.html"))

    assert templates, "expected project-authored templates"
    for template in templates:
        relative = template.relative_to(TEMPLATE_ROOT).as_posix()
        if relative in JAVASCRIPT_TEMPLATE_ALLOWLIST:
            continue
        assert FORBIDDEN.search(template.read_text(encoding="utf-8")) is None, template


def test_t06_javascript_is_scoped_to_the_file_picker() -> None:
    template = TEMPLATE_ROOT / "instructor/t06_documents.html"
    content = template.read_text(encoding="utf-8")

    assert content.count("<script>") == 1
    assert "data-document-file" in content
    assert "data-document-filename" in content
