"""Real SDK constructor, with no lifecycle/network startup."""
# ruff: noqa: F811 -- shared pytest session fixture

import asyncio
import importlib
import inspect

import pytest
from test_monitoring_receipts import SCOPE, session  # noqa: F401


def module():
    spec = importlib.util.find_spec("multi_exchange_trader.execution.native_monitor_client")
    assert spec is not None, "passive genuine Bybit subclass is required"
    return importlib.import_module(spec.name)


@pytest.fixture
async def client(session):
    if importlib.util.find_spec("multi_exchange_trader.execution.native_monitor_client") is None:
        yield None
        return
    mod = module()
    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.common.component import LiveClock, MessageBus
    from nautilus_trader.model.identifiers import TraderId

    owner, binding, *_ = session
    configs = []
    owner.configure_native_monitoring(binding, scope=SCOPE, install=configs.append)
    clock = LiveClock()
    bus = MessageBus(trader_id=TraderId("TRADER-001"), clock=clock)
    result = mod.create_monitor_client(
        session=owner,
        binding=binding,
        scope=SCOPE,
        symbols=("BTCUSDT",),
        loop=asyncio.get_running_loop(),
        name="BYBIT",
        config=configs[0],
        msgbus=bus,
        cache=Cache(),
        clock=clock,
    )
    yield result
    await result._disconnect()


async def test_constructor_is_genuine_and_repayment_task_is_inert(client):
    module()
    from nautilus_trader.adapters.bybit.execution import BybitExecutionClient

    assert isinstance(client, BybitExecutionClient)
    await client._repay_task
    assert client._repay_queues == {}
    assert not client.is_connected
    assert client.monitoring_stamp().epoch == 0


@pytest.mark.parametrize(
    "method",
    [
        "submit_order",
        "submit_order_list",
        "modify_order",
        "cancel_order",
        "cancel_all_orders",
        "batch_cancel_orders",
        "query_account",
        "query_order",
        "_submit_order",
        "_submit_order_list",
        "_submit_order_list_http",
        "_submit_order_list_ws",
        "_modify_order",
        "_cancel_order",
        "_cancel_all_orders",
        "_batch_cancel_orders",
        "_query_account",
        "_query_order",
        "generate_order_status_reports",
        "generate_order_status_report",
        "generate_fill_reports",
        "generate_position_status_reports",
        "generate_mass_status",
        "set_leverage",
        "set_position_mode",
        "_handle_borrow_action",
        "_handle_repay_action",
        "_repay_spot_borrow_if_needed",
        "_send_fill_report",
        "_send_order_status_report",
        "_send_position_status_report",
        "_send_mass_status_report",
        "generate_order_filled",
        "generate_order_submitted",
        "generate_order_rejected",
        "arbitrary_future_write",
    ],
)
async def test_financial_query_report_and_synthesis_denial_is_sticky_before_event(client, method):
    module()
    with pytest.raises(ValueError, match="MONITORING_"):
        result = getattr(client, method)(object())
        if inspect.isawaitable(result):
            await result
    assert client.monitoring_fault is not None
    assert client._cache.orders() == []
    with pytest.raises(ValueError):
        await client.wait_monitoring_ready()


@pytest.mark.parametrize("name", ["_ws_private_client", "_ws_trade_client"])
async def test_constructor_held_specialized_socket_cannot_connect_or_trade(client, name):
    module()
    socket = getattr(client, name)
    with pytest.raises(ValueError):
        socket.connect(loop_=asyncio.get_running_loop(), callback=lambda x: None)
    assert client.monitoring_fault


def test_full_semantic_comparison_excludes_only_acquisition_times_and_empty_info():
    mod = module()
    from nautilus_trader.test_kit.providers import TestInstrumentProvider

    source = TestInstrumentProvider.xrpusdt_linear_bybit()
    raw = source.to_dict(source)
    raw["ts_event"] += 100
    raw["ts_init"] += 100
    raw["info"] = {}
    assert mod.instrument_semantics(source) == mod.instrument_semantics(type(source).from_dict(raw))
    for field, value in [
        ("maker_fee", "0.002"),
        ("margin_init", "0.2"),
        ("info", {"changed": True}),
    ]:
        changed = dict(raw)
        changed[field] = value
        assert mod.instrument_semantics(source) != mod.instrument_semantics(
            type(source).from_dict(changed)
        )


def test_passive_config_disables_all_background_queries_and_cache_writes():
    mod = module()
    assert hasattr(mod, "passive_node_settings"), "explicit passive node settings required"
    settings = mod.passive_node_settings([])
    engine, cache = settings["exec_engine"], settings["cache"]
    assert engine.load_cache is False and engine.reconciliation is False
    assert engine.inflight_check_interval_ms == 0
    assert engine.filter_unclaimed_external_orders and engine.filter_position_reports
    for field in (
        "open_check_interval_secs",
        "position_check_interval_secs",
        "own_books_audit_interval_secs",
        "snapshot_positions_interval_secs",
        "purge_closed_orders_interval_mins",
        "purge_closed_positions_interval_mins",
        "purge_account_events_interval_mins",
        "external_clients",
    ):
        assert getattr(engine, field) is None
    for field in (
        "generate_missing_orders",
        "manage_own_order_books",
        "snapshot_orders",
        "snapshot_positions",
        "purge_from_database",
    ):
        assert getattr(engine, field) is False
    assert cache.database is None and cache.flush_on_start is False


def test_direct_builder_defaults_remain_nonmonitoring():
    from multi_exchange_trader.execution.native_node import build_native_node, build_node_config

    for builder in (build_native_node, build_node_config):
        assert "monitor_only" in inspect.signature(builder).parameters
        assert inspect.signature(builder).parameters["monitor_only"].default is False


async def test_cleanup_preserves_owned_stream_original_loss_evidence(client):
    from multi_exchange_trader.execution.private_monitor_stream import OwnedPrivateMonitorStream

    async def dispatch(*args):
        pass

    # Real unconnected objects: this tests fault retention, not admission/lifecycle.
    stream = OwnedPrivateMonitorStream(
        api_key="synthetic", api_secret="synthetic", guard=lambda: None, dispatch=dispatch
    )
    client._monitor_stream = stream
    stream.retire("MONITORING_INGRESS_LIMIT")
    client.retire_monitoring("MONITORING_STOPPED")
    assert client.monitoring_fault == "MONITORING_INGRESS_LIMIT"
    await client._disconnect()
    assert client.monitoring_fault == "MONITORING_INGRESS_LIMIT"


async def test_original_session_provenance_survives_read_expiry_but_not_reauthentication(
    client, session
):
    from datetime import timedelta

    owner, binding, wall, mono, _ = session
    client._require_event_session()
    wall[0] += timedelta(seconds=31)
    mono[0] += 31
    with pytest.raises(ValueError):
        owner.require_observation_binding(binding, scope=SCOPE)
    client._require_event_session()
    await owner.authenticate(
        expected_uid="12345", expected_parent_uid="0", expected_is_master=False
    )
    with pytest.raises(ValueError):
        client._require_event_session()
