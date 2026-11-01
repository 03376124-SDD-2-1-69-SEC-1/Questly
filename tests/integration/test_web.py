"""Integration tests for the shared web layout."""

import os
import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fakes.app import build_app

STATIC_DIR = Path("src/questly/web/static")


@pytest.mark.anyio
async def test_stylesheet_url_carries_the_file_version() -> None:
    """A rebuilt app.css must reach the browser without a manual cache clear."""
    stylesheet = STATIC_DIR / "css" / "app.css"
    version = int(stylesheet.stat().st_mtime)
    transport = ASGITransport(app=build_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        page = await client.get("/login")
        css = await client.get(f"/static/css/app.css?v={version}")

    assert f'href="/static/css/app.css?v={version}"' in page.text
    assert css.status_code == 200
    assert ".btn-primary" in css.text


def _block_spans(css: str, opener: str) -> list[range]:
    """Character ranges of every `opener { ... }` block, braces matched."""
    spans = []
    for match in re.finditer(re.escape(opener), css):
        start = css.index("{", match.end())
        depth = 0
        for end in range(start, len(css)):
            depth += {"{": 1, "}": -1}.get(css[end], 0)
            if depth == 0:
                spans.append(range(match.start(), end + 1))
                break
    return spans


@pytest.mark.anyio
async def test_loading_icons_move_only_without_reduced_motion() -> None:
    transport = ASGITransport(app=build_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        css = (await client.get("/static/css/app.css")).text

    motion_ok = _block_spans(css, "@media (prefers-reduced-motion: no-preference)")
    keyframes = [m.start() for m in re.finditer(r"@keyframes loading-", css)]

    assert keyframes
    assert [at for at in keyframes if not any(at in span for span in motion_ok)] == []


async def _stylesheet() -> str:
    transport = ASGITransport(app=build_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return (await client.get("/static/css/app.css")).text


@pytest.mark.anyio
async def test_toast_hides_without_moving_under_reduced_motion() -> None:
    """The S-02 toast covers links; with motion off it must still go away."""
    css = await _stylesheet()
    reduce = _block_spans(css, "@media (prefers-reduced-motion: reduce)")

    [rule] = [
        m.group(1)
        for m in re.finditer(r"\.toast \{([^{}]*)\}", css)
        if any(m.start() in span for span in reduce)
    ]
    animation = re.search(r"animation:\s*([^;]+);", rule)
    assert animation is not None
    name, duration, delay, fill = animation.group(1).split()
    [frames] = [
        css[span.start : span.stop] for span in _block_spans(css, f"@keyframes {name} ")
    ]

    assert (duration, fill) == ("0s", "forwards")
    assert float(delay.removesuffix("s")) >= 5
    assert "visibility: hidden" in frames
    assert "transform" not in frames


@pytest.mark.anyio
async def test_hover_lifts_only_without_reduced_motion() -> None:
    css = await _stylesheet()
    motion_ok = _block_spans(css, "@media (prefers-reduced-motion: no-preference)")

    lifts = [
        (m.group(1).strip(), any(m.start() in span for span in motion_ok))
        for m in re.finditer(r"([^{}]*:hover[^{}]*)\{([^{}]*)\}", css)
        if "transform:" in m.group(2)
    ]

    assert [selector for selector, guarded in lifts if not guarded] == []
    assert {".btn-primary:hover", ".class-card:hover"} <= {s for s, _ in lifts}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("rel", "side"),
    [
        ('rel="icon" type="image/png" sizes="32x32"', 32),
        ('rel="apple-touch-icon"', 180),
    ],
)
async def test_page_links_a_small_questly_icon(rel: str, side: int) -> None:
    transport = ASGITransport(app=build_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        page = await client.get("/login")
        link = re.search(rf'<link {re.escape(rel)} href="([^"]+)"', page.text)
        assert link is not None, f"no <link {rel}> on /login"
        icon = await client.get(link.group(1))

    width = int.from_bytes(icon.content[16:20], "big")
    height = int.from_bytes(icon.content[20:24], "big")

    assert icon.status_code == 200
    assert icon.headers["content-type"] == "image/png"
    assert icon.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert (width, height) == (side, side)
    assert len(icon.content) < 50_000


def test_asset_url_changes_when_the_file_changes(tmp_path, monkeypatch) -> None:
    import questly.main as main

    (tmp_path / "css").mkdir()
    built = tmp_path / "css" / "app.css"
    built.write_text("a{}")
    monkeypatch.setattr(main, "_STATIC_DIR", tmp_path)
    os.utime(built, (1_000, 1_000))
    before = main.asset_url("css/app.css")
    os.utime(built, (2_000, 2_000))

    assert before == "/static/css/app.css?v=1000"
    assert main.asset_url("css/app.css") == "/static/css/app.css?v=2000"
