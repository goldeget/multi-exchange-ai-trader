"""Whole-account open orders must have exact, disjoint quantity coverage."""

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from test_bybit_linear_model_inputs_v1 import (
    bind_d_graph,
    d_reprepare,
    full_d_graph,
)

from multi_exchange_trader.execution import bybit_linear_model_inputs_v1 as model
from multi_exchange_trader.execution import bybit_linear_model_v1 as calculation
from multi_exchange_trader.execution import risk_v2_contracts as contracts
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected


def order_graph(*, reserved: bool = False, reduce_only: bool = False) -> dict[str, Any]:
    graph = full_d_graph()
    held = graph["venue_fields"][4][0]
    source = replace(
        held.source,
        observation_id="whole-account-order",
        path="/v5/order/realtime",
        query=(("category", "linear"), ("settleCoin", "USDT")),
    )

    def field(name: str, value: str) -> model.BybitDecimalFieldV1:
        return model.BybitDecimalFieldV1(
            Decimal(value),
            model.BybitModelFieldRefV1(source, "/result/list/0/" + name, "STRING", value),
        )

    order = model.BybitHeldOrderV1(
        source, "venue-Y-order", "RY" if reserved else "different-client", held.instrument_pin,
        0, "Buy", "Limit", field("price", "50"), field("qty", "0.5"),
        field("leavesQty", "0.25"), "PartiallyFilled", reduce_only, False, None,
        held.updated_time_ms,
    )
    values = list(graph["venue_fields"])
    values[5] = (order,)
    values[8] = tuple(sorted((*values[8], source), key=lambda item: item.observation_id))
    return dict(graph, venue_fields=tuple(values))


def with_account_order(
    graph: dict[str, Any], *, component_id: str = "venue-Y-order",
    side: str = "BUY", quantity: str = "0.25", key: Any = None,
) -> dict[str, Any]:
    inputs = graph["inputs"]
    order = graph["venue_fields"][5][0]
    component = contracts.ExposureQuantityV2(
        component_id, order.instrument_pin.key if key is None else key,
        "ACCOUNT_ENTRY_ORDER", side, Decimal(quantity),
    )
    account = replace(inputs.account, components=tuple(sorted(
        (*inputs.account.components, component),
        key=lambda item: (*contracts._key_order_v2(item.instrument), item.component_id),
    )))
    members = tuple(
        replace(member, account_component_ids=tuple(
            item.component_id for item in account.components
            if item.instrument == member.instrument_pin.key
        ))
        for member in graph["domain"].members
    )
    return d_reprepare(graph, replace(inputs, account=account), members=members)


def replace_order(graph: dict[str, Any], **changes: Any) -> dict[str, Any]:
    values = list(graph["venue_fields"])
    values[5] = (replace(values[5][0], **changes),)
    return dict(graph, venue_fields=tuple(values))


def test_unreserved_other_symbol_order_cannot_disappear_from_risk_inputs() -> None:
    with pytest.raises(RiskV2Rejected, match="HELD_COVERAGE_REQUIRED"):
        bind_d_graph(order_graph())


def test_exact_unattributed_venue_order_quantity_is_in_account_gross_once() -> None:
    graph = with_account_order(order_graph())
    bound = bind_d_graph(graph)
    order = bound.venue.held_orders[0]
    entry = bound.risk_inputs.account.components[1]
    assert entry.component_id == order.order_id
    assert entry.base_quantity == order.leaves_quantity.value == Decimal("0.25")
    assert bound.domain.members[1].account_component_ids == ("AY", "venue-Y-order")
    assert bound.domain.members[1].reservation_component_ids == ("RY",)
    assert sum(item.base_quantity for item in bound.risk_inputs.account.components if item.origin == "ACCOUNT_ENTRY_ORDER") == Decimal("0.25")
    assert not hasattr(bound, "disjointness_proven")


@pytest.mark.parametrize("kind", ["quantity", "side", "identity", "instrument"])
def test_account_order_cannot_replace_exact_raw_remaining_identity(kind: str) -> None:
    graph = order_graph()
    changes: dict[str, Any] = {
        "quantity": {"quantity": "0.5"},
        "side": {"side": "SELL"},
        "identity": {"component_id": "different-venue-order"},
        "instrument": {"key": graph["inputs"].instrument.key},
    }[kind]
    with pytest.raises(RiskV2Rejected, match="HELD_COVERAGE_REQUIRED|UNSUPPORTED_MODE"):
        bind_d_graph(with_account_order(graph, **changes))


def test_client_reservation_without_durable_intent_and_native_proof_is_rejected() -> None:
    graph = order_graph(reserved=True)
    before = contracts.reservation_snapshot_sha256_v2(graph["inputs"].reservations)
    with pytest.raises(RiskV2Rejected, match="EXPOSURE_OVERLAP"):
        bind_d_graph(graph)
    assert contracts.reservation_snapshot_sha256_v2(graph["inputs"].reservations) == before


def test_same_order_cannot_be_in_both_account_and_original_reservation() -> None:
    graph = with_account_order(order_graph(reserved=True))
    with pytest.raises(RiskV2Rejected, match="EXPOSURE_OVERLAP|HELD_COVERAGE_REQUIRED"):
        bind_d_graph(graph)


@pytest.mark.parametrize("kind", ["side", "quantity", "instrument", "reduce-only"])
def test_client_id_alone_cannot_hide_incompatible_order(kind: str) -> None:
    graph = order_graph(reserved=True)
    order = graph["venue_fields"][5][0]
    if kind == "quantity":
        quantity = replace(order.quantity, value=Decimal("0.75"), reference=replace(order.quantity.reference, wire_spelling="0.75"))
        changes = {"quantity": quantity}
    elif kind == "instrument":
        changes = {"instrument_pin": graph["domain"].members[0].instrument_pin}
    elif kind == "side":
        changes = {"side": "Sell"}
    else:
        changes = {"reduce_only": True}
    with pytest.raises(RiskV2Rejected, match="HELD_COVERAGE_REQUIRED|EXPOSURE_OVERLAP"):
        bind_d_graph(replace_order(graph, **changes))


def test_source_proven_reduce_only_order_needs_no_entry_exposure() -> None:
    graph = order_graph(reduce_only=True)
    assert bind_d_graph(graph).venue.held_orders[0].reduce_only is True
    with pytest.raises(RiskV2Rejected, match="HELD_COVERAGE_REQUIRED|EXPOSURE_OVERLAP"):
        bind_d_graph(with_account_order(graph))


def test_close_on_trigger_is_not_reduce_only_evidence() -> None:
    with pytest.raises(RiskV2Rejected, match="HELD_COVERAGE_REQUIRED"):
        bind_d_graph(replace_order(order_graph(), close_on_trigger=True))


def test_duplicate_client_id_cannot_reuse_one_original_reservation() -> None:
    graph = order_graph(reserved=True)
    values = list(graph["venue_fields"])
    first = values[5][0]
    values[5] = (first, replace(first, order_id="venue-Y-order-2"))
    with pytest.raises(RiskV2Rejected, match="EXPOSURE_OVERLAP|HELD_COVERAGE_REQUIRED"):
        bind_d_graph(dict(graph, venue_fields=tuple(values)))


def test_duplicate_venue_id_cannot_move_between_products_or_instruments() -> None:
    graph = order_graph(reduce_only=True)
    values = list(graph["venue_fields"])
    first = values[5][0]
    second = replace(first, instrument_pin=graph["domain"].members[0].instrument_pin)
    values[5] = (second, first)
    with pytest.raises(RiskV2Rejected, match="EXPOSURE_OVERLAP|HELD_COVERAGE_REQUIRED"):
        bind_d_graph(dict(graph, venue_fields=tuple(values)))


def test_account_order_without_retained_raw_order_is_rejected() -> None:
    graph = with_account_order(order_graph())
    values = list(graph["venue_fields"])
    values[5] = ()
    with pytest.raises(RiskV2Rejected, match="HELD_COVERAGE_REQUIRED"):
        bind_d_graph(dict(graph, venue_fields=tuple(values)))


def test_omitted_client_id_cannot_prove_disjointness_from_same_side_reservation() -> None:
    graph = with_account_order(replace_order(order_graph(), order_link_id=""))
    with pytest.raises(RiskV2Rejected, match="EXPOSURE_OVERLAP"):
        bind_d_graph(graph)


def test_omitted_client_id_with_no_same_side_reservation_keeps_exact_venue_identity() -> None:
    graph = replace_order(order_graph(), order_link_id="", side="Sell")
    bound = bind_d_graph(with_account_order(graph, side="SELL"))
    assert bound.risk_inputs.account.components[1].component_id == "venue-Y-order"
    assert bound.venue.held_orders[0].order_link_id == ""


@pytest.mark.parametrize("quantity,leaves", [("0", "0"), ("0.5", "0"), ("0.5", "0.75")])
def test_remaining_quantity_must_be_positive_and_within_original_order(
    quantity: str, leaves: str,
) -> None:
    graph = order_graph(reduce_only=True)
    order = graph["venue_fields"][5][0]
    def amount(field: model.BybitDecimalFieldV1, value: str) -> model.BybitDecimalFieldV1:
        return replace(field, value=Decimal(value),
                       reference=replace(field.reference, wire_spelling=value))
    with pytest.raises(RiskV2Rejected, match="HELD_COVERAGE_REQUIRED"):
        bind_d_graph(replace_order(graph, quantity=amount(order.quantity, quantity),
                                  leaves_quantity=amount(order.leaves_quantity, leaves)))


def test_actual_calculator_rebind_cannot_accept_new_uncovered_account_order() -> None:
    graph = full_d_graph()
    bound = bind_d_graph(graph)
    amended = replace(bound, venue=model.BybitVenueStateV1(*order_graph()["venue_fields"]))
    with pytest.raises(RiskV2Rejected, match="HELD_COVERAGE_REQUIRED"):
        calculation.calculate_bound_bybit_model_v1(amended, now=graph["now"])


@pytest.mark.parametrize("changed", ["price", "order-type", "venue-id"])
def test_same_link_side_and_size_cannot_borrow_reservation_for_different_intent(changed: str) -> None:
    graph = order_graph(reserved=True)
    order = graph["venue_fields"][5][0]
    if changed == "price":
        price = replace(order.price, value=Decimal(60),
                        reference=replace(order.price.reference, wire_spelling="60"))
        changes: dict[str, Any] = {"price": price}
    elif changed == "order-type":
        changes = {"order_type": "Market"}
    else:
        changes = {"order_id": "unrelated-venue-order"}
    # A reservation DTO has no durable order type, price or venue ownership
    # evidence. Equal client ID, side and size cannot establish identity.
    with pytest.raises(RiskV2Rejected, match="EXPOSURE_OVERLAP"):
        bind_d_graph(replace_order(graph, **changes))
