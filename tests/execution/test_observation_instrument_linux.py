"""Six pinned real-SDK contracts; skips never qualify observation metadata.

The existing autouse fixture requires isolated Linux before any SDK import.
Only synthetic HTTP responses replace network I/O; original Session, budget,
native types and restart codec remain real. No node or exchange is started.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any, cast

import httpx
import pytest
from test_bybit_native_linux import isolated_native_runtime, module  # noqa: F401
from test_monitoring_receipts import SCOPE
from test_monitoring_receipts import session as session_fixture
from test_native_monitor_http import fee_row, instrument_row

from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.execution.monitoring_models import MonitoringError
from multi_exchange_trader.execution.native_monitor_http import (
    MonitoringHttpAdapter,
    convert_instrument,
)

session = session_fixture


def observation() -> Any:
    import importlib

    return importlib.import_module("multi_exchange_trader.execution.observation_instrument_v1")


def row() -> dict[str, Any]:
    return cast(Callable[[], dict[str, Any]], instrument_row)()


def cython(native: Any) -> Any:
    return module("model.instruments").instruments_from_pyo3([native])[0]


def test_actual_sdk_observation_marker_survives_native_cache_codec_roundtrip() -> None:
    from multi_exchange_trader.execution.native_restart_codec import (
        capture_native_cache,
        install_native_cache,
        prepare_native_cache,
    )
    from multi_exchange_trader.execution.native_restart_store import NativeRestartIdentity

    obs = observation()
    native = obs.convert_observation_instrument_v1(row(), "BTCUSDT", 1000)
    assert type(native) is module("core.nautilus_pyo3").CryptoPerpetual
    original = cython(native)
    obs.require_instrument_metadata_profile_v1(native, obs.SDK1231_OBSERVATION_UNKNOWN_FEES_V1)
    obs.require_instrument_metadata_profile_v1(original, obs.SDK1231_OBSERVATION_UNKNOWN_FEES_V1)
    assert original.maker_fee == original.taker_fee == Decimal("0.001")
    assert original.price_increment.as_decimal() == Decimal("0.10")
    assert original.size_increment.as_decimal() == Decimal("0.001")
    cache = module("cache.cache").Cache()
    cache.add_instrument(original)
    identity = NativeRestartIdentity(SCOPE, "BYBIT-UNIFIED", "TRADER-001", "STRATEGY-001",
                                    ("BTCUSDT-LINEAR.BYBIT",))
    payload = capture_native_cache(cache, identity=identity, owners=())
    prepared = prepare_native_cache(payload, identity=identity, owners=())
    restored = module("cache.cache").Cache()
    install_native_cache(restored, prepared)
    result = restored.instrument(original.id)
    obs.require_instrument_metadata_profile_v1(result, obs.SDK1231_OBSERVATION_UNKNOWN_FEES_V1)
    assert result.to_dict(result) == original.to_dict(original)
    assert result.info == {
        "monitoring_metadata_profile": "SDK1231_OBSERVATION_UNKNOWN_FEES_V1",
        "actual_account_fee_status": "UNKNOWN", "sdk_fee_convention": "0.001",
        "financial_use": "FORBIDDEN",
    }


async def test_original_session_observation_profile_never_requests_private_fees(
    session: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    obs = observation()
    owner, binding, wall, _mono, _requests = session
    paths: list[str] = []

    async def send(_client: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        paths.append(request.url.path)
        assert request.method == "GET" and request.url.host == "api-demo.bybit.com"
        assert dict(request.url.params) == {"category": "linear", "symbol": "BTCUSDT"}
        result = {"category": "linear", "list": [row()], "nextPageCursor": ""}
        return httpx.Response(200, request=request, json={
            "retCode": 10001 if request.url.path.endswith("fee-rate") else 0,
            "time": int(wall[0].timestamp() * 1000), "result": result,
        })

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    faults: list[str] = []
    adapter = MonitoringHttpAdapter(session=owner, binding=binding, scope=SCOPE,
        symbols=("BTCUSDT",), fault=faults.append,
        metadata_profile=obs.SDK1231_OBSERVATION_UNKNOWN_FEES_V1)
    budget = MonitoringReadBudget()
    with owner._monitoring_operation(budget):
        instruments = await adapter.request_instruments(module("core.nautilus_pyo3").BybitProductType.LINEAR,
                                                       "BTCUSDT")
    assert paths == ["/v5/market/instruments-info"]
    assert budget.physical_attempts == 1 and budget._terminal_failure is None and not faults
    obs.require_instrument_metadata_profile_v1(instruments[0], obs.SDK1231_OBSERVATION_UNKNOWN_FEES_V1)
    with pytest.raises(MonitoringError):
        adapter.place_order(symbol="BTCUSDT")


async def test_default_actual_fee_path_still_refuses_private_10001(
    session: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, binding, wall, _mono, _requests = session
    paths: list[str] = []

    async def send(_client: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, request=request, json={
            "retCode": 10001, "time": int(wall[0].timestamp() * 1000), "result": {},
        })

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    faults: list[str] = []
    adapter = MonitoringHttpAdapter(session=owner, binding=binding, scope=SCOPE,
                                   symbols=("BTCUSDT",), fault=faults.append)
    budget = MonitoringReadBudget()
    with owner._monitoring_operation(budget), pytest.raises(MonitoringError, match="^MONITORING_HTTP_FAILED$"):
        await adapter.request_instruments(module("core.nautilus_pyo3").BybitProductType.LINEAR, "BTCUSDT")
    assert paths == ["/v5/account/fee-rate"]
    assert budget._terminal_failure == "MONITORING_READ_FAILED"
    assert faults[-1] == "MONITORING_HTTP_FAILED"


async def test_observation_public_get_failure_keeps_terminal_budget_failure(
    session: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    obs = observation()
    owner, binding, _wall, _mono, _requests = session
    paths: list[str] = []

    async def send(_client: httpx.AsyncClient, request: httpx.Request, **_kwargs: Any) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(403, request=request, json={})

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    faults: list[str] = []
    adapter = MonitoringHttpAdapter(session=owner, binding=binding, scope=SCOPE,
        symbols=("BTCUSDT",), fault=faults.append,
        metadata_profile=obs.SDK1231_OBSERVATION_UNKNOWN_FEES_V1)
    budget = MonitoringReadBudget()
    with owner._monitoring_operation(budget), pytest.raises(MonitoringError, match="^MONITORING_HTTP_FAILED$"):
        await adapter.request_instruments(module("core.nautilus_pyo3").BybitProductType.LINEAR, "BTCUSDT")
    assert paths == ["/v5/market/instruments-info"]
    assert budget._terminal_failure == "MONITORING_QUOTA_OR_BAN"
    assert faults[-1] == "MONITORING_HTTP_FAILED"


async def test_observation_exhausted_physical_budget_sends_nothing(
    session: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    obs = observation()
    owner, binding, _wall, _mono, _requests = session

    async def forbidden(*_args: Any, **_kwargs: Any) -> httpx.Response:
        raise AssertionError("exhausted operation must not send")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    faults: list[str] = []
    adapter = MonitoringHttpAdapter(session=owner, binding=binding, scope=SCOPE,
        symbols=("BTCUSDT",), fault=faults.append,
        metadata_profile=obs.SDK1231_OBSERVATION_UNKNOWN_FEES_V1)
    budget = MonitoringReadBudget()
    budget.physical_attempts = 200
    with owner._monitoring_operation(budget), pytest.raises(MonitoringError, match="^MONITORING_HTTP_FAILED$"):
        await adapter.request_instruments(module("core.nautilus_pyo3").BybitProductType.LINEAR, "BTCUSDT")
    assert budget.physical_attempts == 200
    assert budget._terminal_failure == "MONITORING_PHYSICAL_ATTEMPTS"
    assert faults[-1] == "MONITORING_HTTP_FAILED"


def test_actual_native_types_reject_missing_mixed_and_corrupt_metadata_markers() -> None:
    obs = observation()
    expected = obs.SDK1231_OBSERVATION_UNKNOWN_FEES_V1
    unknown = cython(obs.convert_observation_instrument_v1(row(), "BTCUSDT", 1000))
    actual = cython(convert_instrument(row(), cast(Callable[[], dict[str, Any]], fee_row)(), "BTCUSDT", 1000))
    obs.require_instrument_metadata_profile_v1(actual, obs.ACTUAL_ACCOUNT_FEE_V1)
    for instrument, profile in ((unknown, obs.ACTUAL_ACCOUNT_FEE_V1), (actual, expected),
                                (object(), expected)):
        with pytest.raises(MonitoringError):
            obs.require_instrument_metadata_profile_v1(instrument, profile)
    for field in ("financial_use", "sdk_fee_convention", "monitoring_metadata_profile"):
        data = unknown.to_dict(unknown)
        data["info"] = dict(data["info"])
        del data["info"][field]
        damaged = module("model.instruments").CryptoPerpetual.from_dict(data)
        with pytest.raises(MonitoringError):
            obs.require_instrument_metadata_profile_v1(damaged, expected)
    data = unknown.to_dict(unknown)
    data["maker_fee"] = "0.0002"
    with pytest.raises(MonitoringError):
        obs.require_instrument_metadata_profile_v1(
            module("model.instruments").CryptoPerpetual.from_dict(data), expected)
    with pytest.raises(MonitoringError):
        for instrument in (unknown, actual):
            obs.require_instrument_metadata_profile_v1(instrument, expected)
    with pytest.raises(MonitoringError):
        obs.validate_metadata_profile_v1("unknown-profile")
