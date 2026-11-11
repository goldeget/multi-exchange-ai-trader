"""Genuine source/publication/J owners; explicit synthetic economics, no order authority."""

import importlib
import json
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fixtures.strategy_publication_v1 import artifact
from test_bybit_linear_source_assembler_v1 import SCOPE, Fixture, Stream
from test_policy_sections import policy_fixture

from multi_exchange_trader.account.bybit_readonly import BybitReadError
from multi_exchange_trader.execution import bybit_linear_model_inputs_v1 as a
from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
from multi_exchange_trader.execution import linear_risk_v2 as r
from multi_exchange_trader.execution import risk_domain_v2 as d
from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.account_binding import AccountBindingError
from multi_exchange_trader.execution.monitoring_budget import MonitoringReadBudget
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected
from multi_exchange_trader.execution.runtime_policy import RuntimePolicyBook
from multi_exchange_trader.execution.strategy_publication_v1 import (
    StrategyArtifactBytesV1,
    StrategyPublicationOwnerV1,
)


def api() -> Any:
    return importlib.import_module("multi_exchange_trader.execution.bybit_linear_entry_producer_v1")


def book_for(path: Path) -> tuple[RuntimePolicyBook, StrategyPublicationOwnerV1]:
    book = RuntimePolicyBook(path / "policy.sqlite3")
    supplied = artifact()
    approval = json.loads(supplied.approval_record)
    approval.update(reference=asdict(SCOPE.strategy), permitted_instruments=[asdict(SCOPE.instrument)])
    publication = StrategyPublicationOwnerV1(book)
    publication.publish((StrategyArtifactBytesV1(supplied.implementation, supplied.configuration,
        json.dumps(approval).encode()),), expected_policy_revision=0, expected_generation=0)
    draft = policy_fixture()  # type: ignore[no-untyped-call]  # Original literal-only test factory.
    draft["scope"].update(account={"account_id": "12345", "environment": "DEMO"},
        allowed_instruments=[asdict(SCOPE.instrument)], selected_instruments=[asdict(SCOPE.instrument)])
    current = book.read()
    draft["strategy"] = current["policy"]["strategy"]
    draft["strategy"].update(allowed=[asdict(SCOPE.strategy)], selected=[asdict(SCOPE.strategy)])
    draft["risk_v2"].update(leverage_min="1", leverage_max="10", per_trade_loss="12",
        max_daily_loss="100", max_drawdown="100", max_total_risk="300", max_total_margin="1000",
        margin_allocation="60", margin_mode="ISOLATED", max_quote_age_seconds="5",
        max_evidence_age_seconds="30")
    draft["risk_v2"]["caps"].update(max_order_admission_notional="500",
        max_account_admission_notional="1000")
    book.save_v2(1, draft, expected_strategy_generation=1, policy_sections_version=2)
    return book, publication


class SourceFixture(Fixture):
    def __init__(self, monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
        self.mode = 5
        self.margin = "ISOLATED_MARGIN"
        self.lpa = True
        self.fee_error = False
        self.include_held_eth = False
        super().__init__(monkeypatch, path)

    async def send(self, client: httpx.AsyncClient, request: httpx.Request,
                   **kwargs: Any) -> httpx.Response:
        response = await super().send(client, request, **kwargs)
        body = json.loads(await response.aread())
        result = body["result"]
        if request.url.path == "/v5/account/info":
            result.update(unifiedMarginStatus=self.mode, marginMode=self.margin)
        elif request.url.path == "/v5/account/user-setting-config":
            result["lpaPerp"] = self.lpa
        elif request.url.path == "/v5/account/fee-rate" and self.fee_error:
            body.update(retCode=10001, result={})
        elif request.url.path == "/v5/account/wallet-balance" and not self.include_held_eth:
            result["list"][0]["coin"][0].update(totalPositionIM="0", totalOrderIM="0")
        elif (
            request.url.path == "/v5/order/realtime"
            or (request.url.path == "/v5/position/list" and "symbol" not in request.url.params)
        ) and not self.include_held_eth:
            result["list"] = []
        return httpx.Response(200, stream=Stream(json.dumps(body).encode()), request=request)


def economic_inputs(read: Any, book: RuntimePolicyBook, now: Any, *, assembler: Any,
                    scope: Any = SCOPE,
                    side: str = "LONG", entry_price: Decimal = Decimal(50),
                    stop_price: Decimal = Decimal(40), lifetime_seconds: int = 20) -> tuple[Any, Any]:
    """Supplied fixture-only coverage/cost/minimum values never qualify an actual ledger."""
    policy = book.risk_policy_v2(scope)
    instrument = next(o for o in read.catalog.observations if o.key == scope.instrument)
    source = read.venue.wallet.source
    account_ref = c.RiskObservationRefV2(source.observation_id, source.source_id,
        source.source_environment, "explicit-account-fixture-v1", now, now, source.raw_body_sha256)
    account = c.LinearAccountRiskSnapshotV2(scope.product_scope.account, account_ref,
        "explicit-coverage-fixture-v1", (), "UTC_REALIZED_NET_FEES_FUNDING", now.date(),
        Decimal(1000), Decimal(1000), Decimal(0), Decimal(0), 0, Decimal(0), Decimal(0), 0,
        "ISOLATED", "ONE_WAY", ())
    tiers = a.project_bybit_tier_snapshot_v1(read.venue.tier_tables[0], instrument=instrument)
    member = d.LinearRiskDomainMemberV2(tiers_pin := read.venue.tier_tables[0].instrument_pin,
        read.marks[0], tiers, (), (), Decimal(0), Decimal(0), 0, Decimal(0), Decimal(0), 0,
        account_ref, c.RiskComputationRefV2("fixture-domain-risk", "fixture-risk-v1", "DEMO", now, "a" * 64))
    assert tiers_pin.key == scope.instrument
    domain = d.build_linear_risk_domain_coverage_v2(policy=policy, catalog=read.catalog,
        catalog_revision=read.catalog.revision, coverage_revision="fixture-domain-v1",
        source_generation="OWNER", account=account, reservations=read.full_reservations,
        marks=read.marks, members=(member,),
        source_record_sha256s=tuple(sorted(a.bybit_source_ref_sha256_v1(s)
            for s in read.venue.source_records)), now=now)
    expiry = min(read.venue.valid_until, now + timedelta(seconds=lifetime_seconds))
    proposal = c.LinearEntryProposalV2(scope, side, stop_price,
        c.FixedLimitPriceV2("BUY" if side == "LONG" else "SELL", entry_price, "fixture-quote", expiry))
    fee = read.venue.fee.account_taker_rate.reference.source
    fee_ref = c.RiskObservationRefV2(fee.observation_id, fee.source_id, "DEMO",
        "fixture-fee-v1", fee.received_at, fee.received_at, fee.raw_body_sha256)
    funding = c.RiskObservationRefV2("fixture-funding", "fixture-funding", "FIXTURE",
        "fixture-funding-v1", now, now, "b" * 64)
    last_trade_quote = assembler.issue_current_last_trade_quote_v2(read)
    costs = c.LinearCostAllowanceV2(scope,
        c.RiskComputationRefV2("fixture-cost", "fixture-cost-v1", "DEMO", now, "c" * 64),
        c.risk_policy_sha256_v2(policy), instrument.specification_revision, "fixture-quote", side,
        entry_price, stop_price, fee_ref, "USDT", "fixture-fee-v1", funding, Decimal(0),
        now + timedelta(hours=1), now + timedelta(hours=2), "fixture-funding-v1", c.STOP_GAP_MODEL_REVISION_V2,
        Decimal(1), Decimal("0.5"), Decimal("0.5"), expiry, "fixture-cost-v1")
    sizing = r.size_linear_entry_v2(policy=policy, proposal=proposal, instrument=instrument,
        catalog_revision=read.catalog.revision, costs=costs, now=now)
    minimum = e.MinimumAmountAssessmentV2(scope,
        c.RiskComputationRefV2("fixture-minimum", "fixture-minimum-v1", "DEMO", now, "d" * 64),
        c.envelope_sha256_v2(sizing.envelope), instrument.specification_revision,
        "fixture-minimum-v1", ("instrument", last_trade_quote.reference.observation_id),
        last_trade_quote.last_price, Decimal(5),
        sizing.envelope.base_quantity * last_trade_quote.last_price, expiry)
    requirements = e.RiskRequirementsV2("fixture-mapping-v1", "fixture-minimum-v1", "fixture-cost-v1",
        "fixture-fee-v1", "fixture-funding-v1", c.STOP_GAP_MODEL_REVISION_V2, "LINEAR_MARK_GROSS_UPPER_BOUND_V1",
        "LOCAL_ZERO_ORIGIN_LATTICE_V1", "fixture-margin-v1", "fixture-lp-v1",
        "POINT_ESTIMATE_WITH_RECONCILIATION_V1", "POINT_ESTIMATE_TWO_IM_ENVELOPE_V1",
        "APP_RESERVE_CEILING_QUANTUM_V1", Decimal("0.00000001"))
    return e.LinearRiskInputsV2(policy, proposal, instrument, read.catalog.revision, costs, account,
        read.full_reservations, read.marks, tiers, d.risk_domain_sha256_v2(domain), minimum,
        requirements, last_trade_quote), domain


@pytest.mark.parametrize("mode", [5, 3])
async def test_original_source_policy_and_read_are_checked_but_horizon_keeps_plan_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: int,
) -> None:
    fixture = SourceFixture(monkeypatch, tmp_path)
    fixture.mode = mode
    book, _publication = book_for(tmp_path)
    try:
        binding = await fixture.auth()
        capture = fixture.assembler.capture_demo_uta1_v1 if mode == 3 else fixture.assembler.capture_v1
        read = await capture(binding, scope=SCOPE, owner_generation="OWNER", nonvip_fee_basis=fixture.basis,
            nonvip_applicability_revision="fixture-fee-v1", budget=MonitoringReadBudget(),
            max_age=timedelta(seconds=30), max_quote_age=timedelta(seconds=5))
        inputs, domain = economic_inputs(read, book, fixture.clock(), assembler=fixture.assembler)
        assert inputs.last_trade_quote.last_price == Decimal(50)
        assert inputs.minimum_amount.evaluation_price == inputs.last_trade_quote.last_price
        owner = api().BybitLinearEntryProducerV1(assembler=fixture.assembler, policy_book=book)
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_HOLDING_HORIZON_REQUIRED$"):
            owner.prepare_plan_v1(read=read, inputs=inputs, domain=domain,
                policy_revision=2, publication_generation=1)
        assert owner._current is None
        assert not fixture.journal.list_commands()
    finally:
        await fixture.close()


async def test_bybit_entry_owner_blocks_inflated_minimum_without_horizon_or_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fixture = SourceFixture(monkeypatch, tmp_path)
    book, _publication = book_for(tmp_path)
    try:
        binding = await fixture.auth()
        read = await fixture.assembler.capture_v1(
            binding, scope=SCOPE, owner_generation="OWNER", nonvip_fee_basis=fixture.basis,
            nonvip_applicability_revision="fixture-fee-v1", budget=MonitoringReadBudget(),
            max_age=timedelta(seconds=30), max_quote_age=timedelta(seconds=5),
        )
        inputs, domain = economic_inputs(read, book, fixture.clock(), assembler=fixture.assembler)
        forged_minimum = replace(inputs.minimum_amount, evaluation_price=Decimal(200),
            evaluated_quote_amount=inputs.minimum_amount.evaluated_quote_amount * Decimal(4))
        forged_inputs = replace(inputs, minimum_amount=forged_minimum,
            last_trade_quote=replace(inputs.last_trade_quote, last_price=Decimal(200)))
        owner = api().BybitLinearEntryProducerV1(assembler=fixture.assembler, policy_book=book)
        before_commands = fixture.journal.list_commands()
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_HOLDING_HORIZON_REQUIRED$"):
            owner.prepare_plan_v1(
                read=read, inputs=forged_inputs, domain=domain,
                policy_revision=2, publication_generation=1,
            )
        assert owner._current is None
        assert fixture.journal.list_commands() == before_commands == ()
    finally:
        await fixture.close()


async def test_caller_low_costs_and_fake_minimum_cannot_override_missing_horizon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fixture = SourceFixture(monkeypatch, tmp_path)
    book, _publication = book_for(tmp_path)
    try:
        binding = await fixture.auth()
        read = await fixture.capture(binding)
        inputs, domain = economic_inputs(read, book, fixture.clock(), assembler=fixture.assembler)
        low_cost = replace(inputs.costs, fee_quote_per_base=Decimal("0.00000001"),
            funding_quote_per_base=Decimal("0.00000001"))
        fake_minimum = replace(inputs.minimum_amount, minimum_quote_amount=Decimal("0.01"))
        caller_inputs = replace(inputs, costs=low_cost, minimum_amount=fake_minimum)
        owner = api().BybitLinearEntryProducerV1(assembler=fixture.assembler, policy_book=book)
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_HOLDING_HORIZON_REQUIRED$"):
            owner.prepare_plan_v1(read=read, inputs=caller_inputs, domain=domain,
                policy_revision=2, publication_generation=1)
        assert owner._current is None and not fixture.journal.list_commands()
    finally:
        await fixture.close()


async def test_distinct_ltp_limit_and_mark_remain_blocked_without_horizon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fixture = SourceFixture(monkeypatch, tmp_path)
    fixture.mark_price = "140"
    fixture.last_trade_price = "95"
    book, _publication = book_for(tmp_path)
    try:
        binding = await fixture.auth()
        read = await fixture.assembler.capture_v1(
            binding, scope=SCOPE, owner_generation="OWNER", nonvip_fee_basis=fixture.basis,
            nonvip_applicability_revision="fixture-fee-v1", budget=MonitoringReadBudget(),
            max_age=timedelta(seconds=30), max_quote_age=timedelta(seconds=5),
        )
        inputs, domain = economic_inputs(
            read, book, fixture.clock(), assembler=fixture.assembler,
            entry_price=Decimal(100), stop_price=Decimal(90),
        )
        mark = next(mark for mark in inputs.marks if mark.instrument == SCOPE.instrument)
        assert inputs.last_trade_quote.last_price == Decimal(95)
        assert inputs.proposal.entry.limit_price == Decimal(100)
        assert mark.mark_price == Decimal(140)
        owner = api().BybitLinearEntryProducerV1(assembler=fixture.assembler, policy_book=book)
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_HOLDING_HORIZON_REQUIRED$"):
            owner.prepare_plan_v1(read=read, inputs=inputs, domain=domain,
                policy_revision=2, publication_generation=1)
        assert owner._current is None
        assert not fixture.journal.list_commands()
    finally:
        await fixture.close()


@pytest.mark.parametrize("fault", ["policy", "approval", "source", "journal-data", "read-copy"])
async def test_current_owner_evidence_is_checked_before_the_horizon_blocker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str,
) -> None:
    fixture = SourceFixture(monkeypatch, tmp_path)
    book, publication = book_for(tmp_path)
    try:
        binding = await fixture.auth()
        read = await fixture.capture(binding)
        inputs, domain = economic_inputs(read, book, fixture.clock(), assembler=fixture.assembler)
        owner = api().BybitLinearEntryProducerV1(assembler=fixture.assembler, policy_book=book)
        if fault == "journal-data":
            inputs = replace(inputs, reservations=replace(inputs.reservations, snapshot_id="not-original"))
            with pytest.raises(RiskV2Rejected, match="RISK_V2_SOURCE_MISMATCH"):
                owner.prepare_plan_v1(read=read, inputs=inputs, domain=domain,
                    policy_revision=2, publication_generation=1)
            return
        if fault == "read-copy":
            with pytest.raises(RiskV2Rejected, match="RISK_V2_SOURCE_MISMATCH"):
                owner.prepare_plan_v1(read=replace(read), inputs=inputs, domain=domain,
                    policy_revision=2, publication_generation=1)
            return
        if fault == "policy":
            book.save_v2(2, book.read()["policy"], expected_strategy_generation=1, policy_sections_version=2)
        elif fault == "approval":
            publication.revoke(SCOPE.strategy, expected_policy_revision=2, expected_generation=1)
        else:
            await fixture.auth()
        with pytest.raises((ValueError, BybitReadError, RiskV2Rejected)):
            owner.prepare_plan_v1(read=read, inputs=inputs, domain=domain,
                policy_revision=2, publication_generation=1)
        assert owner._current is None and not fixture.journal.list_commands()
    finally:
        await fixture.close()


@pytest.mark.parametrize("fault", ["cross", "price-correction", "fee-10001"])
async def test_actual_demo_unready_wire_is_not_promoted_to_model_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str,
) -> None:
    fixture = SourceFixture(monkeypatch, tmp_path)
    fixture.mode = 3
    if fault == "cross":
        fixture.margin = "REGULAR_MARGIN"
    elif fault == "price-correction":
        fixture.lpa = False
    else:
        fixture.fee_error = True
    book, _publication = book_for(tmp_path)
    owner = api().BybitLinearEntryProducerV1(assembler=fixture.assembler, policy_book=book)
    try:
        binding = await fixture.auth()
        with pytest.raises((AccountBindingError, BybitReadError, RiskV2Rejected)) as error:
            await fixture.assembler.capture_demo_uta1_v1(binding, scope=SCOPE, owner_generation="OWNER",
                nonvip_fee_basis=fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
                budget=MonitoringReadBudget(), max_age=timedelta(seconds=30),
                max_quote_age=timedelta(seconds=5))
        if fault == "fee-10001":
            assert type(error.value) is RiskV2Rejected
            assert error.value.code == "RISK_V2_SOURCE_READ_FAILED"
        assert owner._current is None and not fixture.journal.list_commands()
    finally:
        await fixture.close()


async def test_original_physical_budget_exhaustion_keeps_closed_risk_rejection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fixture = SourceFixture(monkeypatch, tmp_path)
    try:
        binding = await fixture.auth()
        budget = MonitoringReadBudget()
        for _ in range(200):
            budget.before_attempt(1)
        with pytest.raises(RiskV2Rejected, match="^RISK_V2_SOURCE_READ_BUDGET_EXHAUSTED$"):
            await fixture.assembler.capture_v1(binding, scope=SCOPE, owner_generation="OWNER",
                nonvip_fee_basis=fixture.basis, nonvip_applicability_revision="fixture-fee-v1",
                budget=budget, max_age=timedelta(seconds=30), max_quote_age=timedelta(seconds=5))
        assert len(fixture.requests) == 2 and fixture.assembler._current is None
        assert budget.physical_attempts == 200 and not fixture.journal.list_commands()
    finally:
        await fixture.close()


