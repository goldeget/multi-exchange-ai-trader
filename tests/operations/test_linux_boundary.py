"""Run deployment boundaries without installing services or contacting providers."""

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
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_executed_service_gates_override_incoming_credential_file_flags() -> None:
    unit = configparser.ConfigParser(interpolation=None, strict=False)
    unit.read(ROOT / "deploy/linux/trader.service", encoding="utf-8")
    command = shlex.split(unit["Service"]["ExecStart"])
    # Execute the actual env argument list with a harmless replacement interpreter payload.
    interpreter = command.index("/opt/trader/app/.venv/bin/python")
    enforced = command[1:interpreter]
    keys = ["LIVE_TRADING_ENABLED", "BYBIT_DEMO", "BYBIT_TESTNET",
            "BYBIT_ENV_FILE", "ANTHROPIC_ENV_FILE"]
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
        "BYBIT_ENV_FILE": "off", "ANTHROPIC_ENV_FILE": "off",
    }


@pytest.mark.skipif(sys.platform != "linux", reason="systemd parser requires Linux CI")
def test_systemd_unit_is_accepted_by_real_parser() -> None:
    executable = shutil.which("systemd-analyze")
    assert executable, "Linux operations CI must provide systemd-analyze"
    result = subprocess.run([executable, "verify", str(ROOT / "deploy/linux/trader.service")],
                            capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode == 0, result.stderr


class EchoHeaders(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        payload = json.dumps({"host": self.headers.get("Host"),
                              "origin": self.headers.get("Origin"), "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.mark.skipif(sys.platform != "linux", reason="Real nginx requires Linux CI")
def test_real_nginx_preserves_untrusted_host_and_origin_on_loopback(tmp_path: Path) -> None:
    executable = shutil.which("nginx")
    if not executable:
        if os.environ.get("REQUIRE_LINUX_OPERATIONS") == "1":
            pytest.fail("Linux operations job must install nginx")
        pytest.skip("nginx absent; dedicated Linux operations CI requires it")
    with closing(socket.socket()) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    backend = ThreadingHTTPServer(("127.0.0.1", 0), EchoHeaders)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist/index.html").write_text("local UI fixture", encoding="utf-8")
    template = (ROOT / "deploy/linux/nginx.conf").read_text(encoding="utf-8")
    block = template.replace("127.0.0.1:5173", f"127.0.0.1:{port}")
    block = block.replace("127.0.0.1:8000", f"127.0.0.1:{backend.server_port}")
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
    try:
        deadline = time.monotonic() + 8
        while True:
            try:
                with opener.open(f"http://127.0.0.1:{port}/", timeout=1) as response:
                    assert response.read() == b"local UI fixture"
                    break
            except (OSError, HTTPError):
                assert process.poll() is None, "nginx exited before accepting loopback requests"
                if time.monotonic() > deadline:
                    pytest.fail("nginx did not become ready")
                time.sleep(0.05)
        for host, origin in [(f"127.0.0.1:{port}", "http://127.0.0.1:5173"),
                             ("attacker.invalid", "https://attacker.invalid")]:
            request = Request(f"http://127.0.0.1:{port}/api/v1/probe",
                              headers={"Host": host, "Origin": origin})
            with opener.open(request, timeout=2) as response:
                assert json.load(response) == {"host": host, "origin": origin,
                                               "path": "/api/v1/probe"}
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
