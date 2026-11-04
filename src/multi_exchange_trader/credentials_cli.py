"""Explicit private one-time import. Never prints source arguments or credential values."""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn, cast

from .credentials import (
    _ALLOWLIST,
    _MAX_PLAINTEXT,
    CredentialStore,
    CredentialStoreError,
    _pairs,
    default_credential_store,
    import_env,
)


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise CredentialStoreError("INVALID_ARGUMENTS")


def _stdin_values() -> dict[str, str]:
    """Read only an explicitly requested, bounded UTF-8 JSON name/string map."""
    try:
        raw = sys.stdin.buffer.read(_MAX_PLAINTEXT + 1)
        if type(raw) is not bytes or len(raw) > _MAX_PLAINTEXT:
            raise ValueError
        values = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        if type(values) is not dict or any(
            type(name) is not str or name not in _ALLOWLIST or type(value) is not str
            for name, value in values.items()
        ):
            raise ValueError
        return cast(dict[str, str], values)
    except Exception:  # noqa: BLE001 - stdin/decoder details must never disclose input.
        raise CredentialStoreError("INVALID_IMPORT") from None


def main(argv: Sequence[str] | None = None) -> int:
    parser = _SafeParser(description="Register credentials once from an explicit import source.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--import-env", metavar="ABSOLUTE_PATH")
    source.add_argument("--import-stdin", action="store_true")
    parser.add_argument("--store", metavar="ABSOLUTE_PATH")
    parser.add_argument("--expected-revision", type=int)
    try:
        options = parser.parse_args(argv)
        store = (
            CredentialStore(Path(options.store))
            if options.store is not None
            else default_credential_store(os.environ)
        )
        if store is None:
            raise CredentialStoreError("INVALID_ARGUMENTS")
        if options.import_stdin:
            result = store.register(_stdin_values(), expected_revision=options.expected_revision)
        else:
            result = import_env(
                Path(options.import_env), store, expected_revision=options.expected_revision
            )
        print(
            json.dumps(
                {
                    "schema": result.schema,
                    "revision": result.revision,
                    "namespaces": result.namespaces,
                },
                sort_keys=True,
            )
        )
        return 0
    except CredentialStoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
