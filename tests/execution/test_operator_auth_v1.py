"""Nginx Basic Auth identity bridge; no credential or provider access."""

import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import time
from base64 import b64encode
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import httpx
import pytest
from fastapi import FastAPI, Request

from multi_exchange_trader import api
from multi_exchange_trader.execution.operator_auth_v1 import (
    verified_operator_principal_from_state_v1,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def application(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.delenv("PUBLIC_DASHBOARD_ORIGIN", raising=False)
    app = api.create_app()

    @app.get("/__test/operator-principal")
    async def principal(request: Request) -> dict[str, object] | None:
        value = verified_operator_principal_from_state_v1(request)
        if value is None:
            return None
        return {
            "principal_id": value.principal_id,
            "evidence_reference": value.evidence_reference,
            "evidence_sha256": value.evidence_sha256,
            "authentication_method": value.authentication_method,
        }

    return app


async def test_create_app_rejects_tcp_assertions(application: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, client=("127.0.0.1", 4312)),
        base_url="https://dashboard.example",
    ) as client:
        valid = await client.get("/__test/operator-principal", headers={
            "X-Trader-Operator": "trader-1",
        })
        missing = await client.get("/__test/operator-principal")
        malformed = await client.get("/__test/operator-principal", headers={
            "X-Trader-Operator": "trader/attacker",
        })
        duplicated = await client.get("/__test/operator-principal", headers=[
            ("X-Trader-Operator", "trader-1"),
            ("X-Trader-Operator", "trader-2"),
        ])
        leaked_basic_credentials = await client.get("/__test/operator-principal", headers={
            "X-Trader-Operator": "trader-1",
            "Authorization": "Basic synthetic-only",
        })

    assert valid.json() is None
    assert missing.json() is None
    assert malformed.json() is None
    assert duplicated.json() is None
    assert leaked_basic_credentials.json() is None


async def test_remote_peer_cannot_supply_trusted_operator_even_with_forwarded_for(
    application: FastAPI,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, client=("203.0.113.8", 4312)),
        base_url="https://dashboard.example",
    ) as client:
        response = await client.get("/__test/operator-principal", headers={
            "X-Trader-Operator": "trader-1",
            "X-Forwarded-For": "127.0.0.1",
        })

    assert response.json() is None


async def test_loopback_tcp_header_cannot_mint_operator_authentication_evidence(
    application: FastAPI,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, client=("127.0.0.1", 4312)),
        base_url="https://dashboard.example",
    ) as client:
        first = await client.get("/__test/operator-principal", headers={
            "X-Trader-Operator": "trader-1",
        })
        second = await client.get("/__test/operator-principal", headers={
            "X-Trader-Operator": "trader-1",
        })
        other = await client.get("/__test/operator-principal", headers={
            "X-Trader-Operator": "trader-2",
        })

    assert first.json() is second.json() is other.json() is None


@pytest.mark.skipif(sys.platform != "linux", reason="Systemd-style fd socket requires Linux")
def test_basic_auth_to_publication_over_uds(
    tmp_path: Path,
) -> None:
    nginx = shutil.which("nginx")
    htpasswd = shutil.which("htpasswd")
    if not nginx or not htpasswd:
        if os.environ.get("REQUIRE_LINUX_OPERATIONS") == "1":
            pytest.fail("Linux operations CI must install nginx and apache2-utils")
        pytest.skip("nginx and htpasswd are required for the Linux auth bridge test")

    from test_strategy_publication_v1 import fixture_book
    from test_strategy_publication_api_v1 import builtin_publication_body

    book = fixture_book(tmp_path)
    socket_path = tmp_path / "api.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    os.chmod(socket_path, 0o600)
    listener.listen(16)
    tcp_reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_reservation.bind(("127.0.0.1", 0))
    tcp_port = tcp_reservation.getsockname()[1]

    nginx_reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    nginx_reservation.bind(("127.0.0.1", 0))
    nginx_port = nginx_reservation.getsockname()[1]
    nginx_reservation.close()
    password_file = tmp_path / "dashboard.htpasswd"
    subprocess.run(
        [htpasswd, "-B", "-b", "-c", str(password_file), "trader-1", "synthetic-password"],
        capture_output=True, text=True, check=True, timeout=5,
    )
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist/index.html").write_text("synthetic dashboard", encoding="utf-8")

    child = r"""
import os
from multi_exchange_trader.execution import operator_auth_v1
operator_auth_v1._NGINX_SOCKET_PATH = os.environ["TRADER_TEST_SOCKET_PATH"]
from multi_exchange_trader.api import app
import uvicorn
uvicorn.run(app, fd=int(os.environ["TRADER_TEST_SOCKET_FD"]), host="127.0.0.1",
            port=int(os.environ["TRADER_TEST_TCP_PORT"]), proxy_headers=False,
            log_level="warning")
"""
    environment = {
        name: os.environ[name]
        for name in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
        if name in os.environ
    }
    environment.update({
        "TRADER_TEST_SOCKET_PATH": str(socket_path),
        "TRADER_TEST_SOCKET_FD": str(listener.fileno()),
        "TRADER_TEST_TCP_PORT": str(tcp_port),
        "LIVE_TRADING_ENABLED": "false",
        "BYBIT_DEMO": "false",
        "BYBIT_TESTNET": "false",
        "BYBIT_ENV_FILE": "off",
        "AI_ENV_FILE": "off",
        "ANTHROPIC_ENV_FILE": "off",
        "RUNTIME_POLICY_DB_PATH": str(book.path),
        "PUBLIC_DASHBOARD_ORIGIN": "https://dashboard.example",
        "PAPER_DB_PATH": str(tmp_path / "paper.sqlite3"),
        "PAPER_SPOT_DB_PATH": str(tmp_path / "spot-paper.sqlite3"),
        "PAPER_REVIEW_DB_PATH": str(tmp_path / "order-reviews.sqlite3"),
        "AI_DB_PATH": str(tmp_path / "ai.sqlite3"),
        "RECOMMENDATION_DB_PATH": str(tmp_path / "recommendations.sqlite3"),
        "PHASE4_DB_PATH": str(tmp_path / "research.sqlite3"),
        "PHASE4_DATA_ROOT": str(tmp_path / "datasets"),
        "PYTHONPATH": str(ROOT / "src"),
    })
    public_template = (ROOT / "deploy/linux/nginx-public.conf.example").read_text(encoding="utf-8")
    server_block = public_template.replace(
        "listen 443 ssl;", f"listen 127.0.0.1:{nginx_port};",
    ).replace("server_name dashboard.example.invalid;", "server_name dashboard.example;")
    server_block = server_block.replace(
        "    ssl_certificate /etc/trader/tls/fullchain.pem;\n", "",
    ).replace("    ssl_certificate_key /etc/trader/tls/privkey.pem;\n", "")
    server_block = server_block.replace(
        "/etc/nginx/trader.htpasswd", str(password_file),
    ).replace("/run/trader-api.sock", str(socket_path))
    server_block = server_block.replace("/opt/trader/app/apps/web/dist", str(tmp_path / "dist"))
    nginx_config = tmp_path / "nginx.conf"
    temporary_paths = " ".join(
        f"{module}_temp_path {tmp_path}/{module};"
        for module in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi")
    )
    nginx_config.write_text(
        f"pid {tmp_path}/nginx.pid;\nerror_log {tmp_path}/nginx-error.log;\n"
        f"events {{}}\nhttp {{ access_log off; {temporary_paths} {server_block} }}\n",
        encoding="utf-8",
    )
    nginx_args = [nginx, "-p", str(tmp_path), "-c", str(nginx_config)]
    checked = subprocess.run(
        [*nginx_args, "-t"], capture_output=True, text=True, check=False, timeout=10,
    )
    assert checked.returncode == 0, checked.stderr
    opener = build_opener(ProxyHandler({}))
    process: subprocess.Popen[str] | None = None
    nginx_process: subprocess.Popen[str] | None = None

    def post_preview(
        *, authorization: str | None, forged_operator: str | None,
    ) -> tuple[int, dict[str, object]]:
        body = json.dumps(builtin_publication_body(), separators=(",", ":")).encode("utf-8")
        headers = {
            "Host": "dashboard.example",
            "Origin": "https://dashboard.example",
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        if authorization is not None:
            headers["Authorization"] = authorization
        if forged_operator is not None:
            headers["X-Trader-Operator"] = forged_operator
        request = Request(
            f"http://127.0.0.1:{nginx_port}/api/v1/automatic/strategies/preview",
            data=body, headers=headers, method="POST",
        )
        try:
            with opener.open(request, timeout=4) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            try:
                return error.code, json.load(error)
            except json.JSONDecodeError:
                return error.code, {}

    try:
        process = subprocess.Popen(
            [sys.executable, "-c", child], cwd=ROOT, env=environment,
            pass_fds=(listener.fileno(),), stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True,
        )
        nginx_process = subprocess.Popen(
            [*nginx_args, "-g", "daemon off; master_process off;"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.monotonic() + 8
        authorization = "Basic " + b64encode(b"trader-1:synthetic-password").decode("ascii")
        while True:
            try:
                readiness = Request(
                    f"http://127.0.0.1:{nginx_port}/",
                    headers={"Authorization": authorization},
                )
                with opener.open(readiness, timeout=1) as response:
                    assert response.read() == b"synthetic dashboard"
                break
            except (ConnectionError, OSError, HTTPError):
                assert nginx_process.poll() is None, "nginx exited before accepting requests"
                assert process.poll() is None, "Uvicorn exited before accepting the systemd fd"
                if time.monotonic() >= deadline:
                    pytest.fail("nginx did not become ready on loopback")
                time.sleep(0.05)

        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
        unauthenticated = post_preview(authorization=None, forged_operator="trader-evil")
        assert unauthenticated[0] == 401
        assert book.read()["revision"] == 1 and book.publication_snapshot_v1() is None

        principal_auth = post_preview(authorization=authorization, forged_operator="forged-user")
        assert principal_auth[0] == 200
        preview = principal_auth[1]
        assert preview["authenticated_operator"] == {
            "principal_id": "trader-1",
            "evidence_reference": "nginx-basic-auth-v1",
            "evidence_sha256": hashlib.sha256(
                b"nginx-basic-auth-v1\0trader-1"
            ).hexdigest(),
            "authentication_method": "nginx-basic-auth-v1",
        }
        published_body = {
            **builtin_publication_body(),
            "confirmation_fingerprint": preview["confirmation_fingerprint"],
        }
        publish_bytes = json.dumps(published_body, separators=(",", ":")).encode("utf-8")
        publish_request = Request(
            f"http://127.0.0.1:{nginx_port}/api/v1/automatic/strategies/publish",
            data=publish_bytes,
            headers={
                "Host": "dashboard.example", "Origin": "https://dashboard.example",
                "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json",
                "Content-Length": str(len(publish_bytes)), "Authorization": authorization,
                "X-Trader-Operator": "forged-user",
            }, method="POST",
        )
        with opener.open(publish_request, timeout=4) as response:
            assert response.status == 200
            published = json.load(response)
        assert published["generation"] == 1
        assert book.read()["desired_state"] == "STOPPED"
        assert book.read()["live_order_enabled"] is False

    finally:
        if nginx_process is not None:
            if nginx_process.poll() is None:
                nginx_process.terminate()
            try:
                nginx_process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                nginx_process.kill()
                nginx_process.communicate(timeout=5)
        stderr = ""
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                _, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                _, stderr = process.communicate(timeout=5)
        tcp_reservation.close()
        listener.close()
        socket_path.unlink(missing_ok=True)
        if process is not None and process.returncode not in (0, -15):
            pytest.fail(f"Uvicorn probe exited unexpectedly: {stderr}")
