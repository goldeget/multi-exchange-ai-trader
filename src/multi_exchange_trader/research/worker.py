"""Bounded stdio-only research worker; deny sockets before native imports."""

import json
import sys

from ..paper.sandbox import deny_network
from ..paper.worker import no_floats


def main() -> int:
    try:
        deny_network()
        from .evaluation import evaluate

        raw = sys.stdin.buffer.read(2_100_001)
        if len(raw) > 2_100_000:
            raise ValueError("research worker input limit")
        dataset = json.loads(raw)
        no_floats(dataset)
        if isinstance(dataset, dict) and set(dataset) == {"dataset", "config"}:
            if not isinstance(dataset["config"], dict):
                raise ValueError("research evaluation config")
            result = evaluate(dataset["dataset"], dataset["config"])
        else:
            result = evaluate(dataset)
        from .data import checksum

        result["network_sandbox"] = "Linux_x86_64_socket_denied"
        result["reproducibility_hash"] = checksum(
            {k: v for k, v in result.items() if k != "reproducibility_hash"}
        )
        no_floats(result)
        sys.stdout.write(json.dumps({"report": result}, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, RuntimeError, ArithmeticError, OSError):
        sys.stdout.write(json.dumps({"error": "research native worker failed closed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
