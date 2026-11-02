"""Pure exact data comparison; never releases, credits or synthesizes native events."""

from __future__ import annotations

import hashlib
from decimal import Decimal, localcontext
from typing import TYPE_CHECKING, Any

from .monitoring_models import (
    MonitoringAssessment,
    MonitoringError,
    MonitoringExecution,
    MonitoringObservation,
    MonitoringOrder,
    MonitoringPosition,
    MonitoringStatus,
    MonitoringTarget,
    MonitoringWitness,
    NativeMonitoringSummary,
    StreamStamp,
)

if TYPE_CHECKING:
    from .native_node import NativeNodeAssembly
    from .runtime import NativeExecutionRuntime

_STATUS = {
    "ACCEPTED": "New",
    "PARTIALLY_FILLED": "PartiallyFilled",
    "FILLED": "Filled",
    "CANCELED": "Cancelled",
    "REJECTED": "Rejected",
}


def _candidate_matches(fill: object, venue: str, native: MonitoringExecution) -> bool:
    return (
        all(
            getattr(fill, key, None) == expected
            for key, expected in (
                ("execution_id", native.execution_id),
                ("client_order_id", native.client_order_id),
                ("instrument_id", native.symbol + "-LINEAR.BYBIT"),
                ("side", native.side.upper()),
                ("quantity", native.quantity),
                ("price", native.price),
                ("commission", native.fee),
                ("commission_currency", native.fee_currency),
                ("ts_event_ns", native.ts_event_ns),
            )
        )
        and venue == native.venue_order_id
    )


def compare_restored_state(
    target: MonitoringTarget, observation: MonitoringObservation
) -> MonitoringAssessment:
    codes: set[str] = set()
    if type(target) is not MonitoringTarget or type(observation) is not MonitoringObservation:
        return MonitoringAssessment(MonitoringStatus.BLOCKED, ("MONITORING_DATA_REQUIRED",))
    if observation.scope != target.scope or observation.symbols != target.symbols:
        codes.add("MONITORING_SCOPE_MISMATCH")
    native, app = target.native, target.application
    order_map = {row.client_order_id: row for row in observation.orders}
    if len(order_map) != len(observation.orders) or set(order_map) != {
        row.client_order_id for row in native.orders
    }:
        codes.add("MONITORING_ORDER_SET_MISMATCH")
    for own in native.orders:
        row = order_map.get(own.client_order_id)
        if own.status not in _STATUS or row is None:
            codes.add("MONITORING_NATIVE_STATUS_UNSUPPORTED")
            continue
        for field in (
            "venue_order_id",
            "symbol",
            "side",
            "order_type",
            "quantity",
            "price",
            "reduce_only",
            "cumulative",
            "leaves",
        ):
            if getattr(own, field) != getattr(row, field):
                codes.add("MONITORING_ORDER_ECONOMICS_MISMATCH")
        expected_tif = (
            "IOC"
            if own.order_type == "Market" and own.time_in_force == "GTC"
            else own.time_in_force
        )
        if (
            row.time_in_force != expected_tif
            or own.time_in_force not in ("GTC", "IOC")
            or own.order_type == "Limit"
            and own.time_in_force != "GTC"
            or row.status != _STATUS[own.status]
        ):
            codes.add("MONITORING_ORDER_STATUS_OR_TIF")
        live = own.status in ("ACCEPTED", "PARTIALLY_FILLED")
        if (
            row.source != ("current" if live else "history")
            or live
            and (row.leaves != own.leaves or row.cumulative + row.leaves != row.quantity)
            or own.status == "ACCEPTED"
            and row.cumulative != 0
            or own.status == "FILLED"
            and row.cumulative != row.quantity
        ):
            codes.add("MONITORING_POSITIVE_ORDER_REQUIRED")
    originals = {(row.symbol, row.execution_id): row for row in native.executions}
    observed = {(row.symbol, row.execution_id): row for row in observation.executions}
    if (
        len(originals) != len(native.executions)
        or len(observed) != len(observation.executions)
        or originals.keys() != observed.keys()
    ):
        codes.add("MONITORING_EXECUTION_SET_MISMATCH")
    windows = {symbol: (start, end) for symbol, start, end in observation.windows}
    for key, original in originals.items():
        execution = observed.get(key)
        if execution is None:
            continue
        lifecycle = order_map.get(original.client_order_id)
        window = windows.get(original.symbol)
        if (
            lifecycle is None
            or window is None
            or not lifecycle.created_ms <= original.ts_event_ns // 1_000_000 <= lifecycle.updated_ms
            or not window[0] <= original.ts_event_ns // 1_000_000 <= window[1]
        ):
            codes.add("MONITORING_EXECUTION_LIFECYCLE_MISMATCH")
        if (
            original.ts_event_ns % 1_000_000
            or execution.ts_event_ns % 1_000_000
            or any(
                getattr(original, name) != getattr(execution, name)
                for name in (
                    "client_order_id",
                    "venue_order_id",
                    "side",
                    "quantity",
                    "price",
                    "fee",
                    "fee_currency",
                    "ts_event_ns",
                    "liquidity",
                    "execution_type",
                )
            )
            or original.seq is not None
            and original.seq != execution.seq
        ):
            codes.add("MONITORING_ORIGINAL_EXECUTION_MISMATCH")
    with localcontext() as context:
        context.prec = 100
        for own in native.orders:
            if (
                sum(
                    (
                        row.quantity
                        for row in native.executions
                        if row.client_order_id == own.client_order_id
                    ),
                    Decimal(0),
                )
                != own.cumulative
            ):
                codes.add("MONITORING_CUMULATIVE_MISMATCH")
    positions = {row.symbol: row.signed_quantity for row in observation.positions}
    expected_positions = {row.symbol: row.signed_quantity for row in native.positions}
    if (
        len(positions) != len(observation.positions)
        or set(positions) != set(target.symbols)
        or positions != expected_positions
    ):
        codes.add("MONITORING_EXPLICIT_POSITION_REQUIRED")
    if app.conflicts:
        codes.add("MONITORING_APPLICATION_CONFLICT")
    for candidate in app.candidates:
        matched = originals.get(
            (
                candidate.fill.instrument_id.removesuffix("-LINEAR.BYBIT"),
                candidate.fill.execution_id,
            )
        )
        if matched is None or not _candidate_matches(
            candidate.fill, candidate.venue_order_id, matched
        ):
            codes.add("MONITORING_CANDIDATE_CONFLICT")
    credited: set[tuple[str, str]] = set()
    for credit in app.credits:
        fill = credit.candidate.fill
        key = (fill.instrument_id.removesuffix("-LINEAR.BYBIT"), fill.execution_id)
        matched = originals.get(key)
        if (
            key in credited
            or matched is None
            or not _candidate_matches(fill, credit.candidate.venue_order_id, matched)
        ):
            codes.add("MONITORING_CREDIT_CONFLICT")
        credited.add(key)
    for historical in app.history_credits:
        history_fill = historical.execution.execution
        key = (history_fill.symbol, history_fill.execution_id)
        matched = originals.get(key)
        if (
            key in credited
            or matched is None
            or any(
                getattr(history_fill, name) != expected
                for name, expected in (
                    ("client_order_id", matched.client_order_id),
                    ("venue_order_id", matched.venue_order_id),
                    ("side", matched.side),
                    ("quantity", matched.quantity),
                    ("price", matched.price),
                    ("fee", matched.fee),
                    ("fee_currency", matched.fee_currency),
                    ("time_ms", matched.ts_event_ns // 1_000_000),
                )
            )
        ):
            codes.add("MONITORING_CREDIT_CONFLICT")
        credited.add(key)
    projected = {
        (state.instrument_id.removesuffix("-LINEAR.BYBIT"), fill.execution_id)
        for state in app.positions
        for fill in state.executions
    }
    if projected != credited:
        codes.add("MONITORING_APPLICATION_CREDIT_MISMATCH")
    app_positions = {
        state.instrument_id.removesuffix("-LINEAR.BYBIT"): state.signed_quantity
        for state in app.positions
    }
    if len(app_positions) != len(app.positions):
        codes.add("MONITORING_APPLICATION_OWNERSHIP_AMBIGUOUS")
    with localcontext() as context:
        context.prec = 100
        for symbol in target.symbols:
            expected = sum(
                (
                    row.quantity * (1 if row.side == "Buy" else -1)
                    for key, row in originals.items()
                    if key in credited and row.symbol == symbol
                ),
                Decimal(0),
            )
            if app_positions.get(symbol, Decimal(0)) != expected:
                codes.add("MONITORING_APPLICATION_CREDIT_MISMATCH")
    if codes:
        return MonitoringAssessment(MonitoringStatus.BLOCKED, tuple(sorted(codes)))
    return MonitoringAssessment(
        MonitoringStatus.MATCHED_NATIVE_WITH_UNCREDITED_FILLS
        if originals.keys() - credited
        else MonitoringStatus.MATCHED
    )


def _native_summary(cache: Any, symbols: tuple[str, ...], digest: str) -> NativeMonitoringSummary:
    """Called only after complete detached native validation, preserving original events."""
    orders, fills = [], []
    for order in sorted(cache.orders(), key=lambda item: str(item.client_order_id)):
        symbol = str(order.instrument_id).removesuffix("-LINEAR.BYBIT")
        orders.append(
            MonitoringOrder(
                str(order.client_order_id),
                str(order.venue_order_id),
                symbol,
                "Buy" if order.side.name == "BUY" else "Sell",
                "Market" if order.order_type.name == "MARKET" else "Limit",
                order.time_in_force.name,
                order.quantity.as_decimal(),
                None if order.order_type.name == "MARKET" else order.price.as_decimal(),
                order.is_reduce_only,
                order.status.name,
                order.filled_qty.as_decimal(),
                order.leaves_qty.as_decimal(),
            )
        )
        for event in order.events:
            if type(event).__name__ == "OrderFilled":
                fills.append(
                    MonitoringExecution(
                        str(event.trade_id),
                        str(order.client_order_id),
                        str(event.venue_order_id),
                        symbol,
                        "Buy" if event.order_side.name == "BUY" else "Sell",
                        event.last_qty.as_decimal(),
                        event.last_px.as_decimal(),
                        event.commission.as_decimal(),
                        str(event.commission.currency),
                        event.ts_event,
                        event.liquidity_side.name,
                    )
                )
    positions = {
        str(position.instrument_id).removesuffix("-LINEAR.BYBIT"): position.quantity.as_decimal()
        * (-1 if position.side.name == "SHORT" else 1)
        for position in cache.positions()
    }
    return NativeMonitoringSummary(
        tuple(orders),
        tuple(fills),
        tuple(MonitoringPosition(symbol, positions.get(symbol, Decimal(0))) for symbol in symbols),
        digest,
    )


def capture_monitoring_target(
    runtime: NativeExecutionRuntime,
    assembly: NativeNodeAssembly,
    *,
    restore_generation_id: str,
    policy_revision: str,
    witness: MonitoringWitness,
    stream_stamp: StreamStamp,
) -> MonitoringTarget:
    """Owner-loop detached validation and durable ownership second pass. Never installs."""
    from .native_recovery import OwnedCacheExpectation
    from .native_restart_codec import capture_native_cache, prepare_native_cache
    from .native_restart_store import NativeRestartIdentity, canonical_bytes

    if type(witness) is not MonitoringWitness:
        raise MonitoringError("MONITORING_WITNESS_REQUIRED")
    witness.require()
    if (
        runtime is not witness._runtime
        or assembly is not witness._assembly
        or restore_generation_id != witness._generation
        or policy_revision != witness._policy
        or stream_stamp != witness._stamp()
        or not 1 <= len(witness._symbols) <= 4
        or len(set(witness._symbols)) != len(witness._symbols)
    ):
        raise MonitoringError("MONITORING_TARGET_SCOPE_REQUIRED")
    identity = NativeRestartIdentity(
        assembly.channel.scope,
        assembly.channel.native_account_id,
        str(assembly.strategy.trader_id),
        str(assembly.strategy.id),
        tuple(sorted(symbol + "-LINEAR.BYBIT" for symbol in witness._symbols)),
    )

    def owners(venues: tuple[tuple[str, str], ...]) -> tuple[OwnedCacheExpectation, ...]:
        requests = runtime.native_owned_requests(venues)
        if len(requests) > 32 or any(item.command_state.name == "PENDING" for item in requests):
            raise MonitoringError("MONITORING_OWNED_ORDER_CAP_OR_PENDING")
        return tuple(
            OwnedCacheExpectation(
                item.request,
                identity.native_account_id,
                identity.trader_id,
                identity.strategy_id,
                next(iter(item.venue_order_ids), None),
                None,
            )
            for item in requests
        )

    first = owners(())
    payload = capture_native_cache(assembly.node.cache, identity=identity, owners=first)
    prepared = prepare_native_cache(payload, identity=identity, owners=first)
    prepare_native_cache(payload, identity=identity, owners=owners(prepared.venue_order_ids))
    native = _native_summary(
        assembly.node.cache, witness._symbols, hashlib.sha256(canonical_bytes(payload)).hexdigest()
    )
    if any(
        order.status not in _STATUS or order.venue_order_id in ("", "None")
        for order in native.orders
    ):
        raise MonitoringError("MONITORING_NATIVE_STATUS_UNSUPPORTED")
    return MonitoringTarget(
        identity.scope,
        witness._symbols,
        restore_generation_id,
        policy_revision,
        native,
        runtime.monitoring_state(),
        stream_stamp,
        witness,
    )


def require_current_target(target: MonitoringTarget, session: object, phase: str) -> None:
    if (
        type(target) is not MonitoringTarget
        or type(target._witness) is not MonitoringWitness
        or phase not in ("PRE", "POST")
        or target._witness._session is not session
    ):
        raise MonitoringError("MONITORING_TARGET_AUTHORITY_REQUIRED")
    witness = target._witness
    stamp = target.stream_stamp
    if (
        phase == "PRE"
        and stamp != StreamStamp(0, "", 0, 0)
        or phase == "POST"
        and (stamp.epoch <= 0 or not stamp.conn_id or stamp.received != stamp.delivered)
    ):
        raise MonitoringError("MONITORING_PHASE_STAMP_REQUIRED")
    current = capture_monitoring_target(
        witness._runtime,
        witness._assembly,
        restore_generation_id=target.restore_generation_id,
        policy_revision=target.policy_revision,
        witness=witness,
        stream_stamp=stamp,
    )
    if current.fingerprint != target.fingerprint:
        raise MonitoringError("MONITORING_TARGET_CHANGED")
