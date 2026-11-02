"""Private profile commands; only named enrollment can resolve credentials/use GETs."""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import NoReturn

from .account_enrollment import ADOPTION_POLICY, BybitDemoEnrollmentSession
from .account_profiles import (
    AccountProfileError,
    AccountProfileResult,
    AccountProfileStore,
    default_account_profile_store,
)


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise AccountProfileError("INVALID_ARGUMENTS")


async def _enroll(store: AccountProfileStore, *, policy: str) -> AccountProfileResult:
    owner = BybitDemoEnrollmentSession.from_environment(store=store, environ=os.environ)
    try:
        candidate = await owner.observe()
        return owner.adopt(candidate, policy=policy, adopt_observed_relationship=True)
    finally:
        await owner.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _SafeParser(description="Selected DEMO profile configuration; no activation.")
    parser.add_argument("--profile-store", metavar="ABSOLUTE_PATH")
    commands = parser.add_subparsers(dest="command")
    registration = commands.add_parser("register-uid")
    registration.add_argument("--uid", required=True, metavar="UID")
    registration.add_argument("--expected-revision", type=int)
    commands.add_parser("status")
    enrollment = commands.add_parser("enroll-observed")
    enrollment.add_argument("--policy", required=True, choices=(ADOPTION_POLICY,))
    enrollment.add_argument("--adopt-observed-relationship", action="store_true", required=True)
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
        elif options.command == "enroll-observed":
            result = asyncio.run(_enroll(store, policy=options.policy))
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
    except Exception:  # noqa: BLE001 - never disclose credential/provider/path diagnostics.
        print("ENROLLMENT_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
