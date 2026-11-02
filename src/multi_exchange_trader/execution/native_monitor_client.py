"""Genuine pinned Bybit execution lifecycle, with passive capability fences."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Callable
from typing import Any, NoReturn, cast

from nautilus_trader.adapters.bybit.config import BybitExecClientConfig
from nautilus_trader.adapters.bybit.execution import BybitExecutionClient
from nautilus_trader.adapters.bybit.providers import BybitInstrumentProvider
from nautilus_trader.common.config import InstrumentProviderConfig
from nautilus_trader.core import nautilus_pyo3 as p

from .account_binding import AuthenticatedAccountBinding, BybitDemoAccountSession
from .monitoring_budget import MonitoringReadBudget
from .monitoring_models import MonitoringError, MonitoringTarget, StreamStamp
from .native_monitor_http import MonitoringHttpAdapter
from .private_monitor_stream import OwnedPrivateMonitorStream
from .protection import ProtectionScope


def passive_node_settings(instruments: list[Any]) -> dict[str, Any]:
    from nautilus_trader.config import CacheConfig, LiveExecEngineConfig

    return {
        "cache": CacheConfig(database=None, flush_on_start=False),
        "save_state": False,
        "exec_engine": LiveExecEngineConfig(
            load_cache=False,
            reconciliation=False,
            reconciliation_instrument_ids=instruments,
            inflight_check_interval_ms=0,
            open_check_interval_secs=None,
            position_check_interval_secs=None,
            own_books_audit_interval_secs=None,
            filter_unclaimed_external_orders=True,
            filter_position_reports=True,
            generate_missing_orders=False,
            manage_own_order_books=False,
            snapshot_orders=False,
            snapshot_positions=False,
            snapshot_positions_interval_secs=None,
            purge_closed_orders_interval_mins=None,
            purge_closed_positions_interval_mins=None,
            purge_account_events_interval_mins=None,
            purge_from_database=False,
            external_clients=None,
        ),
    }


def validate_passive_config(config: Any) -> None:
    if (
        type(config) is not BybitExecClientConfig
        or config.environment != p.BybitEnvironment.DEMO
        or config.product_types != (p.BybitProductType.LINEAR,)
        or config.auto_repay_spot_borrows is not False
        or config.use_spot_position_reports is not False
        or config.use_ws_execution_fast
        or config.use_gtd
        or any(
            getattr(config, name) is not None
            for name in (
                "futures_leverages",
                "position_mode",
                "margin_mode",
                "base_url_http",
                "base_url_ws_private",
                "base_url_ws_trade",
                "proxy_url",
            )
        )
    ):
        raise MonitoringError("MONITORING_PASSIVE_CONFIG_REQUIRED")


def instrument_semantics(instrument: Any) -> dict[str, Any]:
    from nautilus_trader.model.instruments import CryptoPerpetual

    if type(instrument) is not CryptoPerpetual:
        raise MonitoringError("MONITORING_NATIVE_INSTRUMENT_TYPE")
    value: dict[str, Any] = instrument.to_dict(instrument)
    del value["ts_event"], value["ts_init"]
    if value.get("info") is None:
        value["info"] = {}
    for key in ("base_currency", "quote_currency", "settlement_currency"):
        currency = getattr(instrument, key)
        value[key] = tuple(
            str(getattr(currency, field))
            for field in ("code", "precision", "iso4217", "name", "currency_type")
        )
    return value


class _UnconnectedSocket:
    """Keep the real constructor-held SDK object, exposing local cleanup only."""

    def __init__(self, raw: Any, fault: Callable[[str], None]) -> None:
        self.__raw, self.__fault = raw, fault

    def __getattr__(self, name: str) -> Any:
        self.__fault("MONITORING_SPECIALIZED_SOCKET_DENIED")
        raise MonitoringError("MONITORING_SPECIALIZED_SOCKET_DENIED")

    def is_closed(self) -> bool:
        return bool(self.__raw.is_closed())

    async def close(self) -> None:
        await self.__raw.close()

    def cache_instrument(self, instrument: Any) -> None:
        if type(instrument) is not p.CryptoPerpetual:
            self.__fault("MONITORING_SPECIALIZED_CACHE_DENIED")
            raise MonitoringError("MONITORING_SPECIALIZED_CACHE_DENIED")
        self.__raw.cache_instrument(instrument)


class MonitoringBybitExecutionClient(BybitExecutionClient):
    def __init__(
        self,
        *,
        monitor_session: BybitDemoAccountSession,
        monitor_binding: AuthenticatedAccountBinding,
        **kwargs: Any,
    ) -> None:
        validate_passive_config(kwargs["config"])
        self._monitor_fault: str | None = None
        self._monitor_session = monitor_session
        self._monitor_binding = monitor_binding
        self._monitor_target: MonitoringTarget | None = None
        self._monitor_stream: OwnedPrivateMonitorStream | None = None
        self._monitor_reports: Any = None
        self._monitor_emit = False
        self._monitor_account_emit = False
        self._monitor_account_event: Any = None
        self._monitor_attestation = monitor_session._attested
        super().__init__(**kwargs)
        # No await has occurred: constructor-scheduled repayment cannot tick yet.
        self._ws_private_client = _UnconnectedSocket(self._ws_private_client, self._fault)  # type: ignore[assignment]  # Runtime duck-typed transport fence.
        self._ws_trade_client = _UnconnectedSocket(self._ws_trade_client, self._fault)  # type: ignore[assignment]

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        self._deny()

    @property
    def monitoring_fault(self) -> str | None:
        return self._monitor_fault or (self._monitor_stream.fault if self._monitor_stream else None)

    def _fault(self, reason: str) -> None:
        # Preserve the stream's original loss/fault evidence when stop follows.
        self._monitor_fault = self.monitoring_fault or reason
        if self._monitor_stream is not None:
            self._monitor_stream.retire(self._monitor_fault)

    def _deny(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._fault("MONITORING_CAPABILITY_DENIED")
        raise MonitoringError("MONITORING_CAPABILITY_DENIED")

    async def _deny_async(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._deny()

    async def _process_repayment_queues(self) -> None:
        # The base constructor always schedules this, even when repayment=False.
        return

    def monitoring_stamp(self) -> StreamStamp:
        return self._monitor_stream.stamp() if self._monitor_stream else StreamStamp(0, "", 0, 0)

    def _event_guard(self) -> None:
        if self.monitoring_fault or self._monitor_target is None:
            raise MonitoringError(self.monitoring_fault or "MONITORING_NOT_ARMED")
        witness = self._monitor_target._witness
        if witness is None:
            self._deny()
        witness.require()
        self._require_event_session()

    def _require_event_session(self) -> None:
        self._monitor_session._guard()
        # Provenance survives TTL expiry. Credential replacement/authentication
        # generation and exact owner changes do not; no read-budget expiry here.
        if (
            self._monitor_session._binding is not self._monitor_binding
            or self._monitor_attestation is None
            or self._monitor_session._attested is not self._monitor_attestation
        ):
            self._fault("MONITORING_SESSION_REPLACED")
            raise MonitoringError("MONITORING_SESSION_REPLACED")

    def arm_monitoring(self, target: MonitoringTarget) -> None:
        from .monitoring_reconciliation import require_current_target
        from .private_monitor_reports import OwnedPrivateReports

        if self._monitor_target is not None or self.monitoring_fault or self.is_connected:
            self._deny()
        require_current_target(target, self._monitor_session, "PRE")
        witness = target._witness
        assert witness is not None
        if witness._assembly.execution_client is not self:
            self._deny()
        # The worker refreshes A→B before PRE. Only the genuine target's exact
        # worker/session binding B can replace the constructor-time read handle.
        binding = witness._worker.binding
        self._monitor_session.require_observation_binding(binding, scope=target.scope)
        self._monitor_binding = binding
        self._http_client._binding = binding
        self._monitor_attestation = self._monitor_session._attested
        self._monitor_target = target
        self._monitor_reports = OwnedPrivateReports(self, target)
        self._monitor_stream = OwnedPrivateMonitorStream(
            api_key=self._config.api_key,
            api_secret=self._config.api_secret,
            guard=self._event_guard,
            dispatch=self._monitor_reports.dispatch,
        )

    async def _cache_instruments(self) -> None:
        self._event_guard()
        provider = self.bybit_instrument_provider
        assert self._monitor_target is not None
        expected = {symbol + "-LINEAR.BYBIT" for symbol in self._monitor_target.symbols}
        fresh = provider.get_all()
        if {str(key) for key in fresh} != expected:
            self._deny()
        for key, instrument in fresh.items():
            original = self._cache.instrument(key)
            if original is None or instrument_semantics(original) != instrument_semantics(
                instrument
            ):
                self._fault("MONITORING_INSTRUMENT_DRIFT")
                raise MonitoringError("MONITORING_INSTRUMENT_DRIFT")
        await super()._cache_instruments()

    async def _connect(self) -> None:
        try:
            self._event_guard()
            await self._instrument_provider.initialize()
            await self._cache_instruments()
            self._monitor_account_emit = True
            try:
                await self._update_account_state()
            finally:
                self._monitor_account_emit = False
            await self._await_account_registered()
            if self._monitor_account_event is None:
                raise MonitoringError("MONITORING_FRESH_ACCOUNT_ACK_REQUIRED")
            assert self._monitor_stream is not None
            budget = self._monitor_session._client._operation.get()
            if not isinstance(budget, MonitoringReadBudget):
                self._deny()
            import time

            await self._monitor_stream.connect(timeout=min(10, budget.deadline - time.monotonic()))
        except BaseException:
            self._fault("MONITORING_NATIVE_CONNECT_FAILED")
            raise

    async def _apply_account_configuration(self) -> None:
        if not self._monitor_account_emit:
            self._deny()
        validate_passive_config(self._config)

    async def _update_account_state(self) -> None:
        if not self._monitor_account_emit:
            self._deny()
        await super()._update_account_state()

    def generate_account_state(self, *args: Any, **kwargs: Any) -> None:
        if not self._monitor_account_emit:
            self._deny()
        super().generate_account_state(*args, **kwargs)

    def _send_account_state(self, account_state: Any) -> None:
        if not self._monitor_account_emit:
            self._deny()
        super()._send_account_state(account_state)
        # Portfolio.update_account is a synchronous MessageBus endpoint in the
        # pinned SDK. Verify the actual event, not mere account presence.
        account = self._cache.account(self.account_id)
        if (
            account is None
            or account.last_event is not account_state
            or account.base_currency is not None
            or any(
                account.balance(balance.currency) != balance for balance in account_state.balances
            )
            or any(
                account.margin_for_currency(margin.currency) != margin
                for margin in account_state.margins
            )
        ):
            self._fault("MONITORING_FRESH_ACCOUNT_NOT_APPLIED")
            raise MonitoringError("MONITORING_FRESH_ACCOUNT_NOT_APPLIED")
        self._monitor_account_event = account_state

    def _send_order_event(self, event: Any) -> None:
        if not self._monitor_emit:
            self._deny()
        self._event_guard()
        self._monitor_reports.expect_native(event)
        super()._send_order_event(event)

    def _emit_original(self, kind: str, fields: dict[str, Any]) -> None:
        self._event_guard()
        self._monitor_emit = True
        try:
            if kind == "fill":
                super().generate_order_filled(**fields)
            elif kind == "cancel":
                super().generate_order_canceled(**fields)
            else:
                self._deny()
        finally:
            self._monitor_emit = False

    def note_monitoring_native(self, event: object) -> None:
        if self._monitor_reports is not None:
            self._monitor_reports.note_native(event)

    def acknowledge_monitoring(self, event: object, *, evidence: object) -> None:
        if self._monitor_reports is not None:
            self._monitor_reports.acknowledge(event, evidence)

    async def wait_monitoring_ready(self) -> None:
        self._event_guard()
        assert self._monitor_stream is not None
        while not self.is_connected:
            self._event_guard()
            await asyncio.sleep(0.01)
        self._monitor_stream.require_ready()
        await self._monitor_stream.barrier()

    async def monitoring_barrier(self) -> StreamStamp:
        await self.wait_monitoring_ready()
        assert self._monitor_stream is not None
        return await self._monitor_stream.barrier()

    def retire_monitoring(self, reason: str) -> None:
        self._fault(reason)

    async def drain_monitoring(self) -> StreamStamp:
        """No admission guard: only already-posted exact native/app ACKs settle."""
        if self._monitor_reports is not None:
            await self._monitor_reports.drain_accepted()
        if self._monitor_stream is not None:
            return await self._monitor_stream.drain_accepted()
        return self.monitoring_stamp()

    async def _disconnect(self) -> None:
        self.retire_monitoring("MONITORING_STOPPED")
        async with asyncio.timeout(10):
            await self.drain_monitoring()
            if self._monitor_reports is not None:
                self._monitor_reports.retire()
            if self._monitor_stream is not None:
                await self._monitor_stream.close()
            await super()._disconnect()
            await self._repay_task


# All pinned financial/query/report/reconciliation entrypoints are denied before
# their bodies can log commands, schedule work, or manufacture local events.
for _name in (
    "submit_order",
    "submit_order_list",
    "modify_order",
    "cancel_order",
    "cancel_all_orders",
    "batch_cancel_orders",
    "query_account",
    "query_order",
    "_send_fill_report",
    "_send_order_status_report",
    "_send_position_status_report",
    "_send_mass_status_report",
    "_handle_msg",
    "_handle_account_state",
    "_handle_order_rejected_pyo3",
    "_handle_order_cancel_rejected_pyo3",
    "_handle_order_modify_rejected_pyo3",
    "_handle_order_status_report_pyo3",
    "_handle_fill_report_pyo3",
    "_handle_position_status_report_pyo3",
    "_cache_reconciliation_order_position_id",
    "_publish_margin_data",
    "generate_order_submitted",
    "generate_order_accepted",
    "generate_order_denied",
    "generate_order_rejected",
    "generate_order_canceled",
    "generate_order_expired",
    "generate_order_filled",
    "generate_order_updated",
    "generate_order_triggered",
    "generate_order_modify_rejected",
    "generate_order_cancel_rejected",
):
    setattr(MonitoringBybitExecutionClient, _name, MonitoringBybitExecutionClient._deny)
for _name in (
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
    "generate_order_status_report",
    "generate_order_status_reports",
    "generate_fill_reports",
    "generate_position_status_reports",
    "generate_mass_status",
    "set_leverage",
    "set_position_mode",
    "_apply_leverage_settings",
    "_apply_position_mode_settings",
    "_apply_margin_mode_setting",
    "_handle_borrow_action",
    "_handle_repay_action",
    "_handle_get_borrow_amount_action",
    "_repay_spot_borrow_if_needed",
):
    setattr(MonitoringBybitExecutionClient, _name, MonitoringBybitExecutionClient._deny_async)


def create_monitor_client(
    *,
    session: BybitDemoAccountSession,
    binding: AuthenticatedAccountBinding,
    scope: ProtectionScope,
    symbols: tuple[str, ...],
    **kwargs: Any,
) -> MonitoringBybitExecutionClient:
    validate_passive_config(kwargs["config"])
    holder: list[MonitoringBybitExecutionClient] = []

    def fault(reason: str) -> None:
        if holder:
            holder[0]._fault(reason)

    adapter = MonitoringHttpAdapter(
        session=session, binding=binding, scope=scope, symbols=symbols, fault=fault
    )
    InstrumentId = importlib.import_module("nautilus_trader.model.identifiers").InstrumentId
    provider = BybitInstrumentProvider(
        client=cast(Any, adapter),  # Pinned provider uses the reviewed duck-typed API.
        product_types=(p.BybitProductType.LINEAR,),
        config=InstrumentProviderConfig(
            load_all=False,
            load_ids=frozenset(InstrumentId.from_str(s + "-LINEAR.BYBIT") for s in symbols),
        ),
    )
    result = MonitoringBybitExecutionClient(
        monitor_session=session,
        monitor_binding=binding,
        client=adapter,
        instrument_provider=provider,
        **kwargs,
    )
    holder.append(result)
    return result
