"""The same fail-closed subprocess/network boundary, dedicated to the spot CASH worker."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def replay(events: list[dict[str, Any]]) -> dict[str, Any]:
    env = {"PATH": os.defpath, "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
           "LANG": "C.UTF-8", "OPENBLAS_NUM_THREADS": "1"}
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE"):
        if name in os.environ:
            env[name] = os.environ[name]
    try:
        result = subprocess.run([sys.executable, "-m", "multi_exchange_trader.paper.spot_worker"],
                                input=json.dumps(events, allow_nan=False), capture_output=True,
                                text=True, timeout=30, env=env, close_fds=True, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("SPOT native worker unavailable") from None
    try:
        response = json.loads(result.stdout)
    except ValueError:
        raise ValueError("SPOT native worker failed closed") from None
    if result.returncode or not isinstance(response, dict) or "state" not in response:
        message = response.get("error", "SPOT native worker rejected") if isinstance(response, dict) else "SPOT native worker rejected"
        raise ValueError(str(message)[:240])
    state = response["state"]
    if not isinstance(state, dict):
        raise TypeError("SPOT worker protocol")
    return state
