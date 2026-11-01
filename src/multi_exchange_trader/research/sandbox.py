"""Research-only native process; same unchanged proxy/CA, no socket capability."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def sandbox_evaluate(
    dataset: dict[str, Any], config: dict[str, Any] | None = None
) -> dict[str, Any]:
    env = {
        "PATH": os.defpath,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "LANG": "C.UTF-8",
        "OPENBLAS_NUM_THREADS": "1",
    }
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
    ):
        if name in os.environ:
            env[name] = os.environ[name]
    try:
        result = subprocess.run(
            [sys.executable, "-m", "multi_exchange_trader.research.worker"],
            input=json.dumps(dataset if config is None else {"dataset": dataset, "config": config}, allow_nan=False),
            text=True,
            capture_output=True,
            close_fds=True,
            env=env,
            timeout=60,
            check=False,
        )
        response = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise ValueError("research native worker unavailable") from None
    if (
        result.returncode
        or not isinstance(response, dict)
        or not isinstance(response.get("report"), dict)
    ):
        raise ValueError("research native worker failed closed")
    return dict(response["report"])
