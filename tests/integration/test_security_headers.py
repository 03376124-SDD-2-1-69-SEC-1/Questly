"""Security headers on every response and API docs hidden in production (OPS-44)."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from questly.main import CONTENT_SECURITY_POLICY
from tests.fakes.app import build_app
from tests.integration.forms import asgi_client

COMMON_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
}


async def _get(app: FastAPI, path: str) -> tuple[int, dict[str, str]]:
    async with asgi_client(app) as client:
        response = await client.get(path)
    return response.status_code, dict(response.headers)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path", ["/login", "/api/v1/auth/me", "/static/css/app.css", "/health", "/nope"]
)
async def test_every_response_carries_the_common_headers(path: str) -> None:
    _, headers = await _get(build_app(), path)

    for name, value in COMMON_HEADERS.items():
        assert headers[name] == value, name


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/login", "/signup", "/nope"])
async def test_html_pages_carry_the_content_security_policy(path: str) -> None:
    _, headers = await _get(build_app(), path)

    assert headers["content-type"].startswith("text/html")
    assert headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert "script-src 'none'" in headers["content-security-policy"]
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path", ["/api/v1/auth/me", "/static/css/app.css", "/health", "/openapi.json"]
)
async def test_non_html_responses_have_no_content_security_policy(path: str) -> None:
    _, headers = await _get(build_app(), path)

    assert "content-security-policy" not in headers


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/docs", "/redoc"])
async def test_the_api_docs_pages_are_left_without_a_csp(path: str) -> None:
    status, headers = await _get(build_app(), path)

    assert status == 200
    assert headers["content-type"].startswith("text/html")
    assert "content-security-policy" not in headers


@pytest.mark.anyio
async def test_hsts_only_when_cookies_are_secure() -> None:
    _, plain = await _get(build_app(), "/login")
    _, secure = await _get(build_app(secure_cookies=True), "/login")

    assert "strict-transport-security" not in plain
    assert secure["strict-transport-security"] == "max-age=31536000"


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_docs_are_served_by_default(path: str) -> None:
    status, _ = await _get(build_app(), path)

    assert status == 200


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_docs_are_gone_when_not_exposed(path: str) -> None:
    status, _ = await _get(build_app(expose_docs=False), path)

    assert status == 404


@pytest.mark.anyio
async def test_hidden_docs_paths_get_the_csp_on_their_404_page() -> None:
    status, headers = await _get(build_app(expose_docs=False), "/docs")

    assert status == 404
    assert headers["content-security-policy"] == CONTENT_SECURITY_POLICY


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/boom", "/api/v1/boom"])
async def test_unhandled_500_carries_the_security_headers(path: str) -> None:
    app = build_app(secure_cookies=True)

    def boom() -> None:
        raise RuntimeError("bug")

    app.add_api_route("/boom", boom)
    app.add_api_route("/api/v1/boom", boom)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        response = await c.get(path)

    assert response.status_code == 500
    for name, value in COMMON_HEADERS.items():
        assert response.headers[name] == value, name
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    assert ("content-security-policy" in response.headers) == (path == "/boom")
