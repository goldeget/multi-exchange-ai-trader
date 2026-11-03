"""Synthetic true-journal own-PENDING accounting; no execution qualification."""

import copy
import hashlib
import importlib
import importlib.util
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

import pytest
from test_bybit_linear_model_inputs_v1 import bind_d_graph, full_d_graph

from multi_exchange_trader.execution import bybit_linear_model_inputs_v1 as a
from multi_exchange_trader.execution import bybit_linear_model_v1 as m
from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
from multi_exchange_trader.execution import linear_risk_v2 as r
from multi_exchange_trader.execution import reservations_v2 as v
from multi_exchange_trader.execution import risk_domain_v2 as d
from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.journal import CommandJournal, ExecutionScope
from multi_exchange_trader.execution.protection import ProtectionPolicy
from multi_exchange_trader.execution.risk_v2_codec import DurableEntryIntentV2
from multi_exchange_trader.execution.risk_v2_math import decimal_fraction_v2, finite_decimal_v2
from multi_exchange_trader.market_data.instrument_catalog import build_instrument_catalog


def adapter() -> Any:
    name = "multi_exchange_trader.execution.bybit_linear_own_pending_v1"
    assert importlib.util.find_spec(name) is not None, "own-PENDING builder must exist"
    value = importlib.import_module(name)
    assert callable(getattr(value, "bind_bybit_own_pending_inputs_v1", None))
    return value


def rebuild(
    graph: dict[str, Any], inputs: Any, members: Any, *, prepare: bool = True
) -> dict[str, Any]:
    old = graph["domain"]
    domain = d.build_linear_risk_domain_coverage_v2(
        policy=inputs.policy,
        catalog=graph["catalog"],
        catalog_revision=inputs.catalog_revision,
        coverage_revision=old.coverage_revision,
        source_generation=old.source_generation,
        account=inputs.account,
        reservations=inputs.reservations,
        marks=inputs.marks,
        members=members,
        source_record_sha256s=old.source_record_sha256s,
        now=graph["now"],
    )
    inputs = replace(inputs, risk_domain_sha256=d.risk_domain_sha256_v2(domain))
    result = dict(graph, inputs=inputs, domain=domain)
    if prepare:
        result["preparation"] = r.prepare_linear_entry_v2(inputs, now=graph["now"])
    return result


def reservation(intent: DurableEntryIntentV2) -> v.ReservationV2:
    required = intent.assessment.required_reservation_quote
    assert required is not None
    return v.ReservationV2(
        2,
        intent.envelope.scope.instrument,
        c.envelope_sha256_v2(intent.envelope),
        intent.envelope.base_quantity,
        intent.assessment.preparation.sizing.estimated_loss_quote,
        required,
    )


def plan_intent(graph: dict[str, Any], command: str) -> tuple[Any, Any, Any]:
    bound = bind_d_graph(graph)
    calculation = m.calculate_bound_bybit_model_v1(bound, now=graph["now"])
    assert type(calculation) is m.BybitBoundModelCalculationV1
    margin = m.build_bybit_margin_assessment_v1(
        calculation,
        inputs=bound,
        reference=c.RiskComputationRefV2(
            "margin-" + command, "margin-v1", "FIXTURE", graph["now"], calculation.binding_sha256
        ),
        now=graph["now"],
    )
    liquidation = m.build_bybit_liquidation_assessment_v1(
        calculation,
        inputs=bound,
        margin=margin,
        reference=c.RiskComputationRefV2(
            "lp-" + command, "lp-v1", "FIXTURE", graph["now"], e.margin_assessment_sha256_v2(margin)
        ),
        now=graph["now"],
    )
    assessment = r.assess_entry_v2(
        bound.risk_inputs,
        now=graph["now"],
        stage="PLAN",
        margin=margin,
        liquidation=liquidation,
        leverage_readback=None,
    )
    intent = DurableEntryIntentV2(
        2,
        command,
        "POSITION-" + command,
        "PLAN-" + command,
        bound.risk_inputs.policy.scope.strategy,
        bound.point.envelope,
        assessment,
        ProtectionPolicy(
            bound.point.envelope.policy_revision,
            0,
            bound.point.stop_price,
            Decimal(120),
            "MARK_PRICE",
        ),
        graph["domain"].source_record_sha256s,
    )
    return intent, bound, calculation


def field_value(field: Any, value: str, *, source: Any = None) -> Any:
    return a.BybitDecimalFieldV1(
        Decimal(value),
        replace(
            field.reference,
            source=field.reference.source if source is None else source,
            wire_spelling=value,
        ),
    )


def retune(
    graph: dict[str, Any],
    *,
    policy_changes: Any = None,
    cost: str | None = None,
    mark: str | None = None,
    available: str | None = None,
    risk: str | None = None,
    prepare: bool = True,
) -> dict[str, Any]:
    """Explicit coherent synthetic changes, always rebuilt by the old owners."""
    inputs = graph["inputs"]
    policy = replace(inputs.policy, **(policy_changes or {}))
    costs = replace(inputs.costs, policy_sha256=c.risk_policy_sha256_v2(policy))
    if cost is not None:
        costs = replace(
            costs,
            fee_quote_per_base=Decimal(cost),
            funding_quote_per_base=Decimal(0),
            stop_gap_quote_per_base=Decimal(0),
        )
    marks = inputs.marks
    members = graph["domain"].members
    fields = list(graph["venue_fields"])
    selected_mark = graph["selected_mark"]
    if mark is not None:
        marks = tuple(
            replace(item, mark_price=Decimal(mark))
            if item.instrument == policy.scope.instrument
            else item
            for item in marks
        )
        members = tuple(replace(item, mark=marks[index]) for index, item in enumerate(members))
        selected_mark = field_value(selected_mark, mark)
    account = inputs.account
    if available is not None:
        account = replace(account, available_margin=Decimal(available))
        total = sum(
            (
                decimal_fraction_v2(getattr(fields[3], name).value)
                for name in ("total_position_im", "total_order_im", "locked", "bonus")
            ),
            decimal_fraction_v2(Decimal(0)),
        )
        balance = finite_decimal_v2(decimal_fraction_v2(Decimal(available)) + total)
        fields[3] = replace(
            fields[3], wallet_balance=field_value(fields[3].wallet_balance, format(balance, "f"))
        )
    if risk is not None:
        account = replace(account, total_risk=Decimal(risk))
        members = tuple(
            replace(item, account_risk_quote=Decimal(risk)) if item.account_component_ids else item
            for item in members
        )
    sizing = r.size_linear_entry_v2(
        policy=policy,
        proposal=inputs.proposal,
        instrument=inputs.instrument,
        catalog_revision=inputs.catalog_revision,
        costs=costs,
        now=graph["now"],
    )
    minimum = replace(
        inputs.minimum_amount,
        envelope_sha256=c.envelope_sha256_v2(sizing.envelope),
        evaluated_quote_amount=finite_decimal_v2(
            decimal_fraction_v2(sizing.envelope.base_quantity)
            * decimal_fraction_v2(inputs.minimum_amount.evaluation_price)
        ),
    )
    inputs = replace(
        inputs, policy=policy, costs=costs, account=account, marks=marks, minimum_amount=minimum
    )
    return rebuild(
        dict(graph, venue_fields=tuple(fields), selected_mark=selected_mark),
        inputs,
        members,
        prepare=prepare,
    )


def register_intent(journal: CommandJournal, graph: dict[str, Any], intent: Any) -> Any:
    account = intent.envelope.scope.product_scope.account
    snapshot = journal.reservation_snapshot_v2(
        intent.envelope.scope, now=graph["now"], owner_generation="OWNER_1", exclude=None
    )
    held = reservation(intent)
    identity = v.entry_command_identity_sha256_v2(account, intent.command_key, intent, held)
    return journal.register_entry_v2(
        intent.command_key,
        intent,
        reservation=held,
        admission=v.ReservationAdmissionV2(
            account,
            "OWNER_1",
            snapshot.journal_revision,
            snapshot.held_quantity_sha256,
            identity,
            graph["inputs"].policy,
            graph["inputs"].account,
            graph["inputs"].marks,
            graph["now"],
        ),
    )


def genuine_other_graph(graph: dict[str, Any], journal: CommandJournal) -> dict[str, Any]:
    """Actual existing M2 PLAN for Y, Q1/risk3/reserve20; no invented command record."""
    inputs = graph["inputs"]
    member = graph["domain"].members[1]
    key = member.instrument_pin.key
    scope = replace(inputs.policy.scope, instrument=key)
    policy = replace(
        inputs.policy,
        scope=scope,
        per_trade_loss=Decimal(3),
        leverage_max=Decimal(3),
        margin_allocation=Decimal(20),
    )
    proposal = replace(
        inputs.proposal,
        scope=scope,
        stop_price=Decimal(53),
        entry=replace(inputs.proposal.entry, limit_price=Decimal(54)),
    )
    instrument = next(item for item in graph["catalog"].observations if item.key == key)
    costs = replace(
        inputs.costs,
        scope=scope,
        policy_sha256=c.risk_policy_sha256_v2(policy),
        instrument_revision=instrument.specification_revision,
        entry_limit_price=Decimal(54),
        stop_price=Decimal(53),
    )
    account = replace(
        inputs.account,
        components=(),
        total_risk=Decimal(0),
        total_margin=Decimal(0),
        position_count=0,
    )
    snapshot = journal.reservation_snapshot_v2(
        scope, now=graph["now"], owner_generation="OWNER_1", exclude=None
    )
    member = replace(
        member,
        account_component_ids=(),
        reservation_component_ids=(),
        account_risk_quote=Decimal(0),
        account_margin_quote=Decimal(0),
        account_position_count=0,
        reserved_risk_quote=Decimal(0),
        reserved_margin_quote=Decimal(0),
        reserved_position_count=0,
    )
    sizing = r.size_linear_entry_v2(
        policy=policy,
        proposal=proposal,
        instrument=instrument,
        catalog_revision=inputs.catalog_revision,
        costs=costs,
        now=graph["now"],
    )
    minimum = replace(
        inputs.minimum_amount,
        scope=scope,
        envelope_sha256=c.envelope_sha256_v2(sizing.envelope),
        instrument_revision=instrument.specification_revision,
    )
    old_fields = graph["venue_fields"]

    def source(value: Any) -> Any:
        return replace(value, query=(("category", "linear"), ("symbol", key.symbol)))

    old_profile = old_fields[7]
    profile_source = source(old_profile.source_records[0])
    profile = replace(
        old_profile,
        symbol=key.symbol,
        selected_position_quantity=field_value(
            old_profile.selected_position_quantity, "0", source=profile_source
        ),
        auto_add_margin=replace(old_profile.auto_add_margin, source=profile_source),
        source_records=(profile_source,),
    )
    fee_source = source(old_fields[2].account_taker_rate.reference.source)
    fee = replace(
        old_fields[2],
        account_taker_rate=field_value(
            old_fields[2].account_taker_rate, "0.00055", source=fee_source
        ),
        account_maker_rate=field_value(old_fields[2].account_maker_rate, "0", source=fee_source),
    )
    flat_source = source(old_fields[1].source)
    flat = replace(
        old_fields[1],
        source=flat_source,
        symbol=key.symbol,
        leverage=field_value(old_fields[1].leverage, "2", source=flat_source),
    )
    raw_mark_source = replace(
        source(graph["selected_mark"].reference.source),
        observation_id=member.mark.reference.observation_id,
    )
    mark = field_value(graph["selected_mark"], "50", source=raw_mark_source)
    wallet = old_fields[3]
    for name in ("total_position_im", "total_order_im", "locked", "bonus"):
        wallet = replace(wallet, **{name: field_value(getattr(wallet, name), "0")})
    wallet = replace(wallet, wallet_balance=field_value(wallet.wallet_balance, "500"))
    table = old_fields[6][1]
    sources = tuple(
        sorted(
            (
                old_fields[0].source,
                fee.behaviour.source,
                wallet.source,
                fee_source,
                flat_source,
                profile_source,
                raw_mark_source,
                *table.pages,
            ),
            key=lambda item: item.observation_id,
        )
    )
    fields = (old_fields[0], flat, fee, wallet, (), (), (table,), profile, sources, old_fields[9])
    inputs = replace(
        inputs,
        policy=policy,
        proposal=proposal,
        instrument=instrument,
        costs=costs,
        account=account,
        reservations=snapshot,
        marks=(member.mark,),
        tiers=member.tiers,
        minimum_amount=minimum,
    )
    return rebuild(dict(graph, venue_fields=fields, selected_mark=mark), inputs, (member,))


def pending_graph(
    tmp_path: Path,
    *,
    other: bool = False,
    policy_changes: Any = None,
    mark: str | None = None,
    risk: str | None = None,
    initial_y_mark: str | None = None,
    prepare_full: bool = True,
) -> dict[str, Any]:
    graph = full_d_graph()
    account = graph["inputs"].account.account
    journal = CommandJournal(
        tmp_path / "journal.db", ExecutionScope(account.account_id, account.environment)
    )
    journal.bind_owner_epoch_v2(
        "OWNER_1", expected_previous_owner_epoch=None, expected_journal_revision=0
    )
    snapshot = journal.reservation_snapshot_v2(
        graph["inputs"].policy.scope, now=graph["now"], owner_generation="OWNER_1", exclude=None
    )
    members = tuple(
        replace(
            member,
            reservation_component_ids=(),
            reserved_risk_quote=Decimal(0),
            reserved_margin_quote=Decimal(0),
            reserved_position_count=0,
        )
        for member in graph["domain"].members
    )
    graph = rebuild(graph, replace(graph["inputs"], reservations=snapshot), members)
    if other:
        other_graph = genuine_other_graph(graph, journal)
        other_intent = plan_intent(other_graph, "OTHER")[0]
        assert reservation(other_intent).risk_amount == Decimal(3)
        assert reservation(other_intent).margin_amount == Decimal(20)
        other_record = register_intent(journal, other_graph, other_intent)
        snapshot = journal.reservation_snapshot_v2(
            graph["inputs"].policy.scope, now=graph["now"], owner_generation="OWNER_1", exclude=None
        )
        members = tuple(
            replace(
                member,
                reservation_component_ids=(other_record.client_order_id,),
                reserved_risk_quote=Decimal(3),
                reserved_margin_quote=Decimal(20),
                reserved_position_count=1,
            )
            if member.instrument_pin.key == other_intent.envelope.scope.instrument
            else member
            for member in members
        )
        graph = rebuild(graph, replace(graph["inputs"], reservations=snapshot), members)
    if initial_y_mark is not None:
        mark_y = replace(graph["inputs"].marks[1], mark_price=Decimal(initial_y_mark))
        members = (graph["domain"].members[0], replace(graph["domain"].members[1], mark=mark_y))
        fields = list(graph["venue_fields"])
        fields[4] = (
            replace(fields[4][0], mark_price=field_value(fields[4][0].mark_price, initial_y_mark)),
        )
        graph = rebuild(
            dict(graph, venue_fields=tuple(fields)),
            replace(graph["inputs"], marks=(graph["inputs"].marks[0], mark_y)),
            members,
        )
    graph = retune(graph, policy_changes=policy_changes, mark=mark, risk=risk)
    intent, bound, calculation = plan_intent(graph, "COMMAND")
    held = reservation(intent)
    record = register_intent(journal, graph, intent)
    identity = v.entry_command_identity_sha256_v2(account, intent.command_key, intent, held)
    view = journal._own_pending_view_v2(
        intent.envelope.scope,
        command_key=intent.command_key,
        owner_epoch="OWNER_1",
        expected_command_identity_sha256=identity,
        now=graph["now"],
    )
    data = journal._require_own_pending_view_v2(
        view,
        scope=intent.envelope.scope,
        command_key=intent.command_key,
        expected_command_identity_sha256=identity,
        now=graph["now"],
    )
    members = tuple(
        replace(
            member,
            reservation_component_ids=(record.client_order_id,),
            reserved_risk_quote=held.risk_amount,
            reserved_margin_quote=held.margin_amount,
            reserved_position_count=1,
        )
        if member.instrument_pin.key == held.instrument
        else member
        for member in graph["domain"].members
    )
    full = rebuild(
        graph, replace(graph["inputs"], reservations=data.full), members, prepare=prepare_full
    )
    venue = a.BybitVenueStateV1(*graph["venue_fields"])
    venue = replace(
        venue,
        selected_profile=replace(
            venue.selected_profile, local_unresolved_entry_ids=(record.client_order_id,)
        ),
    )
    return dict(
        full,
        journal=journal,
        view=view,
        intent=intent,
        identity=identity,
        original_bound=bound,
        original_calculation=calculation,
        full_venue=venue,
        data=data,
    )


def bind(graph: dict[str, Any], **changes: Any) -> Any:
    args = {
        "full_domain": graph["domain"],
        "full_venue": graph["full_venue"],
        "catalog": graph["catalog"],
        "selected_mark": graph["selected_mark"],
        "journal": graph["journal"],
        "view": graph["view"],
        "command_key": graph["intent"].command_key,
        "expected_command_identity_sha256": graph["identity"],
        "now": graph["now"],
    }
    inputs = changes.pop("full_inputs", graph["inputs"])
    args.update(changes)
    return adapter().bind_bybit_own_pending_inputs_v1(inputs, **args)


def test_true_pending_is_rejected_by_old_profile_and_bound_by_new_adapter(tmp_path: Path) -> None:
    graph = pending_graph(tmp_path)
    try:
        with pytest.raises(c.RiskV2Rejected, match="UNSUPPORTED_MODE"):
            a.bind_bybit_model_inputs_v1(
                graph["inputs"],
                preparation=graph["preparation"],
                domain=graph["domain"],
                venue=graph["full_venue"],
                catalog=graph["catalog"],
                selected_mark=graph["selected_mark"],
                now=graph["now"],
            )
        result = bind(graph)
        assert result.full_risk_inputs.reservations.position_count == 1
        assert result.evaluation.risk_inputs.reservations.position_count == 0
        assert result.original_reservation.margin_amount == Decimal("48.66666667")
        assert result.point.capacity_quote == Decimal("48.66666667")
        assert result.evaluation.point.capacity_quote == Decimal(60)
    finally:
        graph["journal"].close()


def test_other_true_m2_pending_and_held_y_survive_exact_subtraction(tmp_path: Path) -> None:
    graph = pending_graph(tmp_path, other=True)
    try:
        value = bind(graph)
        assert value.full_risk_inputs.reservations.risk_amount == Decimal(15)
        assert value.full_risk_inputs.reservations.margin_amount == Decimal("68.66666667")
        assert value.full_risk_inputs.reservations.position_count == 2
        assert value.evaluation.risk_inputs.reservations.risk_amount == Decimal(3)
        assert value.evaluation.risk_inputs.reservations.margin_amount == Decimal(20)
        assert value.evaluation.risk_inputs.reservations.position_count == 1
        assert value.evaluation.domain.members[1] is graph["domain"].members[1]
        assert value.evaluation.venue.held_positions is graph["full_venue"].held_positions
        assert value.evaluation.venue.tier_tables is graph["full_venue"].tier_tables
        assert value.evaluation.venue.source_records is graph["full_venue"].source_records
        full = value.full_risk_inputs.reservations
        evaluation = value.evaluation.risk_inputs.reservations
        assert full.held_quantity_sha256 == evaluation.held_quantity_sha256
        assert full.journal_revision == evaluation.journal_revision == 3
        assert full.owner_generation == evaluation.owner_generation == "OWNER_1"
        assert full.observed_at == evaluation.observed_at == graph["now"]
        assert full.snapshot_id != evaluation.snapshot_id
        assert evaluation.components[0].instrument.symbol == "YBBUSDT"
        assert (
            evaluation.components[0].component_id == graph["journal"].get("OTHER").client_order_id
        )
        assert value.evaluation.point.capacity_quote == Decimal(60)
        assert value.point.capacity_quote == Decimal("48.66666667")
    finally:
        graph["journal"].close()


@pytest.mark.parametrize("cap,accepted", [("240", True), ("239", False)])
def test_all_held_mark_gross_counts_own_once(tmp_path: Path, cap: str, accepted: bool) -> None:
    # Account Y2*50 + original own X1*140 = 240; no second proposed own hold.
    graph = pending_graph(
        tmp_path,
        policy_changes={
            "caps": c.LinearCapitalCapsV2(
                Decimal(500),
                Decimal(cap),
                "LINEAR_MARK_GROSS_AT_ADMISSION_V1",
                "NOT_REQUIRED",
                None,
            )
        },
        initial_y_mark=None if accepted else "49.5",
        prepare_full=False,
    )
    try:
        if accepted:
            value = bind(graph)
            assert value.evaluation.preparation.valuation.combined_gross_quote == Decimal(240)
        else:
            # Original cap239 admitted Y99+own140; fresh Y100+own140 now refuses.
            mark_y = replace(graph["inputs"].marks[1], mark_price=Decimal(50))
            member_y = replace(graph["domain"].members[1], mark=mark_y)
            graph = rebuild(
                graph,
                replace(graph["inputs"], marks=(graph["inputs"].marks[0], mark_y)),
                (graph["domain"].members[0], member_y),
                prepare=False,
            )
            graph["full_venue"] = replace(
                graph["full_venue"],
                held_positions=(
                    replace(
                        graph["full_venue"].held_positions[0],
                        mark_price=field_value(
                            graph["full_venue"].held_positions[0].mark_price, "50"
                        ),
                    ),
                ),
            )
            assert cap == "239"
            with pytest.raises(c.RiskV2Rejected, match="ACCOUNT_ADMISSION_LIMIT"):
                bind(graph)
    finally:
        graph["journal"].close()


def test_original_risk17_refuses_naive_fresh16_without_releasing_hold(tmp_path: Path) -> None:
    graph = pending_graph(
        tmp_path,
        other=True,
        risk="0",
        policy_changes={"max_total_risk": Decimal(16)},
        prepare_full=False,
    )
    try:
        graph = retune(graph, cost="1", risk="2", prepare=False)
        # Fresh preparation itself admits2+3+11=16 with unchanged Q1.
        data = graph["data"]
        evaluation_inputs = replace(graph["inputs"], reservations=data.evaluation)
        members = tuple(
            replace(
                item,
                reservation_component_ids=(),
                reserved_risk_quote=Decimal(0),
                reserved_margin_quote=Decimal(0),
                reserved_position_count=0,
            )
            if item.instrument_pin.key == data.reservation.instrument
            else item
            for item in graph["domain"].members
        )
        fresh = rebuild(graph, evaluation_inputs, members)
        assert fresh["preparation"].sizing.estimated_loss_quote == Decimal(11)
        assert fresh["preparation"].sizing.envelope == data.intent.envelope
        before = tuple(graph["journal"]._db.iterdump())
        with pytest.raises(c.RiskV2Rejected, match="TOTAL_RISK_LIMIT"):
            bind(graph)
        assert tuple(graph["journal"]._db.iterdump()) == before
        assert graph["journal"].get("COMMAND").reservation_v2.risk_amount == Decimal(12)
    finally:
        graph["journal"].close()


def test_fresh_loss_above_original_hold_refuses_under_unchanged_affordable_policy(
    tmp_path: Path,
) -> None:
    # Existing quantity step.1: budget13.1 keeps Q1 for both loss12 and13.
    graph = pending_graph(
        tmp_path, policy_changes={"per_trade_loss": Decimal("13.1")}, prepare_full=False
    )
    try:
        assert graph["data"].reservation.risk_amount == Decimal(12)
        graph = retune(graph, cost="3", prepare=False)
        assert graph["inputs"].policy.per_trade_loss > Decimal(13)
        fresh = r.size_linear_entry_v2(
            policy=graph["inputs"].policy,
            proposal=graph["inputs"].proposal,
            instrument=graph["inputs"].instrument,
            catalog_revision=graph["inputs"].catalog_revision,
            costs=graph["inputs"].costs,
            now=graph["now"],
        )
        assert fresh.estimated_loss_quote == Decimal(13)
        assert c._envelope_payload_v2(fresh.envelope) == c._envelope_payload_v2(
            graph["intent"].envelope
        )
        with pytest.raises(c.RiskV2Rejected, match="LOCAL_ADMISSION_CONFLICT"):
            bind(graph)
    finally:
        graph["journal"].close()


def test_original_margin60_plus_other20_refuses75_despite_fresh50(tmp_path: Path) -> None:
    graph = pending_graph(tmp_path, other=True, mark="174", prepare_full=False)
    try:
        assert graph["data"].reservation.margin_amount == Decimal(60)
        graph = retune(graph, cost="1", mark="147", available="75", prepare=False)
        data = graph["data"]
        members = tuple(
            replace(
                item,
                reservation_component_ids=(),
                reserved_risk_quote=Decimal(0),
                reserved_margin_quote=Decimal(0),
                reserved_position_count=0,
            )
            if item.instrument_pin.key == data.reservation.instrument
            else item
            for item in graph["domain"].members
        )
        fresh = rebuild(graph, replace(graph["inputs"], reservations=data.evaluation), members)
        venue = a.BybitVenueStateV1(*fresh["venue_fields"])
        bound = a.bind_bybit_model_inputs_v1(
            fresh["inputs"],
            preparation=fresh["preparation"],
            domain=fresh["domain"],
            venue=venue,
            catalog=fresh["catalog"],
            selected_mark=fresh["selected_mark"],
            now=fresh["now"],
        )
        calculation = m.calculate_bound_bybit_model_v1(bound, now=fresh["now"])
        assert type(calculation) is m.BybitBoundModelCalculationV1
        assert calculation.search.selected.reserve.required_reservation_quote == Decimal(50)
        assert bound.preparation.capacity_quote == Decimal(55)
        # Use the genuinely current wallet/source fields, preserve own client mapping.
        graph["full_venue"] = replace(venue, selected_profile=graph["full_venue"].selected_profile)
        with pytest.raises(c.RiskV2Rejected, match="MARGIN_LIMIT"):
            bind(graph)
        assert graph["journal"].get("COMMAND").reservation_v2.margin_amount == Decimal(60)
    finally:
        graph["journal"].close()


@pytest.mark.parametrize(
    "kind", ["member", "duplicate", "raw", "tier", "mark", "risk", "margin", "count"]
)
def test_missing_or_substituted_held_y_is_refused(tmp_path: Path, kind: str) -> None:
    graph = pending_graph(tmp_path, other=True)
    try:
        domain, venue, inputs = graph["domain"], graph["full_venue"], graph["inputs"]
        if kind == "member":
            domain = replace(domain, members=domain.members[:1])
        elif kind == "duplicate":
            domain = replace(domain, members=domain.members + (domain.members[1],))
        elif kind == "raw":
            venue = replace(venue, held_positions=())
        elif kind == "tier":
            venue = replace(venue, tier_tables=venue.tier_tables[:1])
        elif kind == "mark":
            inputs = replace(inputs, marks=inputs.marks[:1])
        else:
            name = {
                "risk": "reserved_risk_quote",
                "margin": "reserved_margin_quote",
                "count": "reserved_position_count",
            }[kind]
            domain = replace(
                domain,
                members=(
                    domain.members[0],
                    replace(domain.members[1], **{name: 0 if kind == "count" else Decimal(0)}),
                ),
            )
        with pytest.raises(c.RiskV2Rejected):
            bind(graph, full_inputs=inputs, full_domain=domain, full_venue=venue)
    finally:
        graph["journal"].close()


@pytest.mark.parametrize("ids", [(), ("COMMAND",), ("borrowed",), ("borrowed", "other")])
def test_only_original_pending_client_mapping_is_accepted(tmp_path: Path, ids: Any) -> None:
    graph = pending_graph(tmp_path)
    try:
        venue = replace(
            graph["full_venue"],
            selected_profile=replace(
                graph["full_venue"].selected_profile, local_unresolved_entry_ids=ids
            ),
        )
        with pytest.raises(c.RiskV2Rejected, match="LOCAL_ADMISSION_CONFLICT"):
            bind(graph, full_venue=venue)
    finally:
        graph["journal"].close()


@pytest.mark.parametrize(
    "kind", ["copy-journal", "wrong-view", "retired-view", "identity", "closed", "excluded-full"]
)
def test_wrong_or_consumed_original_view_never_becomes_model_authority(
    tmp_path: Path, kind: str
) -> None:
    graph = pending_graph(tmp_path)
    try:
        args: dict[str, Any] = {}
        if kind == "copy-journal":
            args["journal"] = copy.copy(graph["journal"])
        elif kind == "wrong-view":
            args["view"] = object()
        elif kind == "retired-view":
            graph["journal"]._own_pending_view_v2(
                graph["inputs"].policy.scope,
                command_key="COMMAND",
                owner_epoch="OWNER_1",
                expected_command_identity_sha256=graph["identity"],
                now=graph["now"],
            )
        elif kind == "identity":
            args["expected_command_identity_sha256"] = "f" * 64
        elif kind == "closed":
            graph["journal"].close()
        else:
            args["full_inputs"] = replace(graph["inputs"], reservations=graph["data"].evaluation)
        with pytest.raises(c.RiskV2Rejected, match="LOCAL_ADMISSION_CONFLICT"):
            bind(graph, **args)
    finally:
        graph["journal"].close()


def test_final_original_view_revalidation_detects_actual_failed_claim_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = pending_graph(tmp_path)
    original = a.bind_bybit_model_inputs_v1

    def retire(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        snapshot = graph["data"].full
        with pytest.raises(c.RiskV2Rejected):
            graph["journal"].claim_entry_v2(
                "COMMAND",
                owner_epoch="OWNER_1",
                expected_journal_revision=snapshot.journal_revision,
                expected_held_quantity_sha256=snapshot.held_quantity_sha256,
                expected_command_identity_sha256="f" * 64,
            )
        return result

    monkeypatch.setattr(a, "bind_bybit_model_inputs_v1", retire)
    try:
        with pytest.raises(c.RiskV2Rejected, match="LOCAL_ADMISSION_CONFLICT"):
            bind(graph)
        assert graph["journal"].get("COMMAND").state.value == "PENDING"
    finally:
        graph["journal"].close()


def test_new_hash_has_independent_complete_outer_recipe_and_original_archive(
    tmp_path: Path,
) -> None:
    graph = pending_graph(tmp_path, other=True)
    try:
        value = bind(graph)
        fields = {
            "profile_revision": "BYBIT_USDT_LINEAR_ISOLATED_ONE_WAY_SELECTED_FLAT_OWN_PENDING_V1",
            "projection_revision": "EXACT_JOURNAL_OWN_PENDING_SUBTRACTION_V1",
            "command_key": "COMMAND",
            "client_order_id": graph["data"].record.client_order_id,
            "position_id": "POSITION-COMMAND",
            "command_identity_sha256": graph["identity"],
            "archived_intent_sha256": hashlib.sha256(
                graph["data"].record.payload_json.encode()
            ).hexdigest(),
            "original_reservation": v._reservation_payload(graph["data"].reservation),
            "full_risk_inputs": e._inputs_payload_v2(graph["inputs"]),
            "full_domain_sha256": d.risk_domain_sha256_v2(graph["domain"]),
            "full_venue_sha256": a.bybit_venue_state_sha256_v1(graph["full_venue"]),
            "evaluation_model_sha256": a.bybit_model_inputs_sha256_v1(value.evaluation),
            "point_input_sha256": a.bybit_point_input_sha256_v1(value.point),
            "valid_until": "2026-11-03T00:00:01.000000+00:00",
            "model_rule_revision": "BYBIT_LINEAR_OWN_PENDING_MODEL_V1",
        }
        wire = json.dumps(
            {
                "schema": "RISK_V2_CANONICAL_V1",
                "record_type": "BybitOwnPendingModelInputsV1",
                "fields": fields,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        assert (
            adapter().bybit_own_pending_model_inputs_sha256_v1(value)
            == hashlib.sha256(wire).hexdigest()
        )
        old_point = a.bybit_point_input_sha256_v1(value.point)
        domain = replace(graph["domain"], source_record_sha256s=("b" * 64,))
        changed = dict(
            graph,
            domain=domain,
            inputs=replace(graph["inputs"], risk_domain_sha256=d.risk_domain_sha256_v2(domain)),
        )
        new = bind(changed)
        assert a.bybit_point_input_sha256_v1(new.point) == old_point
        assert (
            adapter().bybit_own_pending_model_inputs_sha256_v1(new)
            != hashlib.sha256(wire).hexdigest()
        )
    finally:
        graph["journal"].close()


def test_builder_rechecks_original_view_twice_without_sql_writes_or_timestamp_renewal(
    tmp_path: Path,
) -> None:
    graph = pending_graph(tmp_path)
    journal = graph["journal"]
    try:
        before = tuple(journal._db.iterdump())
        changes = journal._db.total_changes
        statements: list[str] = []
        journal._db.set_trace_callback(statements.append)
        try:
            first = bind(graph)
        finally:
            journal._db.set_trace_callback(None)
        assert statements.count("BEGIN IMMEDIATE") == statements.count("COMMIT") == 2
        assert tuple(journal._db.iterdump()) == before
        assert journal._db.total_changes == changes
        later = bind(graph, now=graph["now"] + timedelta(microseconds=1))
        assert later.full_risk_inputs.reservations.observed_at == graph["now"]
        assert later.evaluation.risk_inputs.reservations.observed_at == graph["now"]
        assert adapter().bybit_own_pending_model_inputs_sha256_v1(
            later
        ) == adapter().bybit_own_pending_model_inputs_sha256_v1(first)
    finally:
        journal.close()


def test_full_domain_cannot_shift_original_own_risk_to_other_member(tmp_path: Path) -> None:
    graph = pending_graph(tmp_path, other=True)
    try:
        # Header still totals15, but own12 cannot be disguised as11 while other3 becomes4.
        domain = replace(
            graph["domain"],
            members=(
                replace(graph["domain"].members[0], reserved_risk_quote=Decimal(11)),
                replace(graph["domain"].members[1], reserved_risk_quote=Decimal(4)),
            ),
        )
        inputs = replace(graph["inputs"], risk_domain_sha256=d.risk_domain_sha256_v2(domain))
        with pytest.raises(c.RiskV2Rejected, match="LOCAL_ADMISSION_CONFLICT"):
            bind(graph, full_inputs=inputs, full_domain=domain)
    finally:
        graph["journal"].close()


@pytest.mark.parametrize(
    "kind", ["cost-quantity", "limit", "stop", "expiry", "origin", "pin", "quantity-representation"]
)
def test_original_complete_fixed_request_never_resizes_reprices_or_renews(
    tmp_path: Path, kind: str
) -> None:
    graph = pending_graph(tmp_path)
    try:
        inputs = graph["inputs"]
        if kind == "cost-quantity":
            graph = retune(graph, cost="3", prepare=False)
        elif kind == "quantity-representation":
            full = inputs.reservations
            component = replace(full.components[0], base_quantity=Decimal("1.0"))
            graph["inputs"] = replace(inputs, reservations=replace(full, components=(component,)))
        else:
            if kind == "pin":
                inputs = replace(
                    inputs,
                    instrument=replace(
                        inputs.instrument,
                        source=replace(
                            inputs.instrument.source,
                            received_at=graph["now"] - timedelta(microseconds=1),
                        ),
                    ),
                )
            else:
                proposal, costs = inputs.proposal, inputs.costs
                if kind == "limit":
                    proposal = replace(
                        proposal, entry=replace(proposal.entry, limit_price=Decimal("100.1"))
                    )
                    costs = replace(costs, entry_limit_price=Decimal("100.1"))
                elif kind == "stop":
                    proposal, costs = (
                        replace(proposal, stop_price=Decimal("90.1")),
                        replace(costs, stop_price=Decimal("90.1")),
                    )
                elif kind == "expiry":
                    proposal = replace(
                        proposal,
                        entry=replace(
                            proposal.entry,
                            valid_until=proposal.entry.valid_until + timedelta(microseconds=1),
                        ),
                    )
                else:
                    proposal = replace(
                        proposal, entry=replace(proposal.entry, origin_quote_id="OTHER_QUOTE")
                    )
                    costs = replace(costs, origin_quote_id="OTHER_QUOTE")
                inputs = replace(inputs, proposal=proposal, costs=costs)
            graph = retune(dict(graph, inputs=inputs), prepare=False)
        with pytest.raises(c.RiskV2Rejected):
            bind(graph)
    finally:
        graph["journal"].close()


@pytest.mark.parametrize("kind", ["stale-cost", "future-cost", "exclusive-expiry"])
def test_original_source_age_and_exclusive_expiry_remain_enforced(
    tmp_path: Path, kind: str
) -> None:
    graph = pending_graph(tmp_path)
    try:
        if kind == "exclusive-expiry":
            with pytest.raises(c.RiskV2Rejected):
                bind(graph, now=graph["intent"].envelope.valid_until)
        else:
            delta = timedelta(seconds=-11) if kind == "stale-cost" else timedelta(microseconds=1)
            original = graph["inputs"].costs
            changes = {"reference": replace(original.reference, computed_at=graph["now"] + delta)}
            if kind == "stale-cost":
                changes.update(
                    fee_reference=replace(
                        original.fee_reference,
                        server_observed_at=graph["now"] + delta,
                        received_at=graph["now"] + delta,
                    ),
                    funding_reference=replace(
                        original.funding_reference,
                        server_observed_at=graph["now"] + delta,
                        received_at=graph["now"] + delta,
                    ),
                )
            costs = replace(original, **changes)
            with pytest.raises(
                c.RiskV2Rejected,
                match="STALE_EVIDENCE" if kind == "stale-cost" else "FUTURE_EVIDENCE",
            ):
                bind(graph, full_inputs=replace(graph["inputs"], costs=costs))
    finally:
        graph["journal"].close()


@pytest.mark.parametrize(
    "name",
    [
        "full_inputs",
        "full_domain",
        "full_venue",
        "catalog",
        "selected_mark",
        "command_key",
        "expected_command_identity_sha256",
        "now",
    ],
)
def test_closed_input_shapes_refuse_before_journal_io(tmp_path: Path, name: str) -> None:
    graph = pending_graph(tmp_path)
    journal = graph["journal"]
    statements: list[str] = []
    journal._db.set_trace_callback(statements.append)
    try:
        with pytest.raises(c.RiskV2Rejected, match="INPUT_INVALID"):
            bind(graph, **{name: object()})
        assert statements == []
    finally:
        journal._db.set_trace_callback(None)
        journal.close()


def test_hash_constructor_rejects_closed_scalar_and_derived_point_substitutions(
    tmp_path: Path,
) -> None:
    graph = pending_graph(tmp_path)
    try:
        value = bind(graph)
        for field, bad in (
            ("command_key", True),
            ("profile_revision", "BORROWED"),
            ("archived_intent_sha256", "bad"),
            ("original_reservation", object()),
        ):
            with pytest.raises(c.RiskV2Rejected):
                replace(value, **{field: bad})
        with pytest.raises(c.RiskV2Rejected, match="LOCAL_ADMISSION_CONFLICT"):
            replace(value, point=replace(value.point, capacity_quote=Decimal(60)))
    finally:
        graph["journal"].close()


def test_catalog_cannot_omit_held_y_when_only_x_is_selected(tmp_path: Path) -> None:
    graph = pending_graph(tmp_path)
    try:
        observations = tuple(
            item for item in graph["catalog"].observations if item.key.symbol != "YBBUSDT"
        )
        catalog = build_instrument_catalog(
            observations,
            declared_universe=tuple(item.key for item in observations),
            now=graph["now"],
            max_age=graph["catalog"].max_age,
        )
        inputs = replace(graph["inputs"], catalog_revision=catalog.revision)
        with pytest.raises(c.RiskV2Rejected, match="HELD_COVERAGE_REQUIRED"):
            bind(graph, full_inputs=inputs, catalog=catalog)
    finally:
        graph["journal"].close()


@pytest.mark.parametrize("kind", ["risk", "margin", "count"])
def test_negative_member_subtraction_inputs_refuse(tmp_path: Path, kind: str) -> None:
    graph = pending_graph(tmp_path)
    try:
        name = {
            "risk": "reserved_risk_quote",
            "margin": "reserved_margin_quote",
            "count": "reserved_position_count",
        }[kind]
        member = replace(
            graph["domain"].members[0], **{name: -1 if kind == "count" else Decimal(-1)}
        )
        domain = replace(graph["domain"], members=(member, graph["domain"].members[1]))
        with pytest.raises(c.RiskV2Rejected, match="INPUT_INVALID"):
            bind(graph, full_domain=domain)
    finally:
        graph["journal"].close()


@pytest.mark.parametrize("kind", ["position", "open-order", "conditional-order", "extra-margin"])
def test_selected_raw_activity_is_not_erased_with_own_local_id(tmp_path: Path, kind: str) -> None:
    graph = pending_graph(tmp_path)
    try:
        profile = graph["full_venue"].selected_profile
        if kind == "position":
            profile = replace(
                profile,
                selected_position_quantity=field_value(profile.selected_position_quantity, "1"),
            )
        elif kind == "open-order":
            profile = replace(profile, selected_open_order_ids=("VENUE_ORDER",))
        elif kind == "conditional-order":
            profile = replace(profile, selected_conditional_order_ids=("VENUE_CONDITIONAL",))
        else:
            profile = replace(profile, prospective_extra_margin_usdt=Decimal(1))
        with pytest.raises(c.RiskV2Rejected, match="UNSUPPORTED_MODE"):
            bind(graph, full_venue=replace(graph["full_venue"], selected_profile=profile))
    finally:
        graph["journal"].close()


def test_second_real_selected_journal_hold_refuses_even_with_fresh_view(tmp_path: Path) -> None:
    graph = pending_graph(tmp_path)
    try:
        # Negative duplicate archival candidate, not a new current PLAN/issuer qualification.
        duplicate = replace(
            graph["intent"],
            command_key="SECOND_X",
            position_id="POSITION-SECOND_X",
            plan_id="PLAN-SECOND_X",
        )
        register_intent(graph["journal"], graph, duplicate)
        view = graph["journal"]._own_pending_view_v2(
            graph["intent"].envelope.scope,
            command_key="COMMAND",
            owner_epoch="OWNER_1",
            expected_command_identity_sha256=graph["identity"],
            now=graph["now"],
        )
        data = graph["journal"]._require_own_pending_view_v2(
            view,
            scope=graph["intent"].envelope.scope,
            command_key="COMMAND",
            expected_command_identity_sha256=graph["identity"],
            now=graph["now"],
        )
        members = (
            replace(
                graph["domain"].members[0],
                reservation_component_ids=tuple(item.component_id for item in data.full.components),
                reserved_risk_quote=Decimal(24),
                reserved_margin_quote=Decimal("97.33333334"),
                reserved_position_count=2,
            ),
            graph["domain"].members[1],
        )
        graph = rebuild(
            graph, replace(graph["inputs"], reservations=data.full), members, prepare=False
        )
        graph["view"] = view
        with pytest.raises(c.RiskV2Rejected, match="LOCAL_ADMISSION_CONFLICT"):
            bind(graph)
    finally:
        graph["journal"].close()


def test_exact_derivation_does_not_change_ambient_decimal_context(tmp_path: Path) -> None:
    graph = pending_graph(tmp_path)
    try:
        with localcontext() as context:
            context.prec, context.capitals = 1, 0
            for signal in context.traps:
                context.traps[signal] = True
            before = (context.prec, context.rounding, dict(context.traps), dict(context.flags))
            value = bind(graph)
            adapter().bybit_own_pending_model_inputs_sha256_v1(value)
            assert before == (
                context.prec,
                context.rounding,
                dict(context.traps),
                dict(context.flags),
            )
    finally:
        graph["journal"].close()
