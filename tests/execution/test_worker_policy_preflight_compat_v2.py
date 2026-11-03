"""The configured loader stays genuine when the passive constructor is replaced."""
import argparse

import pytest
from test_policy_shared_target_v2 import SharedFixture
from test_policy_shared_target_v2 import shared_target as _shared_target

from multi_exchange_trader.execution import worker_cli

shared_target = _shared_target


@pytest.mark.parametrize("explicit_path", [False, True])
async def test_shared_preflight_precedes_profile_with_passive_constructor_factory(
    shared_target: SharedFixture, monkeypatch: pytest.MonkeyPatch, explicit_path: bool,
) -> None:
    class ProfileBoundary(RuntimeError):
        pass

    def profile(**_: object) -> None:
        raise ProfileBoundary("test stops before credentials")

    def passive_constructor(*_: object) -> object:
        pytest.fail("configured source must use its genuine loader")

    monkeypatch.setattr(worker_cli, "RuntimePolicyBook", passive_constructor)
    monkeypatch.setattr(worker_cli, "resolve_worker_expectations", profile)
    args = argparse.Namespace(uid=None, parent_uid=None, is_master=None)
    if explicit_path:
        args.policy_db = str(shared_target.db.with_name("wrong.sqlite3"))
        with pytest.raises(ValueError):
            await worker_cli._run(args)
        args.policy_db = str(shared_target.db)
    with pytest.raises(ProfileBoundary):
        await worker_cli._run(args)
    assert not shared_target.metadata.descriptors
