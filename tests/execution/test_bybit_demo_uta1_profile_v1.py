"""DEMO UTA1 data contract; original HTTPX sources and a genuine fixture journal.

Economic graph literals are synthetic data. They never attest an actual account.
"""

import importlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_bybit_linear_model_inputs_v1 import bind_d_graph, full_d_graph
from test_bybit_linear_own_pending_v1 import pending_graph
from test_private_account_sources_bundle_v1 import SCOPE, authenticate, intercepted_session

from multi_exchange_trader.execution import bybit_linear_model_inputs_v1 as a
from multi_exchange_trader.execution import bybit_linear_model_v1 as m
from multi_exchange_trader.execution.account_binding import AccountBindingError
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected


def api() -> Any:
    return importlib.import_module("multi_exchange_trader.execution.bybit_demo_uta1_profile_v1")


def venue3(venue: a.BybitVenueStateV1) -> Any:
    p = api()
    mode = p.BybitDemoUta1IsolatedModeV1(
        venue.account_mode.source, 3, "ISOLATED_MARGIN", venue.account_mode.updated_time_ms
    )
    return p.BybitDemoUta1VenueStateV1(
        mode,
        venue.flat_leverage,
        venue.fee,
        venue.wallet,
        venue.held_positions,
        venue.held_orders,
        venue.tier_tables,
        venue.selected_profile,
        venue.source_records,
        venue.valid_until,
    )


@pytest.mark.parametrize("margin", ["ISOLATED_MARGIN", "REGULAR_MARGIN"])
async def test_original_session_projects_actual_three_without_relaxing_old_mode(
    monkeypatch: pytest.MonkeyPatch,
    margin: str,
) -> None:
    session, _requests = intercepted_session(monkeypatch)
    original = httpx.AsyncClient.send

    async def send(
        client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        response = await original(client, request, **kwargs)
        if request.url.path == "/v5/account/info":
            body = json.loads(response.content)
            body["result"].update(unifiedMarginStatus=3, marginMode=margin)
            return httpx.Response(200, content=json.dumps(body).encode(), request=request)
        return response

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        binding = await authenticate(session)
        source = await session._capture_linear_account_info_v1(
            binding, scope=SCOPE, budget=MonitoringReadBudget(), owner_guard=lambda: None
        )
        project = getattr(session, "_project_linear_demo_uta1_isolated_mode_v1", None)
        assert callable(project), "Versioned original-session UTA1 projection is absent"
        with pytest.raises(AccountBindingError):
            session._project_linear_account_mode_v1(source, binding=binding, scope=SCOPE)
        if margin == "REGULAR_MARGIN":
            with pytest.raises(AccountBindingError):
                project(source, binding=binding, scope=SCOPE)
        else:
            value = project(source, binding=binding, scope=SCOPE)
            assert value.source is source and value.unified_margin_status == 3
            assert type(value) is api().BybitDemoUta1IsolatedModeV1
            await authenticate(session)
            with pytest.raises(AccountBindingError):
                project(source, binding=binding, scope=SCOPE)
    finally:
        await session.aclose()


def test_versioned_binding_and_actual_selector_reuse_economics_without_cross_profile_hash() -> None:
    graph = full_d_graph()
    old = bind_d_graph(graph)
    p = api()
    venue = venue3(old.venue)
    value = p.bind_bybit_demo_uta1_model_inputs_v1(
        graph["inputs"],
        preparation=graph["preparation"],
        domain=graph["domain"],
        venue=venue,
        catalog=graph["catalog"],
        selected_mark=graph["selected_mark"],
        now=graph["now"],
    )
    assert value.venue.account_mode.unified_margin_status == 3
    assert a._point_payload_v1(value.point) == a._point_payload_v1(old.point)
    assert p.bybit_demo_uta1_model_inputs_sha256_v1(value) != a.bybit_model_inputs_sha256_v1(old)
    calculation = p.calculate_bound_bybit_demo_uta1_model_v1(value, now=graph["now"])
    original = m.calculate_bound_bybit_model_v1(old, now=graph["now"])
    assert type(calculation) is type(original) is m.BybitBoundModelCalculationV1
    assert calculation.search == original.search
    assert calculation.model_input_sha256 != original.model_input_sha256
    with pytest.raises(RiskV2Rejected):
        a.BybitUtaModeV1(old.venue.account_mode.source, 3, "ISOLATED_MARGIN", 1)  # type: ignore[arg-type]
    with pytest.raises(RiskV2Rejected):
        m.calculate_bound_bybit_model_v1(value, now=graph["now"])


@pytest.mark.parametrize("failure", ["live", "regular", "lpa", "status"])
def test_new_profile_keeps_target_and_fixed_price_gates(failure: str) -> None:
    graph = full_d_graph()
    old = bind_d_graph(graph)
    p = api()
    with pytest.raises(RiskV2Rejected):
        if failure in ("live", "regular", "status"):
            source = old.venue.account_mode.source
            if failure == "live":
                source = replace(
                    source, account_scope=replace(source.account_scope, environment="LIVE")
                )
            p.BybitDemoUta1IsolatedModeV1(
                source,
                5 if failure == "status" else 3,
                "REGULAR_MARGIN" if failure == "regular" else "ISOLATED_MARGIN",
                1,
            )
        else:
            venue = venue3(old.venue)
            venue = replace(
                venue,
                fee=replace(venue.fee, behaviour=replace(venue.fee.behaviour, lpa_perp=False)),
            )
            p.bind_bybit_demo_uta1_model_inputs_v1(
                graph["inputs"],
                preparation=graph["preparation"],
                domain=graph["domain"],
                venue=venue,
                catalog=graph["catalog"],
                selected_mark=graph["selected_mark"],
                now=graph["now"],
            )


def test_genuine_pending_rebind_caps_original_hold_and_replay_requires_same_view(
    tmp_path: Path,
) -> None:
    graph = pending_graph(tmp_path)
    p = api()
    try:
        value = p.bind_bybit_demo_uta1_own_pending_inputs_v1(
            graph["inputs"],
            full_domain=graph["domain"],
            full_venue=venue3(graph["full_venue"]),
            catalog=graph["catalog"],
            selected_mark=graph["selected_mark"],
            journal=graph["journal"],
            view=graph["view"],
            command_key=graph["intent"].command_key,
            expected_command_identity_sha256=graph["identity"],
            now=graph["now"],
        )
        assert value.full_venue.account_mode.unified_margin_status == 3
        assert value.evaluation.venue.account_mode.unified_margin_status == 3
        assert value.point.capacity_quote <= value.original_reservation.margin_amount
        calculation = p.calculate_bybit_demo_uta1_own_pending_v1(
            value, journal=graph["journal"], view=graph["view"], now=graph["now"]
        )
        assert (
            p.replay_bybit_demo_uta1_own_pending_v1(
                calculation,
                inputs=value,
                journal=graph["journal"],
                view=graph["view"],
                now=graph["now"],
            )
            == calculation
        )
        with pytest.raises(RiskV2Rejected):
            graph["journal"].claim(graph["intent"].command_key)
        with pytest.raises(RiskV2Rejected):
            p.replay_bybit_demo_uta1_own_pending_v1(
                calculation,
                inputs=value,
                journal=graph["journal"],
                view=graph["view"],
                now=graph["now"],
            )
    finally:
        graph["journal"].close()
