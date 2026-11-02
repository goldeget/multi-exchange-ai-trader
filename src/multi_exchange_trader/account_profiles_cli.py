"""Explicit private pending registration/status, with no credentials or HTTP."""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import NoReturn

from .account_profiles import (
    AccountProfileError,
    AccountProfileStore,
    default_account_profile_store,
)


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise AccountProfileError("INVALID_ARGUMENTS")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _SafeParser(description="Selected DEMO profile configuration; no activation.")
    parser.add_argument("--profile-store", metavar="ABSOLUTE_PATH")
    commands = parser.add_subparsers(dest="command")
    registration = commands.add_parser("register-uid")
    registration.add_argument("--uid", required=True, metavar="UID")
    registration.add_argument("--expected-revision", type=int)
    commands.add_parser("status")
    try:
        options = parser.parse_args(argv)
        if options.command is None:
            if options.profile_store is not None:
                raise AccountProfileError("INVALID_ARGUMENTS")
            print("DRY_CHECK")
            return 0
        store = (AccountProfileStore(Path(options.profile_store))
                 if options.profile_store is not None else default_account_profile_store(os.environ))
        if store is None:
            raise AccountProfileError("INVALID_ARGUMENTS")
        if options.command == "register-uid":
            result = store.register_uid(options.uid, expected_revision=options.expected_revision)
        else:
            snapshot = store.load()
            if snapshot is None:
                print(json.dumps({"state": "NO_PROFILE"}))
                return 0
            result = snapshot.safe_result()
        print(json.dumps(asdict(result), sort_keys=True))
        return 0
    except AccountProfileError as error:
        print(error.code, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
