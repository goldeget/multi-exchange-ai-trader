"""Actual pinned adapter forwarding, never wire serialization or venue acceptance.

The imported autouse fixture requires Linux OS network isolation before any
native imports. Only the Rust HTTP boundary is replaced; no client is created.
"""

import inspect
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from test_bybit_native_linux import (
    CLIENT_ID,
    INSTRUMENT,
    adapter_boundary,
    isolated_native_runtime,  # noqa: F401
    module,
    request,
)
from test_bybit_native_linux import native_harness as native_harness_fixture

from multi_exchange_trader.execution.bybit_native import AttachedProtection
from multi_exchange_trader.execution.native_capabilities import describe_native_capability

native_harness = native_harness_fixture


async def forwarded_payload(harness: Any) -> dict[str, Any]:
    assert len(harness.commands) == 1
    command = harness.commands[0]
    assert type(command) is module("execution.messages").SubmitOrder
    receiver = adapter_boundary(harness)
    await module("adapters.bybit.execution").BybitExecutionClient._submit_order(receiver, command)
    assert len(receiver._http_client.calls) == 1
    assert [type(event) for event in receiver.emitted] == [
        module("model.events.order").OrderSubmitted]
    payload: dict[str, Any] = receiver._http_client.calls[0]
    # The permissive fake cannot hide a keyword the public Rust binding rejects.
    inspect.signature(module("core.nautilus_pyo3").BybitHttpClient.submit_order).bind(
        object(), **payload)
    return payload


def assert_identity(payload: dict[str, Any], *, side: str, reduce_only: bool,
                    order_type: str) -> None:
    native = module("core.nautilus_pyo3")
    assert str(payload["client_order_id"]) == CLIENT_ID
    assert str(payload["instrument_id"]) == INSTRUMENT
    assert str(payload["account_id"]) == "BYBIT-UNIFIED"
    assert str(payload["quantity"]) == "0.010"
    assert payload["order_side"] == getattr(native.OrderSide, side)
    assert payload["order_type"] == getattr(native.OrderType, order_type)
    assert payload["product_type"] == native.BybitProductType.LINEAR
    assert payload["position_idx"] == native.BybitPositionIdx.from_str(0)
    assert payload["reduce_only"] is reduce_only


@pytest.mark.parametrize("source", ["MarkPrice", "LastPrice", "IndexPrice"])
@pytest.mark.parametrize("side,stop,take_profit", [
    ("BUY", "49000", "52000"), ("SELL", "52000", "49000"),
])
async def test_attached_sources_reach_actual_native_tp_sl_params(
    native_harness: Any, source: Any, side: str, stop: str, take_profit: str,
) -> None:
    harness = native_harness
    original = request()
    entry = replace(original, owned=replace(original.owned, side=side),
        attached=AttachedProtection(Decimal(stop), Decimal(take_profit), source))
    harness.bridge.submit(entry)
    order = harness.commands[0].order
    assert order.emulation_trigger == module("model.enums").TriggerType.NO_TRIGGER
    payload = await forwarded_payload(harness)
    assert_identity(payload, side=side, reduce_only=False, order_type="LIMIT")
    assert str(payload["price"]) == "50000.0"
    assert payload["trigger_price"] is None
    protection = payload["native_tp_sl"]
    assert type(protection) is module("core.nautilus_pyo3").BybitNativeTpSlParams
    assert (protection.stop_loss, protection.take_profit) == (stop, take_profit)
    assert (protection.tp_trigger_by, protection.sl_trigger_by) == (source, source)
    assert (protection.tp_order_type, protection.sl_order_type, protection.tpsl_mode) == (
        "Market", "Market", "Full")
    capability = describe_native_capability(native_version="1.231.0",
        command_kind="ATTACHED_ENTRY", trigger_source=source)
    assert capability.parameter_forwarding == "SUPPORTED"
    assert capability.production_supported is False


def conditional_command(harness: Any, *, factory_method: str, side: str,
                        trigger_price: str, generic_source: str | None = None) -> Any:
    ids, enums, objects = module("model.identifiers"), module("model.enums"), module("model.objects")
    order = getattr(harness.strategy.order_factory, factory_method)(
        instrument_id=ids.InstrumentId.from_str(INSTRUMENT),
        client_order_id=ids.ClientOrderId(CLIENT_ID),
        order_side=getattr(enums.OrderSide, side), quantity=objects.Quantity.from_str("0.010"),
        trigger_price=objects.Price.from_str(trigger_price), trigger_type=enums.TriggerType.MARK_PRICE,
        emulation_trigger=enums.TriggerType.NO_TRIGGER, reduce_only=True)
    params: dict[str, object] = {"position_idx": 0}
    if generic_source is not None:
        params["trigger_by"] = generic_source
    harness.strategy.submit_order(order, params=params)
    assert len(harness.commands) == 1
    assert harness.commands[0].order is order
    assert order.client_order_id.value == CLIENT_ID
    assert order.instrument_id.value == INSTRUMENT
    assert order.quantity.as_decimal() == Decimal("0.010")
    assert order.side == getattr(enums.OrderSide, side)
    assert order.is_reduce_only is True
    assert order.trigger_price.as_decimal() == Decimal(trigger_price)
    assert order.trigger_type == enums.TriggerType.MARK_PRICE
    assert order.emulation_trigger == enums.TriggerType.NO_TRIGGER
    assert harness.commands[0].params == params
    return order


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("factory_method,order_type,command_kind", [
    ("stop_market", "STOP_MARKET", "STANDALONE_STOP"),
    ("market_if_touched", "MARKET_IF_TOUCHED", "STANDALONE_TAKE_PROFIT"),
])
async def test_standalone_mark_price_is_not_forwarded_despite_exact_conditional_fields(
    native_harness: Any, side: str, factory_method: str, order_type: str, command_kind: Any,
) -> None:
    harness = native_harness
    trigger = "52000.0" if (side == "BUY") == (factory_method == "stop_market") else "49000.0"
    order = conditional_command(harness, factory_method=factory_method, side=side,
                                trigger_price=trigger)
    assert order.order_type == getattr(module("model.enums").OrderType, order_type)
    payload = await forwarded_payload(harness)
    assert_identity(payload, side=side, reduce_only=True, order_type=order_type)
    assert str(payload["trigger_price"]) == trigger
    assert payload["price"] is None
    assert payload["native_tp_sl"] is None
    assert {"trigger_by", "trigger_type", "trigger_source"}.isdisjoint(payload)
    capability = describe_native_capability(native_version="1.231.0", command_kind=command_kind,
                                            trigger_source="MarkPrice")
    assert capability.parameter_forwarding == "UNSUPPORTED"
    assert capability.reason_code == "EXPLICIT_TRIGGER_SOURCE_NOT_FORWARDED"
    assert capability.production_supported is False


def test_public_pyo3_submit_signature_cannot_accept_explicit_trigger_source() -> None:
    signature = inspect.signature(module("core.nautilus_pyo3").BybitHttpClient.submit_order)
    assert tuple(signature.parameters) == (
        "self", "account_id", "product_type", "instrument_id", "client_order_id", "order_side",
        "order_type", "quantity", "time_in_force", "price", "trigger_price", "post_only",
        "reduce_only", "is_quote_quantity", "is_leverage", "position_idx", "bbo_side_type",
        "bbo_level", "native_tp_sl")
    with pytest.raises(TypeError, match="trigger_by"):
        signature.bind_partial(trigger_by="MarkPrice")


async def test_generic_trigger_by_is_silently_ignored_not_a_supported_workaround(
    native_harness: Any,
) -> None:
    harness = native_harness
    conditional_command(harness, factory_method="stop_market", side="SELL",
                        trigger_price="49000.0", generic_source="MarkPrice")
    assert harness.commands[0].params["trigger_by"] == "MarkPrice"
    payload = await forwarded_payload(harness)
    assert_identity(payload, side="SELL", reduce_only=True, order_type="STOP_MARKET")
    assert str(payload["trigger_price"]) == "49000.0"
    assert payload["price"] is None
    assert payload["native_tp_sl"] is None
    assert {"trigger_by", "trigger_type", "trigger_source"}.isdisjoint(payload)
    capability = describe_native_capability(native_version="1.231.0",
        command_kind="STANDALONE_STOP", trigger_source="MarkPrice")
    assert capability.parameter_forwarding == "UNSUPPORTED"
    assert capability.production_supported is False
