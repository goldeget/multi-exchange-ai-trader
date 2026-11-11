"""Run deployment boundaries without installing services or contacting providers."""

import argparse
import configparser
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from base64 import b64encode
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from multi_exchange_trader.account.config import MissingBybitCredentials, require_demo_credentials
from multi_exchange_trader.account_profiles import AccountProfileStore
from multi_exchange_trader.credential_resolution import CredentialResolver
from multi_exchange_trader.credentials import CredentialStore
from multi_exchange_trader.worker_account_selection import resolve_worker_expectations

ROOT = Path(__file__).resolve().parents[2]


def _template_environment(name: str) -> dict[str, str]:
    entries = shlex.split((ROOT / "deploy/linux" / name).read_text(encoding="utf-8"),
                          comments=True)
    return dict(entry.split("=", 1) for entry in entries)


def test_runtime_host_blocklist_matches_actual_supported_public_hosts() -> None:
    from multi_exchange_trader.market_data.public_client import PUBLIC_HOSTS, parse_blocked_hosts

    value = _template_environment("runtime.env.example")["MARKET_DATA_BLOCKED_HOSTS"]
    assert parse_blocked_hosts(value) == PUBLIC_HOSTS


def test_api_and_monitor_share_explicit_persistent_registration_paths() -> None:
    api = _template_environment("runtime.env.example")
    worker = _template_environment("monitor-worker.env.example")
    for name, expected in {
        "TRADER_CREDENTIAL_FILE": "/var/lib/trader/private/credentials.json",
        "TRADER_ACCOUNT_PROFILE_FILE": "/var/lib/trader/private/account-profiles.json",
    }.items():
        assert api[name] == worker[name] == expected
        assert PurePosixPath(worker[name]).is_absolute()
    assert worker["TRADER_MONITOR_SYMBOL"] == "BTCUSDT"
    assert not {
        "BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET", "BYBIT_DEMO_UID",
        "BYBIT_DEMO_PARENT_UID", "BYBIT_DEMO_IS_MASTER",
    }.intersection(worker)


def test_monitor_command_uses_saved_identity_and_retains_monitoring_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution import worker_cli
    from multi_exchange_trader.execution.observation_instrument_v1 import (
        SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
    )

    unit = configparser.ConfigParser(interpolation=None, strict=False)
    unit.read(ROOT / "deploy/linux/trader-worker.service", encoding="utf-8")
    command = shlex.split(unit["Service"]["ExecStart"])
    assert command[0] == "/usr/bin/env"
    interpreter = command.index("/opt/trader/app/.venv/bin/python")
    enforced = dict(entry.split("=", 1) for entry in command[1:interpreter])
    assert enforced == {
        "LIVE_TRADING_ENABLED": "false", "BYBIT_DEMO": "true", "BYBIT_TESTNET": "false",
        "BYBIT_ENV_FILE": "off", "AI_ENV_FILE": "off", "ANTHROPIC_ENV_FILE": "off",
    }
    assert command[interpreter + 1:] == [
        "-m", "multi_exchange_trader.execution.worker_cli",
        "--run-monitor", "--connect-monitoring",
        "--observation-fee-profile", SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
        "--symbol", "${TRADER_MONITOR_SYMBOL}",
        "--directory", "/var/lib/trader/data",
        "--policy-db", "/var/lib/trader/data/runtime-policy.sqlite3",
        "--lock-directory", "/var/lib/trader/locks",
    ]
    assert unit["Service"]["EnvironmentFile"] == "/etc/trader/monitor-worker.env"
    assert unit["Service"]["TimeoutStopSec"] == "60"
    assert unit["Service"]["KillMode"] == "control-group"
    assert unit["Service"]["UMask"] == "0077"
    assert unit["Service"]["ReadWritePaths"] == "/var/lib/trader"
    captured: list[argparse.Namespace] = []

    async def capture(args: argparse.Namespace) -> None:
        captured.append(args)

    monkeypatch.setattr(worker_cli, "sys", SimpleNamespace(platform="linux", stderr=sys.stderr))
    monkeypatch.setattr(worker_cli, "_run", capture)
    symbol = _template_environment("monitor-worker.env.example")["TRADER_MONITOR_SYMBOL"]
    argv = [word.replace("${TRADER_MONITOR_SYMBOL}", symbol)
            for word in command[interpreter + 3:]]
    assert worker_cli.main(argv) == 0
    assert len(captured) == 1
    assert captured[0].observation_fee_profile == SDK1231_OBSERVATION_UNKNOWN_FEES_V1
    assert captured[0].uid is captured[0].parent_uid is captured[0].is_master is None
    assert captured[0].account_profile is captured[0].profile_store is None


def test_api_legacy_environment_is_optional_after_persistent_registration() -> None:
    # Preserve both directives: ConfigParser alone keeps only the final duplicate.
    lines = (ROOT / "deploy/linux/trader.service").read_text(encoding="utf-8").splitlines()
    files = [line.split("=", 1)[1] for line in lines if line.startswith("EnvironmentFile=")]
    assert files == ["/etc/trader/runtime.env", "-/etc/trader/credentials.env"]


def test_monitor_service_resolves_registered_pair_and_profile_before_authentication(
    tmp_path: Path,
) -> None:
    # Genuine private stores containing synthetic configuration, never account authentication.
    credential_path = tmp_path / "private" / "credentials.json"
    profile_path = tmp_path / "private" / "account-profiles.json"
    CredentialStore(credential_path).register({
        "BYBIT_DEMO_API_KEY": "synthetic-monitor-key",
        "BYBIT_DEMO_API_SECRET": "synthetic-monitor-secret",
    }, expected_revision=None)
    store = AccountProfileStore(profile_path)
    store.register_uid("12345", expected_revision=None)
    profile = store.load()
    assert profile is not None
    stamp = "2026-11-03T00:00:00Z"
    store._adopt_initial(profile_id=profile.profile_id, expected_uid="12345",
        expected_revision=1, relationship={
            "parent_uid": "0", "is_master": True,
            "basis": "PROVIDER_OBSERVED_OPERATOR_ADOPTED", "source": "BYBIT_DEMO_QUERY_API",
            "evidence_id": "a24bd44b-422d-409c-8563-91458ed1eb18", "confirmed_at": stamp,
            "provider_observed_at": stamp, "policy": "uid-anchored-adoption-v1",
        }, last_observation={
            "source": "BYBIT_DEMO_QUERY_API", "parent_uid": "0", "is_master": True,
            "observed_at": stamp, "server_observed_at": stamp,
        }, guard=lambda: None)
    unit = configparser.ConfigParser(interpolation=None, strict=False)
    unit.read(ROOT / "deploy/linux/trader-worker.service", encoding="utf-8")
    command = shlex.split(unit["Service"]["ExecStart"])
    interpreter = command.index("/opt/trader/app/.venv/bin/python")
    environment = _template_environment("monitor-worker.env.example")
    environment.update(dict(entry.split("=", 1) for entry in command[1:interpreter]))
    environment.update(TRADER_CREDENTIAL_FILE=str(credential_path),
                       TRADER_ACCOUNT_PROFILE_FILE=str(profile_path))
    selected = resolve_worker_expectations(uid=None, parent_uid=None, is_master=None,
        account_profile=None, profile_store=None, environ=environment)
    assert (selected._uid, selected._parent_uid, selected._is_master) == ("12345", "0", True)
    assert require_demo_credentials(resolver=CredentialResolver.from_environment(
        environ=environment)) == ("synthetic-monitor-key", "synthetic-monitor-secret")
    assert environment["LIVE_TRADING_ENABLED"] == "false"
    # The API's existing opt-in flag remains closed, even with the same saved DEMO pair.
    environment.update(_template_environment("runtime.env.example"))
    environment.update(TRADER_CREDENTIAL_FILE=str(credential_path),
                       TRADER_ACCOUNT_PROFILE_FILE=str(profile_path))
    with pytest.raises(MissingBybitCredentials, match="^BYBIT_DEMO is not enabled$"):
        require_demo_credentials(resolver=CredentialResolver.from_environment(environ=environment))


def test_executed_service_gates_override_incoming_credential_file_flags() -> None:
    unit = configparser.ConfigParser(interpolation=None, strict=False)
    unit.read(ROOT / "deploy/linux/trader.service", encoding="utf-8")
    command = shlex.split(unit["Service"]["ExecStart"])
    # Execute the actual env argument list with a harmless replacement interpreter payload.
    interpreter = command.index("/opt/trader/app/.venv/bin/python")
    enforced = command[1:interpreter]
    keys = ["LIVE_TRADING_ENABLED", "BYBIT_DEMO", "BYBIT_TESTNET",
            "BYBIT_ENV_FILE", "ANTHROPIC_ENV_FILE", "AI_ENV_FILE"]
    incoming = {**os.environ, **dict.fromkeys(keys, "true")}
    program = "import json,os; print(json.dumps({k:os.environ[k] for k in " + repr(keys) + "}))"
    if os.name == "posix":
        result = subprocess.run([command[0], *enforced, sys.executable, "-c", program],
                                capture_output=True, text=True, check=True, env=incoming, timeout=10)
    else:
        # GNU env is Linux-only; the full enforcement execution is required in Linux CI.
        pytest.skip("ExecStart /usr/bin/env enforcement requires Linux CI")
    assert json.loads(result.stdout) == {
        "LIVE_TRADING_ENABLED": "false", "BYBIT_DEMO": "false", "BYBIT_TESTNET": "false",
        "BYBIT_ENV_FILE": "off", "ANTHROPIC_ENV_FILE": "off", "AI_ENV_FILE": "off",
    }


def test_api_service_uses_only_permission_restricted_systemd_socket() -> None:
    from multi_exchange_trader.execution.operator_auth_v1 import _NGINX_SOCKET_PATH

    unit = configparser.ConfigParser(interpolation=None, strict=False)
    unit.read(ROOT / "deploy/linux/trader.service", encoding="utf-8")
    command = shlex.split(unit["Service"]["ExecStart"])
    interpreter = command.index("/opt/trader/app/.venv/bin/python")
    assert command[interpreter + 1:] == [
        "-m", "uvicorn", "multi_exchange_trader.api:app", "--fd", "3",
        "--workers", "1", "--no-proxy-headers",
    ]
    assert "--host" not in command and "--port" not in command

    socket_unit = configparser.ConfigParser(interpolation=None)
    socket_unit.read(ROOT / "deploy/linux/trader.socket", encoding="utf-8")
    assert socket_unit["Socket"]["ListenStream"] == "/run/trader-api.sock"
    assert _NGINX_SOCKET_PATH == socket_unit["Socket"]["ListenStream"]
    assert socket_unit["Socket"]["Accept"].lower() == "no"
    assert socket_unit["Socket"]["SocketUser"] == "www-data"
    assert socket_unit["Socket"]["SocketGroup"] == "www-data"
    assert socket_unit["Socket"]["SocketMode"] == "0600"
    assert socket_unit["Socket"]["RemoveOnStop"].lower() == "true"


@pytest.mark.skipif(sys.platform != "linux", reason="systemd parser requires Linux CI")
def test_systemd_unit_is_accepted_by_real_parser() -> None:
    executable = shutil.which("systemd-analyze")
    assert executable, "Linux operations CI must provide systemd-analyze"
    result = subprocess.run([executable, "verify", str(ROOT / "deploy/linux/trader.socket"),
                             str(ROOT / "deploy/linux/trader.service")],
                            capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode == 0, result.stderr


class EchoHeaders(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        payload = json.dumps({"host": self.headers.get("Host"),
                              "origin": self.headers.get("Origin"), "path": self.path,
                              "operator": self.headers.get("X-Trader-Operator"),
                              "authorization": self.headers.get("Authorization")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        pass


class ThreadingUnixHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_UNIX

    def server_bind(self) -> None:
        super().server_bind()
        os.chmod(self.server_address, 0o600)


@pytest.mark.skipif(sys.platform != "linux", reason="Real nginx requires Linux CI")
def test_real_nginx_authenticates_local_operator_and_preserves_origin(tmp_path: Path) -> None:
    executable = shutil.which("nginx")
    htpasswd = shutil.which("htpasswd")
    if not executable or not htpasswd:
        if os.environ.get("REQUIRE_LINUX_OPERATIONS") == "1":
            pytest.fail("Linux operations job must install nginx and apache2-utils")
        pytest.skip("nginx and htpasswd are required for the Linux auth boundary test")
    with closing(socket.socket()) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    backend_path = tmp_path / "backend.sock"
    backend = ThreadingUnixHTTPServer(str(backend_path), EchoHeaders)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    password_file = tmp_path / "dashboard.htpasswd"
    subprocess.run(
        [htpasswd, "-B", "-b", "-c", str(password_file), "trader-1", "synthetic-password"],
        capture_output=True, text=True, check=True, timeout=5,
    )
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist/index.html").write_text("local UI fixture", encoding="utf-8")
    template = (ROOT / "deploy/linux/nginx.conf").read_text(encoding="utf-8")
    block = template.replace("127.0.0.1:5173", f"127.0.0.1:{port}")
    block = block.replace("/run/trader-api.sock", str(backend_path))
    block = block.replace("/etc/nginx/trader.htpasswd", str(password_file))
    block = block.replace("/opt/trader/app/apps/web/dist", str(tmp_path / "dist"))
    config = tmp_path / "nginx.conf"
    temporary_paths = " ".join(f"{module}_temp_path {tmp_path}/{module};"
                               for module in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"))
    config.write_text(f"pid {tmp_path}/nginx.pid;\nerror_log {tmp_path}/error.log;\n"
                      f"events {{}}\nhttp {{ access_log off; {temporary_paths} {block} }}\n",
                      encoding="utf-8")
    args = [executable, "-p", str(tmp_path), "-c", str(config)]
    checked = subprocess.run([*args, "-t"], capture_output=True, text=True, check=False, timeout=10)
    assert checked.returncode == 0, checked.stderr
    process = subprocess.Popen([*args, "-g", "daemon off; master_process off;"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    opener = build_opener(ProxyHandler({}))
    authorization = "Basic " + b64encode(
        b"trader-1:synthetic-password"
    ).decode("ascii")
    try:
        deadline = time.monotonic() + 8
        while True:
            try:
                readiness = Request(
                    f"http://127.0.0.1:{port}/",
                    headers={"Authorization": authorization},
                )
                with opener.open(readiness, timeout=1) as response:
                    assert response.read() == b"local UI fixture"
                    break
            except (OSError, HTTPError):
                assert process.poll() is None, "nginx exited before accepting loopback requests"
                if time.monotonic() > deadline:
                    pytest.fail("nginx did not become ready")
                time.sleep(0.05)
        for host, origin in [(f"127.0.0.1:{port}", "http://127.0.0.1:5173"),
                             ("attacker.invalid", "https://attacker.invalid")]:
            unauthorized = Request(
                f"http://127.0.0.1:{port}/api/v1/probe",
                headers={"Host": host, "Origin": origin,
                         "X-Trader-Operator": "forged-local-user"},
            )
            with pytest.raises(HTTPError) as denied:
                opener.open(unauthorized, timeout=2)
            assert denied.value.code == 401
            request = Request(f"http://127.0.0.1:{port}/api/v1/probe",
                              headers={"Host": host, "Origin": origin,
                                       "X-Trader-Operator": "forged-local-user",
                                       "Authorization": authorization})
            with opener.open(request, timeout=2) as response:
                payload = json.load(response)
                assert payload["host"] == host
                assert payload["origin"] == origin
                assert payload["path"] == "/api/v1/probe"
                assert payload["operator"] == "trader-1"
                assert payload["authorization"] is None
        # Linux kernel socket table verifies the actual nginx listener, not config text.
        matching = []
        for line in Path("/proc/net/tcp").read_text().splitlines()[1:]:
            fields = line.split()
            address, local_port = fields[1].split(":")
            if int(local_port, 16) == port and fields[3] == "0A":
                matching.append(address)
        assert matching == ["0100007F"]
    finally:
        process.terminate()
        process.communicate(timeout=5)
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=3)
