"""Genuine local journal/source owners with intercepted fixture HTTP only."""

import importlib
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from multi_exchange_trader.execution import reservations_v2 as v
from multi_exchange_trader.execution.account_binding import (
    AccountBindingError,
    BybitDemoAccountSession,
)
from multi_exchange_trader.execution.journal import (
    CommandJournal,
    CommandKind,
    ExecutionScope,
    _OwnPendingViewV2,
)
from multi_exchange_trader.execution.monitoring_budget import (
    MonitoringBudgetError,
    MonitoringReadBudget,
)
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected

SOURCE = importlib.import_module("test_bybit_linear_source_assembler_v1")


def api() -> Any:
    return importlib.import_module("multi_exchange_trader.execution.bybit_linear_pending_coverage_v1")


def test_pending_coverage_owner_exists() -> None:
    assert callable(api().BybitLinearPendingCoverageOwnerV1)


class PendingFixture:
    """Only a genuine private owner and journal; no unnecessary public clients."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
        self.requests: list[httpx.Request] = []
        self.session = BybitDemoAccountSession(api_key="fixture-demo-key",
            api_secret="fixture-demo-secret", clock=self.clock, max_age=timedelta(minutes=5))
        self.journal = CommandJournal(path / "journal.sqlite3", ExecutionScope("12345", "DEMO"))
        self.journal.bind_owner_epoch_v2("OWNER", expected_previous_owner_epoch=None,
            expected_journal_revision=0)

        async def send(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
            assert type(client) is httpx.AsyncClient and request.method == "GET"
            assert request.url.host == "api-demo.bybit.com" and kwargs.get("stream") is True
            self.requests.append(request)
            result: dict[str, Any]
            if request.url.path == "/v5/market/time":
                result = {}
            elif request.url.path == "/v5/user/query-api":
                result = {"apiKey": "fixture-demo-key", "secret": "", "userID": 12345,
                    "parentUid": "56789", "isMaster": False, "readOnly": 0,
                    "permissions": {"ContractTrade": ["Order", "Position"], "Wallet": [],
                        "Derivatives": ["DerivativesTrade"], "Spot": []}}
            else:
                assert request.url.path in ("/v5/position/list", "/v5/order/realtime")
                result = {"category": "linear", "nextPageCursor": "", "list": []}
            body = {"retCode": 0, "result": result, "time": int(self.clock().timestamp() * 1000)}
            return httpx.Response(200, stream=SOURCE.Stream(json.dumps(body).encode()), request=request)

        monkeypatch.setattr(httpx.AsyncClient, "send", send)

    @staticmethod
    def clock() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def guard() -> None:
        pass

    async def auth(self) -> Any:
        return await self.session.authenticate(expected_uid="12345", expected_parent_uid="56789",
            expected_is_master=False)

    async def close(self) -> None:
        self.journal.close()
        await self.session.aclose()


async def pending_graph(monkeypatch: pytest.MonkeyPatch, path: Path,
                        *, retained_source: bool = False) -> dict[str, Any]:
    """Existing accepted synthetic PLAN factory; genuine journal and source owners."""
    if retained_source:
        def bounded_session(**kwargs: Any) -> BybitDemoAccountSession:
            # This test exercises ownership/composition, with the constructor's
            # existing supported bound. The independent expiry case uses 20s.
            kwargs["max_age"] = timedelta(minutes=5)
            return BybitDemoAccountSession(**kwargs)

        monkeypatch.setattr(SOURCE, "BybitDemoAccountSession", bounded_session)
    fixture = (importlib.import_module("test_bybit_linear_entry_producer_v1").SourceFixture(monkeypatch, path)
        if retained_source else PendingFixture(monkeypatch, path))
    binding = await fixture.auth()
    codec = importlib.import_module("test_risk_v2_codec")
    factories = importlib.import_module("test_reservations_v2")
    now = datetime.now(UTC)
    original_scope = codec.ProtectionScope
    monkeypatch.setattr(codec, "ProtectionScope", lambda account, environment: original_scope("12345", environment))
    monkeypatch.setattr(codec, "NOW", now)
    monkeypatch.setattr(codec, "EXPIRY", now + timedelta(minutes=5))
    monkeypatch.setattr(factories, "NOW", now)
    intent, policy, account, marks = factories.fixture_values(symbol="BTCUSDT")
    scope, held = intent.envelope.scope, factories.reserve(intent)
    identity = v.entry_command_identity_sha256_v2(scope.product_scope.account, "COMMAND", intent, held)
    journal = fixture.journal
    snapshot = journal.reservation_snapshot_v2(scope, now=now, owner_generation="OWNER", exclude=None)
    journal.register_entry_v2("COMMAND", intent, reservation=held,
        admission=v.ReservationAdmissionV2(scope.product_scope.account, "OWNER",
            snapshot.journal_revision, snapshot.held_quantity_sha256, identity, policy, account, marks, now))
    view = journal._own_pending_view_v2(scope, command_key="COMMAND", owner_epoch="OWNER",
        expected_command_identity_sha256=identity, now=now)
    owner = api().BybitLinearPendingCoverageOwnerV1(session=fixture.session, journal=journal,
        clock=fixture.clock, owner_guard=fixture.guard)
    return {"fixture": fixture, "binding": binding, "journal": journal, "owner": owner,
        "view": view, "identity": identity, "held": held, "scope": scope}


async def capture(graph: dict[str, Any], **changes: Any) -> Any:
    args = {"scope": graph["scope"], "owner_generation": "OWNER", "budget": MonitoringReadBudget(),
        "max_age": timedelta(minutes=5), "view": graph["view"], "command_key": "COMMAND",
        "expected_command_identity_sha256": graph["identity"]}
    args.update(changes)
    return await graph["owner"].capture_v1(graph["binding"], **args)


async def test_original_pending_pair_retains_full_hold_and_venue_zeroes_separately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    graph = await pending_graph(monkeypatch, tmp_path)
    try:
        fixture = graph["fixture"]
        before = len(fixture.requests)
        result = await capture(graph)
        assert graph["owner"].require_current_v1(result) is result
        assert (result.total_risk, result.total_margin, result.position_count) == (0, 0, 0)
        assert result.reservation == graph["held"]
        assert result.reservations.risk_amount == graph["held"].risk_amount > 0
        assert result.reservations.margin_amount == graph["held"].margin_amount > 0
        assert result.reservations.position_count == 1 and len(result.reservations.components) == 1
        assert result.reservations.excluded_command_key is None
        assert result.evaluation_reservations.excluded_command_key == "COMMAND"
        assert result.evaluation_reservations.components == ()
        assert result.evaluation_reservations.risk_amount == result.evaluation_reservations.margin_amount == 0
        slot = graph["journal"]._own_pending_slot_v2
        assert slot is not None
        assert result.reservation is slot[1].reservation
        assert result.reservations is slot[1].full
        assert result.evaluation_reservations is slot[1].evaluation
        assert result.valid_until <= slot[1].intent.envelope.valid_until
        assert [req.url.path for req in fixture.requests[before:]] == ["/v5/position/list", "/v5/order/realtime"]
        assert not hasattr(result, "financial_authority") and not hasattr(result, "daily_loss")
    finally:
        await graph["fixture"].close()


@pytest.mark.parametrize("fault", ["forged-view", "wrong-identity", "extra-command", "sending"])
async def test_forged_or_nonexclusive_or_claimed_pair_cannot_issue_coverage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str,
) -> None:
    graph = await pending_graph(monkeypatch, tmp_path)
    try:
        changes: dict[str, Any] = {}
        if fault == "forged-view":
            changes["view"] = _OwnPendingViewV2()
        elif fault == "wrong-identity":
            changes["expected_command_identity_sha256"] = "f" * 64
        elif fault == "extra-command":
            graph["journal"].register("EXTRA", CommandKind.CANCEL, {"fixture": True})
        else:
            data = graph["journal"]._own_pending_slot_v2[1]
            graph["journal"].claim_entry_v2("COMMAND", owner_epoch="OWNER",
                expected_journal_revision=data.full.journal_revision,
                expected_held_quantity_sha256=data.full.held_quantity_sha256,
                expected_command_identity_sha256=graph["identity"])
        before = len(graph["fixture"].requests)
        with pytest.raises(RiskV2Rejected):
            await capture(graph, **changes)
        assert graph["owner"]._current is None
        assert len(graph["fixture"].requests) == before
    finally:
        await graph["fixture"].close()


@pytest.mark.parametrize("fault", ["second-position-page", "orders"])
async def test_any_original_whole_venue_row_prevents_pending_flat_coverage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str,
) -> None:
    graph = await pending_graph(monkeypatch, tmp_path)
    send_original = httpx.AsyncClient.send

    async def send(client: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        response = await send_original(client, request, **kwargs)
        body = json.loads(await response.aread())
        if request.url.path == "/v5/position/list" and fault == "second-position-page":
            row = importlib.import_module("test_bybit_position_order_projections_v1").POSITION
            body["result"].update(list=[row] if "cursor" in request.url.params else [],
                nextPageCursor="" if "cursor" in request.url.params else "NEXT")
        if request.url.path == "/v5/order/realtime" and fault == "orders":
            body["result"]["list"] = [{"orderId": "not-empty"}]
        return httpx.Response(200, stream=SOURCE.Stream(json.dumps(body).encode()), request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        with pytest.raises(RiskV2Rejected, match="HELD_COVERAGE_REQUIRED"):
            await capture(graph)
        assert graph["owner"]._current is None
    finally:
        await graph["fixture"].close()


@pytest.mark.parametrize("fault", ["copy", "reauth", "new-view", "late-command"])
async def test_retired_owner_sources_or_true_journal_pair_reject_existing_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str,
) -> None:
    graph = await pending_graph(monkeypatch, tmp_path)
    try:
        result = await capture(graph)
        if fault == "copy":
            result = replace(result)
        elif fault == "reauth":
            await graph["fixture"].auth()
        elif fault == "new-view":
            graph["journal"]._own_pending_view_v2(graph["scope"], command_key="COMMAND", owner_epoch="OWNER",
                expected_command_identity_sha256=graph["identity"], now=graph["fixture"].clock())
        else:
            graph["journal"].register("LATE", CommandKind.CANCEL, {"fixture": True})
        with pytest.raises((RiskV2Rejected, AccountBindingError)) as caught:
            graph["owner"].require_current_v1(result)
        if fault in ("new-view", "late-command"):
            assert type(caught.value) is RiskV2Rejected
            assert str(caught.value) == "RISK_V2_LOCAL_ADMISSION_CONFLICT"
        assert graph["owner"]._current is None
    finally:
        await graph["fixture"].close()


async def test_retained_own_pending_assembler_reuses_same_original_pages_without_new_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    graph = await pending_graph(monkeypatch, tmp_path, retained_source=True)
    fixture, budget = graph["fixture"], MonitoringReadBudget()
    try:
        read = await fixture.assembler.capture_v1(graph["binding"], scope=graph["scope"],
            owner_generation="OWNER", nonvip_fee_basis=fixture.basis,
            nonvip_applicability_revision="fixture-fee-v1", budget=budget,
            max_age=timedelta(minutes=5), max_quote_age=timedelta(minutes=5),
            own_pending_view=graph["view"], own_pending_command_key="COMMAND",
            own_pending_identity_sha256=graph["identity"])
        source_slot = fixture.assembler._current
        assert source_slot is not None
        sources = dict(source_slot.sources)
        before = len(fixture.requests)
        with pytest.raises(RiskV2Rejected, match="SOURCE_MISMATCH"):
            await capture(graph, assembler=fixture.assembler, read=read)
        result = await capture(graph, assembler=fixture.assembler, read=read, budget=budget)
        assert graph["owner"].require_current_v1(result) is result
        assert fixture.assembler.require_current_v1(read) is read
        assert len(fixture.requests) == before
        assert result.positions_sources is sources["POSITIONS"]
        assert result.orders_sources is sources["ORDERS"]
        assert result.reservations is read.full_reservations
        assert result.evaluation_reservations is read.evaluation_reservations
        assert result.valid_until <= read.venue.valid_until
    finally:
        await fixture.close()


async def test_original_guard_advancing_clock_past_plan_expiry_rejects_existing_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    codec = importlib.import_module("test_risk_v2_codec")
    factories = importlib.import_module("test_reservations_v2")
    original_factory = factories.fixture_values
    offset = timedelta(0)
    armed = False
    expiry: datetime | None = None

    def values(*args: Any, **kwargs: Any) -> Any:
        monkeypatch.setattr(codec, "EXPIRY", datetime.now(UTC) + timedelta(seconds=20))
        return original_factory(*args, **kwargs)

    def clock() -> datetime:
        return datetime.now(UTC) + offset

    def guard() -> None:
        nonlocal offset
        if armed:
            assert expiry is not None
            offset = expiry + timedelta(seconds=1) - datetime.now(UTC)

    monkeypatch.setattr(factories, "fixture_values", values)
    monkeypatch.setattr(PendingFixture, "clock", staticmethod(clock))
    monkeypatch.setattr(PendingFixture, "guard", staticmethod(guard))
    graph = await pending_graph(monkeypatch, tmp_path)
    try:
        result = await capture(graph)
        expiry, armed = result.valid_until, True
        with pytest.raises(RiskV2Rejected, match="EXPIRED|LOCAL_ADMISSION_CONFLICT"):
            graph["owner"].require_current_v1(result)
        assert graph["owner"]._current is None
    finally:
        await graph["fixture"].close()


@pytest.mark.parametrize("fault", ["private-page", "assembler-retired", "terminal-budget"])
async def test_final_original_clock_cannot_mutate_validated_pages_or_retire_assembler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str,
) -> None:
    armed = False
    mutations = 0
    graph: dict[str, Any] = {}

    def clock() -> datetime:
        nonlocal armed, mutations
        frame = sys._getframe(1)
        if (armed and frame.f_code.co_name == "_require"
                and frame.f_globals["__name__"] == api().__name__):
            armed = False
            mutations += 1
            fixture = graph["fixture"]
            if fault == "assembler-retired":
                fixture.assembler._current = None
            elif fault == "terminal-budget":
                budget = fixture.session._linear_positions_current.pages[0].capture.budget
                with pytest.raises(MonitoringBudgetError, match="MONITORING_READ_FAILED"):
                    budget.read_failed()
            else:
                captured = fixture.session._linear_positions_current.pages[0].capture
                object.__setattr__(captured, "raw_body", captured.raw_body + b" ")
        return datetime.now(UTC)

    monkeypatch.setattr(PendingFixture, "clock", staticmethod(clock))
    monkeypatch.setattr(SOURCE.Fixture, "clock", staticmethod(clock))
    graph = await pending_graph(monkeypatch, tmp_path, retained_source=fault == "assembler-retired")
    fixture = graph["fixture"]
    try:
        if fault == "assembler-retired":
            budget = MonitoringReadBudget()
            read = await fixture.assembler.capture_v1(graph["binding"], scope=graph["scope"],
                owner_generation="OWNER", nonvip_fee_basis=fixture.basis,
                nonvip_applicability_revision="fixture-fee-v1", budget=budget,
                max_age=timedelta(minutes=5), max_quote_age=timedelta(minutes=5),
                own_pending_view=graph["view"], own_pending_command_key="COMMAND",
                own_pending_identity_sha256=graph["identity"])
            result = await capture(graph, assembler=fixture.assembler, read=read, budget=budget)
        else:
            result = await capture(graph)
        armed = True
        with pytest.raises(RiskV2Rejected, match="SOURCE_MISMATCH"):
            graph["owner"].require_current_v1(result)
        assert mutations == 1 and graph["owner"]._current is None
    finally:
        await fixture.close()
