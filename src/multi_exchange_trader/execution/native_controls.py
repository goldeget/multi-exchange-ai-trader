"""Pinned native account controls and independent setting observations.

Source: nautilus_trader 1.231.0, commit
27a8e54e7ac3c57d6cbf8891f0283dfbaee97317. The public Python
BybitExecutionClient.set_leverage accepts int. For fractional venue grids this
narrow extension calls the SAME execution client's Rust BybitHttpClient method,
whose pinned PyO3 signature takes buy/sell String. It creates no second client,
reads no credentials and performs no rounding. Runtime owns the durable claim,
lease, explicit user policy and current risk checks before calling this boundary.
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from .bybit_native import _require_native_version
from .native_events import bounded_decimal
from .protection import ProtectionScope
from .venue_readback import VenueSnapshot


class NativeLeverageControl:
    """Bind the already existing DEMO execution client, never construct one.

    Native account ID is local adapter identity, not authenticated venue UID.
    The production runtime's qualification gate remains mandatory.
    """

    def __init__(self, *, client: object, scope: ProtectionScope,
                 native_account_id: str) -> None:
        _require_native_version()
        adapter = importlib.import_module("nautilus_trader.adapters.bybit.execution")
        native = importlib.import_module("nautilus_trader.core.nautilus_pyo3")
        if (type(scope) is not ProtectionScope or scope.environment != "DEMO"
                or type(client) is not adapter.BybitExecutionClient
                or getattr(client, "_is_demo", None) is not True
                or str(getattr(client, "pyo3_account_id", None)) != native_account_id
                or type(getattr(client, "_http_client", None)) is not native.BybitHttpClient):
            raise ValueError("PINNED_EXISTING_DEMO_EXECUTION_CLIENT_REQUIRED")
        self.scope, self.native_account_id = scope, native_account_id
        self._client: Any = client
        self._http_client: Any = client._http_client  # type: ignore[attr-defined]
        self._linear = native.BybitProductType.LINEAR

    async def apply(self, symbol: str, leverage: Decimal) -> None:
        if re.fullmatch(r"[A-Z0-9]{2,48}", symbol) is None:
            raise ValueError("NATIVE_LEVERAGE_SYMBOL_REQUIRED")
        bounded_decimal(leverage)
        # A replaced transport invalidates the single-writer binding.
        if self._client._http_client is not self._http_client:
            raise ValueError("NATIVE_EXECUTION_CLIENT_CHANGED")
        if leverage == leverage.to_integral_value():
            await self._client.set_leverage(symbol=symbol + "-LINEAR", leverage=int(leverage))
        else:
            text = format(leverage, "f")
            await self._http_client.set_leverage(product_type=self._linear, symbol=symbol,
                                                buy_leverage=text, sell_leverage=text)


@dataclass(frozen=True)
class LeverageObservation:
    """A current matched GET setting; it does not qualify identity or entry risk."""

    command_key: str
    entry_key: str
    scope: ProtectionScope
    symbol: str
    leverage: Decimal
    policy_revision: str
    cycle_id: str
    observed_at: datetime
    qualified: Literal[False] = False


def require_flat_snapshot(snapshot: VenueSnapshot, *, scope: ProtectionScope, symbol: str,
                          now: datetime, max_age: timedelta,
                          after: datetime | None = None) -> None:
    """Require explicit zero-size one-way row and no orders, never infer empty=flat.

    This is a conservative setting-mutation precondition, not execution-history
    or authenticated-account flat proof. Runtime separately rejects unresolved
    owned fills/commands, and retains every reservation.
    """
    if (type(snapshot) is not VenueSnapshot or snapshot.scope != scope
            or snapshot.symbol != symbol or not snapshot.complete or snapshot.blockers
            or len(snapshot.positions) != 1 or snapshot.orders
            or snapshot.server_observed_at is None
            or not timedelta(0) <= now - snapshot.observed_at <= max_age
            or not timedelta(0) <= now - snapshot.server_observed_at <= max_age
            or (after is not None and snapshot.server_observed_at < after)):
        raise ValueError("CURRENT_EXPLICIT_FLAT_READBACK_REQUIRED")
    row = snapshot.positions[0]
    if row.symbol != symbol or row.position_idx != 0 or row.quantity != 0:
        raise ValueError("EXTERNAL_OR_OWNED_EXPOSURE_FORBIDS_LEVERAGE_CHANGE")


def observe_leverage(snapshot: VenueSnapshot, *, command_key: str, entry_key: str,
                     scope: ProtectionScope, symbol: str, leverage: Decimal,
                     policy_revision: str, now: datetime, max_age: timedelta,
                     after: datetime) -> LeverageObservation:
    require_flat_snapshot(snapshot, scope=scope, symbol=symbol, now=now,
                          max_age=max_age, after=after)
    if snapshot.positions[0].leverage != leverage:
        raise ValueError("INDEPENDENT_LEVERAGE_READBACK_MISMATCH")
    assert snapshot.server_observed_at is not None
    return LeverageObservation(command_key, entry_key, scope, symbol, leverage,
                               policy_revision, snapshot.cycle_id, snapshot.server_observed_at)
