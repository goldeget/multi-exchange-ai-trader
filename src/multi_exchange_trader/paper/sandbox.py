"""Fail-closed Linux worker boundary: no credentials, inherited sockets, or new network."""

import ctypes
import errno
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


def deny_network() -> None:
    """Unprivileged seccomp: block socket/connect, reject non-x86_64/x32 ABI."""
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise ValueError("PAPER network sandbox currently qualified only on Linux x86_64")

    class Filter(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort),
            ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte),
            ("k", ctypes.c_uint),
        ]

    class Program(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

    reject = 0x00050000 | errno.EPERM
    instructions = [
        (0x20, 0, 0, 4),
        (0x15, 1, 0, 0xC000003E),
        (0x06, 0, 0, reject),
        (0x20, 0, 0, 0),
        (0x35, 0, 1, 0x40000000),
        (0x06, 0, 0, reject),
        (0x15, 1, 0, 41),
        (0x15, 0, 1, 42),
        (0x06, 0, 0, reject),
        (0x06, 0, 0, 0x7FFF0000),
    ]
    filters = (Filter * len(instructions))(*(Filter(*row) for row in instructions))
    program = Program(len(instructions), filters)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0:
        raise ValueError("PAPER OS network sandbox unavailable; no fallback")


def sandbox_replay(events: list[dict[str, Any]]) -> dict[str, Any]:
    env = {
        "PATH": os.defpath,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "LANG": "C.UTF-8",
        "OPENBLAS_NUM_THREADS": "1",
    }
    # Preserve runtime trust/proxy settings unchanged, but grant no socket capability.
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
            [sys.executable, "-m", "multi_exchange_trader.paper.worker"],
            input=json.dumps(events, allow_nan=False),
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            close_fds=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("PAPER native worker unavailable") from None
    try:
        response = json.loads(result.stdout)
    except ValueError:
        raise ValueError("PAPER native worker failed closed") from None
    if result.returncode or not isinstance(response, dict) or "state" not in response:
        message = (
            response.get("error", "PAPER native worker rejected")
            if isinstance(response, dict)
            else "PAPER native worker rejected"
        )
        raise ValueError(str(message)[:240])
    state = response["state"]
    if not isinstance(state, dict):
        raise TypeError("PAPER worker protocol")
    return state
