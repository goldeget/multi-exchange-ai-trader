"""Synthetic private-core fixtures; actual authenticated transport stays unverified.

Every owned client fails closed on unconfigured reads. No public receipt issuer,
validator replacement, provider request or native effect is used by this helper.
"""

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from multi_exchange_trader.execution.account_binding import (
    AuthenticatedAccountBinding,
    BybitAccountIdentity,
    BybitDemoAccountSession,
)
from multi_exchange_trader.execution.protection import ProtectionScope

SESSIONS: list[BybitDemoAccountSession] = []


@dataclass
class SyntheticSession:
    session: BybitDemoAccountSession
    binding: AuthenticatedAccountBinding
    wall: list[datetime]
    mono: list[float]
    rows: list[dict[str, Any]] = field(default_factory=list)
    configured: bool = False
    block: bool = False
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    reads: list[str] = field(default_factory=list)

    def permit_fake_monitoring(self) -> None:
        """Private fixture grant solely for existing fake native-node tests."""
        identity = self.session._identity
        assert identity is not None
        self.session._identity = replace(identity, read_only=False,
            permissions=(("ContractTrade", ("Order", "Position")), ("Wallet", ())))


def synthetic_session(scope: ProtectionScope, now: datetime, *,
                      transport: Any = None) -> SyntheticSession:
    wall, mono = [now], [10.0]
    owner = BybitDemoAccountSession(api_key="synthetic-worker-key",
        api_secret="synthetic-worker-secret", clock=lambda: wall[0], monotonic=lambda: mono[0],
        max_age=timedelta(seconds=60), transport=transport)
    SESSIONS.append(owner)
    binding = object.__new__(AuthenticatedAccountBinding)
    owner._binding = binding
    owner._identity = BybitAccountIdentity(scope.account_id, "0", False, True,
        (("Wallet", ()),), now, now, transport is not None)
    owner._issued_mono = mono[0]
    core = SyntheticSession(owner, binding, wall, mono)

    async def read(path: str, params: Any, *, signed: bool, deadline: float) -> Any:
        core.reads.append(path)
        if not core.configured:
            raise AssertionError("UNCONFIGURED_SYNTHETIC_READ")
        if core.block:
            core.entered.set()
            await core.release.wait()
        paths = {"/v5/execution/list", "/v5/order/history", "/v5/account/transaction-log"}
        if path not in paths:
            raise AssertionError("UNCONFIGURED_SYNTHETIC_PATH")
        rows = list(core.rows) if path == "/v5/execution/list" else []
        return {"category": "linear", "list": rows, "nextPageCursor": ""}, int(
            core.wall[0].timestamp() * 1000)

    owner._client.read = read  # type: ignore[method-assign]
    return core
