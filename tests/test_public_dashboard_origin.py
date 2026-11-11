"""Opt-in dashboard origins; in-process ASGI only, no server or account startup."""

import importlib
import shlex
from pathlib import Path
from typing import NoReturn

import httpx
import pytest
from fastapi import FastAPI

from multi_exchange_trader import api


def probe_app() -> FastAPI:
    app = api.create_app()

    async def probe() -> dict[str, str]:
        return {"mode": "BOUNDARY_TEST"}

    app.add_api_route("/api/v1/settings/origin-probe", probe, methods=["GET", "POST"])
    return app


async def test_explicit_https_origin_allows_original_public_host_without_rewriting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_DASHBOARD_ORIGIN", "https://dashboard.example:8443")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=probe_app()),
                                base_url="https://dashboard.example:8443") as client:
        assert (await client.get("/api/v1/settings/origin-probe")).status_code == 200
        response = await client.post("/api/v1/settings/origin-probe", json={}, headers={
            "Origin": "https://dashboard.example:8443", "Sec-Fetch-Site": "same-origin",
        })
        assert response.status_code == 200 and response.json() == {"mode": "BOUNDARY_TEST"}


async def test_public_host_origin_duplicates_and_existing_csrf_json_limits_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_DASHBOARD_ORIGIN", "https://dashboard.example")
    origin = "https://dashboard.example"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=probe_app()),
                                base_url=origin) as client:
        invalid = [
            [("Host", "dashboard.example.evil")],
            [("Host", "dashboard.example@evil.example")],
            [("Host", "dashboard.example/path")],
            [("Host", "dashboard.example"), ("Host", "localhost")],
            [("Host", "dashboard.example,localhost")],
            [("Origin", origin), ("Origin", origin)],
            [("Origin", origin + ",https://evil.example")],
            [("Origin", "http://dashboard.example")],
            [("Origin", "https://dashboard.example.evil")],
            [("Origin", "http://localhost:5173")],
            [("Host", "evil.example"), ("X-Forwarded-Host", "dashboard.example")],
        ]
        for headers in invalid:
            response = await client.get("/api/v1/settings/origin-probe", headers=headers)
            assert response.status_code == 403, headers
            assert response.json() == {"reason": "paper_local_only"}
        response = await client.post("/api/v1/settings/origin-probe", json={}, headers={
            "Origin": origin, "Sec-Fetch-Site": "cross-site",
        })
        assert response.status_code == 403
        assert (await client.post("/api/v1/settings/origin-probe", content="{}",
            headers={"Origin": origin, "Content-Type": "text/plain"})).status_code == 415
        assert (await client.post("/api/v1/settings/origin-probe", content=b" " * 8193,
            headers={"Origin": origin, "Content-Type": "application/json"})).status_code == 413


async def test_absent_setting_keeps_local_dashboard_and_public_market_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PUBLIC_DASHBOARD_ORIGIN", raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=probe_app()),
                                base_url="http://localhost:8000") as client:
        path = "/api/v1/settings/origin-probe"
        assert (await client.get(path)).status_code == 200
        assert (await client.get(path, headers={"Host": "dashboard.example"})).status_code == 403
        assert (await client.post(path, json={},
            headers={"Origin": "http://localhost:5173"})).status_code == 200
        assert (await client.post(path, json={},
            headers={"Origin": "https://evil.example"})).status_code == 403
        assert (await client.post(path, json={},
            headers={"Sec-Fetch-Site": "cross-site"})).status_code == 403
        assert (await client.get("/api/v1/health", headers={"Host": "public.example"})).json() == {
            "status": "ok",
        }


async def test_public_account_read_stays_data_only_and_order_routes_remain_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_DASHBOARD_ORIGIN", "https://dashboard.example")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=probe_app()),
                                base_url="https://dashboard.example") as client:
        response = await client.get("/api/v1/account/bybit/view")
        assert response.status_code == 200
        assert response.json()["상태코드"] == "missing_credentials"
        assert response.json()["주문보냄"] is False
        assert (await client.get("/api/v1/account/bybit/view",
            headers={"Host": "evil.example"})).status_code == 403
        assert (await client.get("/api/v1/account/bybit/view",
            headers={"Origin": "https://evil.example"})).status_code == 403
        for path in ("/api/v1/account/bybit/orders", "/api/v1/account/bybit/leverage"):
            assert (await client.post(path, json={}, headers={
                "Origin": "https://dashboard.example",
            })).status_code == 403


@pytest.mark.parametrize("value", [
    "", "http://dashboard.example", "https://*.example", "https://u:p@dashboard.example",
    "https://dashboard.example/", "https://dashboard.example/path", "https://dashboard.example?x=1",
    "https://dashboard.example#x", "https://dashboard.example https://other.example",
    "https://dashboard.example,https://other.example", "https://dashboard.example\n",
    "https://dashboard.example\t", "https://dashboard.example\\evil", "https://dashboard.example.",
    "https://127.000.000.001", "https://2130706433", "https://dashboard.example:0",
    "https://dashboard.example:65536", "https://dashboard.example:", "https://%64ashboard.example",
])
def test_invalid_present_origin_fails_before_credentials_or_lifespan(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("credential startup must not be reached")

    monkeypatch.setenv("PUBLIC_DASHBOARD_ORIGIN", value)
    monkeypatch.setattr(api, "CredentialStartup", forbidden)
    with pytest.raises(ValueError, match="^INVALID_PUBLIC_DASHBOARD_ORIGIN$"):
        api.create_app()


def test_https_parser_preserves_single_canonical_origin_without_default_authority() -> None:
    module = importlib.import_module("multi_exchange_trader.public_dashboard_origin")
    assert module.parse_public_dashboard_origin(None) is None
    for value, expected in (
        ("https://dashboard.example", "https://dashboard.example"),
        ("https://dashboard.example:8443", "https://dashboard.example:8443"),
        ("https://203.0.113.7", "https://203.0.113.7"),
        ("https://[2001:db8::7]:8443", "https://[2001:db8::7]:8443"),
        ("https://dashboard.example:443", "https://dashboard.example"),
    ):
        assert module.parse_public_dashboard_origin(value).origin == expected


def test_public_nginx_template_inherits_auth_for_both_locations_and_preserves_headers() -> None:
    root = Path(__file__).resolve().parents[1]
    lexer = shlex.shlex((root / "deploy/linux/nginx-public.conf.example").read_text(
        encoding="utf-8"), posix=True, punctuation_chars="{};")
    lexer.whitespace_split = True
    tokens = list(lexer)
    assert tokens[:2] == ["server", "{"] and tokens[-1] == "}"
    depth = 0
    directives: list[tuple[int, list[str]]] = []
    statement: list[str] = []
    for token in tokens:
        if token == "{":
            depth += 1
            statement = []
        elif token == "}":
            depth -= 1
            statement = []
        elif token == ";":
            directives.append((depth, statement))
            statement = []
        else:
            statement.append(token)
    assert (1, ["listen", "443", "ssl"]) in directives
    assert (1, ["auth_basic", "Trader dashboard"]) in directives
    assert (1, ["auth_basic_user_file", "/etc/nginx/trader.htpasswd"]) in directives
    assert (1, ["satisfy", "all"]) in directives
    assert sum(row[:1] == ["auth_basic"] for _depth, row in directives) == 1
    assert sum(token == "location" for token in tokens) == 2
    assert (2, ["proxy_pass", "http://unix:/run/trader-api.sock:/api/"]) in directives
    assert (2, ["proxy_set_header", "Host", "$http_host"]) in directives
    assert (2, ["proxy_set_header", "Origin", "$http_origin"]) in directives
    assert (2, ["proxy_set_header", "X-Trader-Operator", "$remote_user"]) in directives
    assert (2, ["proxy_set_header", "Authorization", ""]) in directives
    assert (1, ["ssl_protocols", "TLSv1.2", "TLSv1.3"]) in directives

    local_template = (root / "deploy/linux/nginx.conf").read_text(encoding="utf-8")
    assert 'listen 127.0.0.1:5173 default_server;' in local_template
    assert 'auth_basic "Trader dashboard";' in local_template
    assert 'auth_basic_user_file /etc/nginx/trader.htpasswd;' in local_template
    assert "proxy_set_header X-Trader-Operator $remote_user;" in local_template
    assert 'proxy_set_header Authorization "";' in local_template
