"""Private immutable worker expectation selection, never authentication."""

from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from .account_profiles import (
    AccountExpectations,
    AccountProfileError,
    AccountProfileStore,
    default_account_profile_store,
)

_MONITOR_POLICIES = frozenset({"independent-v1", "uid-anchored-adoption-v1"})


def resolve_worker_expectations(*, uid: str | None, parent_uid: str | None,
                                is_master: str | None, account_profile: str | None,
                                profile_store: str | None,
                                environ: Mapping[str, str]) -> AccountExpectations:
    """Complete legacy tuple bypasses all profile I/O, including location options.

    A location alone is not an identity override. Legacy fields retain their
    low-level authenticate validation; no claim of independent source verification
    is inferred from command-line strings. Saved values carry parsed provenance.
    """
    count = sum(value is not None for value in (uid, parent_uid, is_master))
    if count:
        if count != 3 or account_profile is not None or is_master not in {"true", "false"}:
            raise AccountProfileError("INVALID_ARGUMENTS")
        assert uid is not None and parent_uid is not None
        return AccountExpectations("", 0, "EXPLICIT_LEGACY_EXPECTATIONS", "LEGACY_CLI",
                                   "legacy-explicit-v1", uid, parent_uid, is_master == "true")
    if account_profile is not None:
        try:
            parsed = UUID(account_profile)
            if parsed.version != 4 or str(parsed) != account_profile:
                raise ValueError
        except ValueError:
            raise AccountProfileError("INVALID_ARGUMENTS") from None
    store = (AccountProfileStore(Path(profile_store)) if profile_store is not None
             else default_account_profile_store(environ))
    if store is None:
        raise AccountProfileError("INVALID_ARGUMENTS")
    snapshot = store.load()
    if snapshot is None or (account_profile is not None and snapshot.profile_id != account_profile):
        raise AccountProfileError("INVALID_ARGUMENTS")
    return snapshot.resolve_expectations(supported_policies=_MONITOR_POLICIES)
