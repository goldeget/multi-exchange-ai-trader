"""Internal stdio-only simulator worker; never an HTTP/live client."""

import json
import sys
from typing import Any

from .sandbox import deny_network


def no_floats(value: Any) -> None:
    if type(value) is float:
        raise ValueError("float in PAPER worker ledger")
    if isinstance(value, dict):
        for item in value.values():
            no_floats(item)
    if isinstance(value, list):
        for item in value:
            no_floats(item)


def main() -> int:
    try:
        deny_network()
        from .native import replay

        raw = sys.stdin.buffer.read(2100001)
        if len(raw) > 2100000:
            raise ValueError("PAPER worker input limit")
        events = json.loads(raw)
        if not isinstance(events, list) or len(events) > 500:
            raise ValueError("PAPER worker event limit")
        no_floats(events)
        state = replay(events)
        no_floats(state)
        sys.stdout.write(json.dumps({"state": state}, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, RuntimeError, ArithmeticError, OSError) as exc:
        sys.stdout.write(json.dumps({"error": str(exc)[:240]}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    raise SystemExit(main())
