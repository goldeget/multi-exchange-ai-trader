"""Unlinked terminal history stays explicit metadata, never a managed order."""

import importlib
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from multi_exchange_trader.execution.monitoring_reconciliation import compare_restored_state

pytest_plugins = ("test_monitoring_readback", "test_monitoring_receipts")


def _terminal(core: Any, **changes: Any) -> dict[str, object]:
    return {**core[5](), "orderId": "unlinked-venue", "orderLinkId": "", "orderStatus": "Filled",
            "cumExecQty": "2", "leavesQty": "0", **changes}


def _history(core: Any, rows: list[dict[str, object]]) -> None:
    core[4]["/v5/order/history"] = lambda request: {
        "category": "linear", "list": rows, "nextPageCursor": ""}


async def _read(core: Any) -> Any:
    return await importlib.import_module("test_monitoring_readback").read(core)


async def test_terminal_unlinked_and_forced_history_are_separate_unattributed_metadata(reader_core: Any) -> None:
    forced = _terminal(reader_core, orderId="unlinked-close", orderType="Market", timeInForce="IOC",
        price="101", closeOnTrigger=True, createType="CreateByClosing", stopOrderType="StopLoss")
    _history(reader_core, [_terminal(reader_core), forced])
    observation = await _read(reader_core)
    assert [row.client_order_id for row in observation.orders] == ["client-1"]
    metadata = observation.unattributed_history_orders
    assert len(metadata) == 2
    assert all(row.record.client_order_id == "" and row.attribution == "UNATTRIBUTED"
               and row.source == "history" and not row.managed and not row.financial_qualified for row in metadata)
    assert compare_restored_state(reader_core[2], observation).status.value == "MATCHED"


@pytest.mark.parametrize("changes", [
    {"orderStatus": "New"}, {"orderStatus": "PartiallyFilled"}, {"leavesQty": "1"},
    {"cumExecQty": "3"}, {"orderStatus": "Rejected", "cumExecQty": "1"},
    {"positionIdx": 1}, {"positionIdx": True}, {"orderLinkId": None},
    {"orderId": ""}, {"reduceOnly": "false"}, {"updatedTime": "9999999999999"},
])
async def test_active_ambiguous_or_malformed_unlinked_history_blocks(reader_core: Any,
                                                                 changes: dict[str, object]) -> None:
    _history(reader_core, [_terminal(reader_core, **changes)])
    with pytest.raises(ValueError):
        await _read(reader_core)


async def test_current_unlinked_row_and_known_native_venue_collision_never_become_metadata(reader_core: Any) -> None:
    reader_core[4]["/v5/order/realtime"] = lambda request: {
        "category": "linear", "list": [_terminal(reader_core)], "nextPageCursor": ""}
    with pytest.raises(ValueError):
        await _read(reader_core)
    reader_core[4].pop("/v5/order/realtime")
    _history(reader_core, [_terminal(reader_core, orderId="venue-1")])
    with pytest.raises(ValueError):
        await _read(reader_core)


async def test_later_history_page_is_consumed_and_conflicting_unlinked_duplicate_blocks(reader_core: Any) -> None:
    calls = []

    def pages(request: Any) -> dict[str, object]:
        calls.append(request)
        later = "cursor" in request.url.params
        row = _terminal(reader_core, closeOnTrigger=later)
        return {"category": "linear", "list": [row], "nextPageCursor": "" if later else "next"}

    reader_core[4]["/v5/order/history"] = pages
    with pytest.raises(ValueError):
        await _read(reader_core)
    assert len(calls) == 2


async def test_owned_conditional_history_keeps_original_strict_parser(reader_core: Any) -> None:
    _history(reader_core, [_terminal(reader_core, orderId="venue-1", orderLinkId="client-1",
                                   closeOnTrigger=True, createType="CreateByClosing")])
    with pytest.raises(ValueError):
        await _read(reader_core)


async def test_comparison_rejects_untyped_or_relabelled_unattributed_metadata(reader_core: Any) -> None:
    _history(reader_core, [_terminal(reader_core)])
    observation = await _read(reader_core)
    assert compare_restored_state(reader_core[2], replace(
        observation, unattributed_history_orders=(object(),))).status.value == "BLOCKED"
    row = observation.unattributed_history_orders[0]
    oversized = replace(row, price=Decimal("1e100000000"))
    assert compare_restored_state(reader_core[2], replace(
        observation, unattributed_history_orders=(oversized,))).status.value == "BLOCKED"
    object.__setattr__(row, "managed", True)
    assert compare_restored_state(reader_core[2], observation).status.value == "BLOCKED"


def _execution(**changes: object) -> dict[str, object]:
    ms = importlib.import_module("test_monitoring_receipts").MS
    return {"symbol": "BTCUSDT", "execId": "unlinked-execution", "orderId": "unlinked-venue",
        "orderLinkId": "", "side": "Buy", "execType": "Trade", "execTime": str(ms - 500),
        "execQty": "2", "execPrice": "100", "execValue": "200", "execFee": "0.12",
        "feeCurrency": "USDT", "feeRate": "0.0006", "closedSize": "0", "isMaker": False,
        "seq": "1", **changes}


def _executions(core: Any, rows: list[dict[str, object]]) -> None:
    core[4]["/v5/execution/list"] = lambda request: {
        "category": "linear", "list": rows, "nextPageCursor": ""}


async def test_unlinked_trade_adl_bust_and_funding_remain_explicit_unattributed_events(reader_core: Any) -> None:
    rows = [_execution(execId="unlinked-" + kind, execType=kind)
            for kind in ("Trade", "AdlTrade", "BustTrade", "Funding")]
    rows[-1].update(execQty="0", execPrice="0", execValue="0", execFee="-0.1")
    _executions(reader_core, rows)
    observation = await _read(reader_core)
    assert observation.executions == ()
    metadata = observation.unattributed_history_executions
    assert [row.record.execution_type for row in metadata] == ["Trade", "AdlTrade", "BustTrade", "Funding"]
    assert all(row.record.client_order_id == "" and row.attribution == "UNATTRIBUTED"
               and row.source == "history" and not row.managed and not row.financial_qualified for row in metadata)
    assert compare_restored_state(reader_core[2], observation).status.value == "MATCHED"
    assert compare_restored_state(reader_core[2], replace(
        observation, unattributed_history_executions=(object(),))).status.value == "BLOCKED"


@pytest.mark.parametrize("changes", [
    {"execType": "Delivery"}, {"execType": "Unknown"}, {"feeCurrency": "BTC"},
    {"execQty": "-1"}, {"closedSize": "3"}, {"seq": True}, {"isMaker": "false"},
    {"orderLinkId": None}, {"execTime": "9999999999999"}, {"execValue": "199"},
])
async def test_unlinked_malformed_unsupported_or_inconsistent_execution_blocks(
    reader_core: Any, changes: dict[str, object],
) -> None:
    _executions(reader_core, [_execution(**changes)])
    with pytest.raises(ValueError):
        await _read(reader_core)


async def test_unlinked_execution_colliding_with_owned_venue_or_pending_command_blocks(
    reader_core: Any, tmp_path: Path,
) -> None:
    from multi_exchange_trader.execution.journal import CommandJournal, CommandKind, ExecutionScope

    _executions(reader_core, [_execution(orderId="venue-1")])
    with pytest.raises(ValueError):
        await _read(reader_core)
    _executions(reader_core, [_execution()])
    target = reader_core[2]
    journal = CommandJournal(tmp_path / "unresolved.sqlite3", ExecutionScope("12345", "DEMO"))
    try:
        journal.register("unresolved-entry", CommandKind.ENTRY, {"synthetic": True})
        journal.claim("unresolved-entry")
        record = journal.mark_unknown("unresolved-entry", "synthetic unknown outcome")
        uncertain = replace(target, application=replace(target.application, commands=(record,)))
        core = (*reader_core[:2], uncertain, *reader_core[3:])
        with pytest.raises(ValueError):
            await _read(core)
    finally:
        journal.close()


async def test_all_execution_pages_are_consumed_and_conflicting_unlinked_payload_blocks(reader_core: Any) -> None:
    calls = []

    def pages(request: Any) -> dict[str, object]:
        calls.append(request)
        later = "cursor" in request.url.params
        return {"category": "linear", "list": [_execution(execFee="0.2" if later else "0.12")],
                "nextPageCursor": "" if later else "next"}

    reader_core[4]["/v5/execution/list"] = pages
    with pytest.raises(ValueError):
        await _read(reader_core)
    assert len(calls) == 2


async def test_order_retention_is_separate_from_recent_owned_execution_window(reader_core: Any) -> None:
    ms = importlib.import_module("test_monitoring_receipts").MS
    _history(reader_core, [_terminal(reader_core, createdTime=str(ms - 86400000),
                                   updatedTime=str(ms - 86400000))])
    observation = await _read(reader_core)
    assert observation.windows[0][1] == ms - 1000
    assert compare_restored_state(reader_core[2], observation).status.value == "MATCHED"


async def test_flat_unattributed_history_matches_fresh_data_without_issuing_target_authority(reader_core: Any) -> None:
    fresh_api = importlib.import_module("multi_exchange_trader.execution.fresh_monitoring_v1")
    target = replace(reader_core[2], native=replace(reader_core[2].native, orders=()))
    core = (*reader_core[:2], target, *reader_core[3:])
    core[4]["/v5/order/realtime"] = lambda request: {
        "category": "linear", "list": [], "nextPageCursor": ""}
    _history(core, [_terminal(core)])
    _executions(core, [_execution()])
    observation = await _read(core)
    # Pure frozen data can be compared; it remains an unissued target.
    fresh = fresh_api.FreshMonitoringTargetV1(target.scope, target.symbols, "synthetic-start",
        "FRESH_EMPTY_CACHE", None, "SDK1231_OBSERVATION_UNKNOWN_FEES_V1", "1",
        target.native, target.application, target.stream_stamp, None)
    assessment = fresh_api.compare_fresh_observation_v1(fresh, observation)
    assert assessment.status.value == "MATCHED" and assessment.credit_authorized is False
    with pytest.raises(ValueError, match="CAPTURED_TARGET_REQUIRED"):
        fresh_api.require_fresh_target_v1(fresh, core[0], "PRE")
    assert fresh_api.compare_fresh_observation_v1(fresh, replace(
        observation, unattributed_history_executions=(object(),))).status.value == "BLOCKED"


@pytest.mark.parametrize("change", ["raw-hash", "record"])
async def test_original_receipt_rejects_metadata_mutated_by_its_owner_callback(
    target_core: Any, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    core = target_core
    owner, binding, *_ = core[0]
    original_send = httpx.AsyncClient.send
    ms = importlib.import_module("test_monitoring_receipts").MS

    async def send(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        if request.url.path == "/v5/execution/list":
            return httpx.Response(200, json={"retCode": 0, "time": ms, "result": {
                "category": "linear", "list": [_execution()], "nextPageCursor": ""}}, request=request)
        return await original_send(client, request, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    armed, mutations = False, 0
    captured: list[Any] = []
    witness = core[1]._witness
    original_guard = witness._guard

    def guard() -> None:
        nonlocal armed, mutations
        original_guard()
        if armed:
            armed = False
            mutations += 1
            if change == "raw-hash":
                object.__setattr__(captured[0], "raw_row_sha256", "f" * 64)
            else:
                object.__setattr__(captured[0], "record", replace(captured[0].record, fee=Decimal("0.13")))

    object.__setattr__(witness, "_guard", guard)
    receipt = await owner.read_monitoring_receipt(binding, target=core[1], phase="PRE")
    view = owner.require_monitoring_receipt(receipt, binding=binding, target=core[1], phase="PRE")
    captured.append(view.observation.unattributed_history_executions[0])
    armed = True
    with pytest.raises(ValueError, match="UNATTRIBUTED_HISTORY_CHANGED"):
        owner.consume_monitoring_receipt(receipt, binding=binding, target=core[1], phase="PRE")
    assert mutations == 1 and receipt not in owner._monitoring_receipts


def test_acknowledged_nonterminal_command_requires_its_bound_venue_ids(tmp_path: Path) -> None:
    from multi_exchange_trader.execution.bybit_native import NativeOrderRequest
    from multi_exchange_trader.execution.journal import CommandJournal, CommandKind, ExecutionScope
    from multi_exchange_trader.execution.monitoring_unattributed_history import (
        unresolved_monitoring_commands,
    )
    from multi_exchange_trader.execution.protection import OwnedOrder
    from multi_exchange_trader.execution.runtime import NativeOwnedRequest

    target = importlib.import_module("test_monitoring_reconciliation").sample()[0]
    journal = CommandJournal(tmp_path / "acknowledged.sqlite3", ExecutionScope("12345", "DEMO"))
    try:
        journal.register("acknowledged-entry", CommandKind.ENTRY, {"synthetic": True})
        journal.claim("acknowledged-entry")
        record = journal.acknowledge("acknowledged-entry", "synthetic acknowledgement")
        changed = replace(target, application=replace(target.application, commands=(record,)))
        assert unresolved_monitoring_commands(changed)
        request = NativeOrderRequest(target.scope, OwnedOrder(record.client_order_id, "position-1",
            "BTCUSDT-LINEAR.BYBIT", "BUY", "ENTRY", Decimal(2), False, 1, "1"), "LIMIT", Decimal(100), Decimal(100))
        bound = NativeOwnedRequest(request, record.state, frozenset({"venue-bound"}))
        changed = replace(changed, application=replace(changed.application, owned=(bound,)))
        # This is only the ambiguity predicate; original target/positive checks still apply.
        assert not unresolved_monitoring_commands(changed)
    finally:
        journal.close()
