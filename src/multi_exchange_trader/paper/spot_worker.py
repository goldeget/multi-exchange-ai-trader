"""Credential-free, seccomp-isolated CASH native replay worker."""

import json
import sys

from .sandbox import deny_network
from .worker import no_floats


def main() -> None:
    try:
        deny_network()
        from .spot_native import replay

        raw = sys.stdin.buffer.read(2_100_001)
        if len(raw) > 2_100_000:
            raise ValueError("SPOT worker input limit")
        events = json.loads(raw)
        if not isinstance(events, list) or len(events) > 499:
            raise ValueError("SPOT bounded command list required")
        no_floats(events)
        state = replay(events)
        no_floats(state)
        print(json.dumps({"state": state}, allow_nan=False))
    except (ValueError, TypeError, KeyError, RuntimeError, ArithmeticError, OSError) as exc:
        print(json.dumps({"error": str(exc)[:240]}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
