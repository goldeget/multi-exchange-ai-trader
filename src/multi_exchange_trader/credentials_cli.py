"""Explicit private one-time import. Never prints source arguments or credential values."""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from .credentials import (
    CredentialStore,
    CredentialStoreError,
    default_credential_store,
    import_env,
)


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise CredentialStoreError("INVALID_ARGUMENTS")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _SafeParser(description="Register credentials once from an explicit local file.")
    parser.add_argument("--import-env", required=True, metavar="ABSOLUTE_PATH")
    parser.add_argument("--store", metavar="ABSOLUTE_PATH")
    parser.add_argument("--expected-revision", type=int)
    try:
        options = parser.parse_args(argv)
        store = (CredentialStore(Path(options.store)) if options.store else
                 default_credential_store(os.environ))
        if store is None:
            raise CredentialStoreError("INVALID_ARGUMENTS")
        result = import_env(Path(options.import_env), store,
                            expected_revision=options.expected_revision)
        print(json.dumps({"schema": result.schema, "revision": result.revision,
                          "namespaces": result.namespaces}, sort_keys=True))
        return 0
    except CredentialStoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
