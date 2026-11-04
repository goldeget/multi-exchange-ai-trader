"""Observed DEMO checkpoints and diagnostics, never risk or trading qualification.

A wallet read during a user-started observation is not a UTC midnight balance.
Saved values are historical data. Only the worker's original current Session,
lease and authenticated history receipts can collect a new observation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Any, Literal, cast

from .bybit_account_projections_v1 import project_bybit_usdt_wallet_v1
from .bybit_linear_model_inputs_v1 import BybitModelSourceRefV1, bybit_source_ref_sha256_v1
from .history import (
    HistorySnapshot,
    TransactionRecord,
    _symbol,
    _text,
    amount,
    milliseconds,
    validate_history_snapshot,
)
from .monitoring_models import ApplicationMonitoringSummary
from .protection import ProtectionScope

PROJECTION_KEY = "account-observation-v1"
_BLOCKERS = (
    "UTC_MIDNIGHT_BASELINE_REQUIRED",
    "VENUE_EVENT_COMPLETENESS_UNPROVEN",
    "OBSERVATION_BOUNDARY_UNPROVEN",
    "DRAWDOWN_HIGH_WATER_AND_FLOW_RULES_REQUIRED",
    "CONSECUTIVE_CLOSED_TRADE_SEQUENCE_REQUIRED",
    "ACCOUNT_EXPOSURE_RISK_UNRECONCILED",
    "ACCOUNT_WIDE_ASSET_COVERAGE_UNPROVEN",
)


class AccountReconciliationError(ValueError):
    """Fixed local diagnostics; no raw response or signed request is retained."""


def _primitive(value: Any) -> Any:
    if type(value) is Decimal:
        return str(value)
    if type(value) is datetime:
        return value.isoformat()
    if type(value) in (tuple, list):
        return [_primitive(item) for item in value]
    if type(value) is dict:
        return {key: _primitive(item) for key, item in value.items()}
    if value is None or type(value) in (str, int, bool):
        return value
    raise AccountReconciliationError("ACCOUNT_OBSERVATION_DATA_INVALID")


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class AccountObservationCheckpointV1:
    scope: ProtectionScope
    checkpoint_id: str
    symbol: str
    started_at: datetime
    wallet_observed_at: datetime
    wallet_request_started_at: datetime
    cash_balance: Decimal
    equity: Decimal
    source_hashes: tuple[str, ...]
    position_ids: tuple[str, ...]
    order_ids: tuple[str, ...]
    unresolved_command_keys: tuple[str, ...]
    application_digest: str
    ledger_qualified: Literal[False] = field(default=False, init=False)
    utc_day_coverage: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True)
class AccountReconciliationReportV1:
    checkpoint_id: str
    report_id: str
    observed_at: datetime
    start_ms: int
    through_ms: int
    source_hashes: tuple[str, ...]
    history_observation_keys: tuple[str, ...]
    cash_change: Decimal
    history_change: Decimal
    cash_difference: Decimal
    cash_chain_status: Literal["MATCHED_OBSERVED_VALUES", "UNRECONCILED"]
    position_ids: tuple[str, ...]
    order_ids: tuple[str, ...]
    unresolved_command_keys: tuple[str, ...]
    external_order_ids: tuple[str, ...]
    uncovered_history_symbols: tuple[str, ...]
    blockers: tuple[str, ...]
    daily_loss: None = field(default=None, init=False)
    drawdown: None = field(default=None, init=False)
    consecutive_losses: None = field(default=None, init=False)
    total_risk: None = field(default=None, init=False)
    ledger_qualified: Literal[False] = field(default=False, init=False)
    utc_day_coverage: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True)
class _AccountObservationV1:
    scope: ProtectionScope
    symbol: str
    wallet_source: BybitModelSourceRefV1
    observed_at: datetime
    cash_balance: Decimal
    equity: Decimal
    source_hashes: tuple[str, ...]
    position_ids: tuple[str, ...]
    order_ids: tuple[str, ...]
    external_order_ids: tuple[str, ...]
    unsupported_assets: tuple[str, ...]
    unresolved_command_keys: tuple[str, ...]
    application_digest: str
    application_conflicts: tuple[str, ...]


def _body(raw: bytes) -> dict[str, Any]:
    from ..account.bybit_readonly import _parse_json

    value = _parse_json(raw)
    if (
        type(value) is not dict
        or type(value.get("retCode")) is not int
        or value["retCode"] != 0
        or type(value.get("result")) is not dict
    ):
        raise AccountReconciliationError("ACCOUNT_OBSERVATION_DATA_INVALID")
    return cast("dict[str, Any]", value["result"])


def observe_account_sources_v1(
    *,
    scope: ProtectionScope,
    symbol: str,
    sources: tuple[BybitModelSourceRefV1, ...],
    wallet_source: BybitModelSourceRefV1,
    wallet_raw: bytes,
    position_raw: tuple[bytes, ...],
    order_raw: tuple[bytes, ...],
    application: ApplicationMonitoringSummary,
    now: datetime,
) -> _AccountObservationV1:
    """Project diagnostic identities and exact wallet values from current originals.

    Source shape/hash checks are data checks. Worker require calls supply authority.
    Position IDs describe venue rows, not owned or protected positions.
    """
    _symbol(symbol)
    if scope.environment != "DEMO" or now.tzinfo is not UTC or len(sources) < 7:
        raise AccountReconciliationError("ACCOUNT_OBSERVATION_DATA_INVALID")
    for source in sources:
        if source.account_scope != scope or source.source_environment != "DEMO":
            raise AccountReconciliationError("ACCOUNT_OBSERVATION_SCOPE_MISMATCH")
    wallet = project_bybit_usdt_wallet_v1(wallet_source, wallet_raw)
    accounts = _body(wallet_raw)["list"]
    other_assets = tuple(
        sorted(_text(row["coin"]) for row in accounts[0]["coin"] if row["coin"] != "USDT")
    )
    positions: list[str] = []
    orders: list[str] = []
    external: list[str] = []
    owned = {item.request.owned.client_order_id: item for item in application.owned}
    for raw in position_raw:
        for row in _body(raw)["list"]:
            name, index = _symbol(row.get("symbol")), row.get("positionIdx")
            if type(index) is not int or index not in (0, 1, 2):
                raise AccountReconciliationError("ACCOUNT_OBSERVATION_POSITION_INVALID")
            positions.append(f"{name}:{index}")
    for raw in order_raw:
        for row in _body(raw)["list"]:
            venue, client, name = (
                _text(row.get("orderId")),
                _text(row.get("orderLinkId"), empty=True),
                _symbol(row.get("symbol")),
            )
            orders.append(venue)
            own = owned.get(client)
            if (
                own is None
                or own.request.owned.instrument_id != name + "-LINEAR.BYBIT"
                or venue not in own.venue_order_ids
            ):
                external.append(venue)
    if len(set(positions)) != len(positions) or len(set(orders)) != len(orders):
        raise AccountReconciliationError("ACCOUNT_OBSERVATION_DUPLICATE_IDENTITY")
    unresolved = tuple(
        sorted(
            item.command_key
            for item in application.commands
            if not item.terminal_confirmed
            and (
                item.reservation is not None
                or item.reservation_v2 is not None
                or item.state.value in ("PENDING", "SENDING", "UNKNOWN")
            )
        )
    )
    return _AccountObservationV1(
        scope,
        symbol,
        wallet_source,
        now,
        wallet.wallet_balance.value,
        wallet.equity.value,
        tuple(bybit_source_ref_sha256_v1(source) for source in sources),
        tuple(sorted(positions)),
        tuple(sorted(orders)),
        tuple(sorted(external)),
        other_assets,
        unresolved,
        application.digest,
        application.conflicts,
    )


def checkpoint_payload_v1(value: AccountObservationCheckpointV1) -> dict[str, Any]:
    result: dict[str, Any] = _primitive(asdict(value))
    return result


def begin_account_checkpoint_v1(
    observation: _AccountObservationV1,
) -> AccountObservationCheckpointV1:
    source = observation.wallet_source
    value = AccountObservationCheckpointV1(
        observation.scope,
        "",
        observation.symbol,
        source.received_at,
        source.received_at,
        source.request_started_at,
        observation.cash_balance,
        observation.equity,
        observation.source_hashes,
        observation.position_ids,
        observation.order_ids,
        observation.unresolved_command_keys,
        observation.application_digest,
    )
    payload = checkpoint_payload_v1(value)
    payload["checkpoint_id"] = _digest(
        {key: child for key, child in payload.items() if key != "checkpoint_id"}
    )
    return decode_account_checkpoint_v1(payload, scope=observation.scope)


def decode_account_checkpoint_v1(
    value: object, *, scope: ProtectionScope
) -> AccountObservationCheckpointV1:
    """Validate saved data, without restoring live receipt or baseline authority."""
    try:
        if type(value) is not dict:
            raise ValueError
        fields = {
            "scope",
            "checkpoint_id",
            "symbol",
            "started_at",
            "wallet_observed_at",
            "wallet_request_started_at",
            "cash_balance",
            "equity",
            "source_hashes",
            "position_ids",
            "order_ids",
            "unresolved_command_keys",
            "application_digest",
            "ledger_qualified",
            "utc_day_coverage",
        }
        if set(value) != fields or value["scope"] != asdict(scope):
            raise ValueError
        if (
            value["ledger_qualified"] is not False
            or value["utc_day_coverage"] is not False
            or value["checkpoint_id"]
            != _digest({key: child for key, child in value.items() if key != "checkpoint_id"})
        ):
            raise ValueError
        strings = (
            "checkpoint_id",
            "symbol",
            "started_at",
            "wallet_observed_at",
            "wallet_request_started_at",
            "application_digest",
        )
        if any(type(value[key]) is not str for key in strings):
            raise ValueError
        for key in ("source_hashes", "position_ids", "order_ids", "unresolved_command_keys"):
            if (
                type(value[key]) is not list
                or len(value[key]) > 4096
                or any(type(child) is not str for child in value[key])
            ):
                raise ValueError
        _symbol(value["symbol"])
        times = [
            datetime.fromisoformat(value[key])
            for key in ("started_at", "wallet_observed_at", "wallet_request_started_at")
        ]
        if (
            any(stamp.tzinfo is not UTC for stamp in times)
            or times[0] != times[1]
            or times[2] > times[1]
            or len(value["source_hashes"]) < 7
        ):
            raise ValueError
        return AccountObservationCheckpointV1(
            scope,
            value["checkpoint_id"],
            value["symbol"],
            times[0],
            times[1],
            times[2],
            amount(value["cash_balance"]),
            amount(value["equity"]),
            tuple(value["source_hashes"]),
            tuple(value["position_ids"]),
            tuple(value["order_ids"]),
            tuple(value["unresolved_command_keys"]),
            value["application_digest"],
        )
    except Exception:  # noqa: BLE001, S110 - discard stored context before the fixed public code.
        pass
    raise AccountReconciliationError("ACCOUNT_OBSERVATION_CHECKPOINT_INVALID") from None


def reconcile_account_observation_v1(
    checkpoint: AccountObservationCheckpointV1,
    current: _AccountObservationV1,
    *,
    histories: tuple[HistorySnapshot, ...],
    observation_keys: tuple[str, ...],
    conflict_keys: tuple[str, ...],
    start_ms: int,
    through_ms: int,
) -> AccountReconciliationReportV1:
    """Compare observed cash with complete-query records; never attest event absence."""
    if checkpoint.scope != current.scope or checkpoint.symbol != current.symbol:
        raise AccountReconciliationError("ACCOUNT_OBSERVATION_SCOPE_MISMATCH")
    blockers = set(_BLOCKERS)
    transactions: dict[str, TransactionRecord] = {}
    symbols = set()
    for history in histories:
        validate_history_snapshot(history)
        if (
            history.scope != current.scope
            or history.transport_injected
            or (history.window.start_ms, history.window.end_ms) != (start_ms, through_ms)
        ):
            raise AccountReconciliationError("ACCOUNT_OBSERVATION_HISTORY_INVALID")
        symbols.add(history.symbol)
        for row in history.transactions:
            if row.transaction_id in transactions and transactions[row.transaction_id] != row:
                blockers.add("HISTORY_CONFLICTS_PRESENT")
            transactions[row.transaction_id] = row
    if not histories or len(histories) != len(observation_keys):
        raise AccountReconciliationError("ACCOUNT_OBSERVATION_HISTORY_REQUIRED")
    uncovered = tuple(sorted({row.symbol for row in transactions.values() if row.symbol} - symbols))
    if uncovered:
        blockers.add("HISTORY_SYMBOL_COVERAGE_INCOMPLETE")
    if conflict_keys:
        blockers.add("HISTORY_CONFLICTS_PRESENT")
    if current.unsupported_assets:
        blockers.add("NON_USDT_ASSETS_OR_LIABILITIES_PRESENT")
    if current.external_order_ids:
        blockers.add("EXTERNAL_ORDER_OWNERSHIP_UNRECONCILED")
    if current.position_ids:
        blockers.add("HELD_POSITION_OWNERSHIP_AND_PROTECTION_UNRECONCILED")
    if current.unresolved_command_keys:
        blockers.add("UNRESOLVED_LOCAL_COMMANDS")
    if current.application_conflicts:
        blockers.add("APPLICATION_CONFLICTS_PRESENT")
    with localcontext() as context:
        context.prec = 100
        history_change = sum((row.change for row in transactions.values()), Decimal(0))
        cash_change = current.cash_balance - checkpoint.cash_balance
        difference = cash_change - history_change
        balance = checkpoint.cash_balance
        pending = sorted(transactions.values(), key=lambda row: row.time_ms)
        chain = True
        while pending and chain:
            instant = pending[0].time_ms
            group = [row for row in pending if row.time_ms == instant]
            pending = [row for row in pending if row.time_ms != instant]
            while group:
                matches = [row for row in group if balance + row.change == row.cash_balance]
                if len(matches) != 1:
                    chain = False
                    break
                row = matches[0]
                balance = row.cash_balance
                group.remove(row)
        if not chain or balance != current.cash_balance or difference:
            blockers.add("CASH_CHAIN_UNRECONCILED")
        if any(
            row.time_ms
            <= milliseconds(
                checkpoint.wallet_observed_at.replace(
                    microsecond=checkpoint.wallet_observed_at.microsecond // 1000 * 1000
                )
            )
            for row in transactions.values()
        ):
            blockers.add("CHECKPOINT_BOUNDARY_ACTIVITY_PRESENT")
    report = AccountReconciliationReportV1(
        checkpoint.checkpoint_id,
        "",
        current.observed_at,
        start_ms,
        through_ms,
        current.source_hashes,
        observation_keys,
        cash_change,
        history_change,
        difference,
        "MATCHED_OBSERVED_VALUES" if "CASH_CHAIN_UNRECONCILED" not in blockers else "UNRECONCILED",
        current.position_ids,
        current.order_ids,
        current.unresolved_command_keys,
        current.external_order_ids,
        uncovered,
        tuple(sorted(blockers)),
    )
    from dataclasses import replace

    return replace(report, report_id=_digest(_primitive(asdict(report))))


def report_payload_v1(value: AccountReconciliationReportV1) -> dict[str, Any]:
    result: dict[str, Any] = _primitive(asdict(value))
    return result
