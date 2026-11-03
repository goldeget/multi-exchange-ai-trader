"""Independent synthetic economic literals; no transport or issuer authority."""

import hashlib
import json
import sys
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from decimal import getcontext, localcontext
from fractions import Fraction as F
from typing import Any

import pytest

from multi_exchange_trader.account.instrument_envelope import HttpsOrigin
from multi_exchange_trader.account.risk_limit_envelope import BybitRiskLimitEnvelopeReadV1
from multi_exchange_trader.execution import bybit_linear_model_v1 as b
from multi_exchange_trader.execution.bybit_linear_model_inputs_v1 import (
    BybitLinearTierTableV1,
    bybit_instrument_pin_sha256_v1,
    bybit_tier_table_sha256_v1,
    parse_bybit_risk_limit_table_v1,
)
from multi_exchange_trader.execution.risk_v2_contracts import InstrumentPinV2, _canonical_sha256_v2
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected
from multi_exchange_trader.market_data.instrument_definition import (
    LINEAR_SOURCE_RULE_REVISION,
    PARSER_REVISION,
)
from multi_exchange_trader.trading_product import InstrumentKey


def table(
    rows: tuple[tuple[str, str, str, str, str], ...] = (("1000000", "0.01", "0.005", "100", "0"),),
) -> BybitLinearTierTableV1:
    text = []
    for i, (upper, imr, mmr, leverage, deduction) in enumerate(rows, 1):
        text.append(
            '{"id":'
            + str(i)
            + ',"symbol":"BTCUSDT","riskLimitValue":"'
            + upper
            + '","initialMargin":'
            + imr
            + ',"maintenanceMargin":'
            + mmr
            + ',"maxLeverage":"'
            + leverage
            + '","mmDeduction":"'
            + deduction
            + '","isLowestRisk":'
            + str(int(i == 1))
            + "}"
        )
    raw = (
        '{"retCode":0,"result":{"category":"linear","list":['
        + ",".join(text)
        + '],"nextPageCursor":""},"time":1}'
    ).encode()
    stamp = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=1)
    key = InstrumentKey("BYBIT", "LINEAR", "BTCUSDT")
    pin = InstrumentPinV2(
        key,
        "BTCUSDT-LINEAR.BYBIT",
        "catalog-v1",
        "a" * 64,
        PARSER_REVISION,
        LINEAR_SOURCE_RULE_REVISION,
        "instruments",
        "FIXTURE",
        stamp,
        stamp,
        "b" * 64,
    )
    origin = HttpsOrigin("https", "api.bybit.com", 443)
    envelope = BybitRiskLimitEnvelopeReadV1(
        key,
        "GET",
        "/v5/market/risk-limit",
        (("category", "linear"), ("symbol", "BTCUSDT")),
        origin,
        origin,
        200,
        stamp,
        stamp,
        0.0,
        1.0,
        stamp,
        raw,
        hashlib.sha256(raw).hexdigest(),
        1,
        0,
        1,
        1,
        "page1",
        "source1",
        "gen1",
        "FIXTURE",
    )
    return parse_bybit_risk_limit_table_v1(
        (envelope,), instrument_pin=pin, observation_id="table1"
    ).table


def margin(side: str = "LONG", **updates: Any) -> Any:
    args: dict[str, Any] = {
        "side": side,
        "quantity": D(".5"),
        "entry_limit": D(50000),
        "mark": D(50500),
        "leverage": D(10),
        "taker_rate": D(".00055"),
        "order_close_rate": D(".00055"),
    }
    args.update(updates)
    return b.calculate_bybit_margin_point_v1(**args)


def lp(side: str = "LONG", **updates: Any) -> Any:
    args: dict[str, Any] = {
        "side": side,
        "quantity": D(".5"),
        "entry_price": D(50000),
        "leverage": D(10),
        "extra_margin": D(0),
        "taker_rate": D(".00055"),
        "table": table(),
    }
    args.update(updates)
    return b.solve_bybit_liquidation_v1(**args)


@pytest.mark.parametrize(
    "side,close,cost,reserve",
    [
        ("LONG", F(99, 8), F(20209, 8), F(20409, 8)),
        ("SHORT", F(121, 8), F(20231, 8), F(20431, 8)),
    ],
)
def test_f1_two_im_envelope(side: str, close: F, cost: F, reserve: F) -> None:
    value = margin(side)
    assert value.initial_margin_envelope == F(2525)
    assert value.order_im == F(2500)
    assert value.open_estimate == F(55, 4)
    assert value.order_close_reserve == close
    assert value.order_cost == cost
    assert value.point_reserve == reserve


@pytest.mark.parametrize("side,expected", [("LONG", F(5047, 500)), ("SHORT", F(5053, 500))])
def test_distinct_account_and_order_fee_rates(side: str, expected: F) -> None:
    value = margin(
        side,
        quantity=D(1),
        entry_limit=D(100),
        mark=D(100),
        taker_rate=D(".0004"),
        order_close_rate=D(".0006"),
    )
    assert value.point_reserve == expected
    assert value.position_close_estimate == (F(9, 250) if side == "LONG" else F(11, 250))


@pytest.mark.parametrize("side,expected", [("LONG", F(9000000, 199)), ("SHORT", F(11000000, 201))])
def test_f3_exact_lp(side: str, expected: F) -> None:
    value = lp(side)
    assert value.outcome.exact_price == expected


def test_f4_complete_table_and_recorded_intercept() -> None:
    full = table((("1050", "0.1", "0.01", "10", "0"), ("2000", "0.1", "0.02", "10", "10.5")))
    value = lp("SHORT", quantity=D(1), entry_price=D(1000), table=full)
    assert value.outcome.exact_price == F(55525, 51)
    assert value.outcome.candidate_tier_id == "2"
    assert value.consumed_tier_ids == ("1", "2")
    assert value.rows[1].intercept == F(2221, 2)
    assert value.rows[0].self_consistent is False
    assert value.rows[1].self_consistent is True


def test_b_import_and_closed_rejected_return() -> None:
    short = table((("1050", "0.1", "0.01", "10", "0"), ("1080", "0.1", "0.02", "10", "10.5")))
    result = lp("SHORT", quantity=D(1), entry_price=D(1000), table=short)
    assert result.kind == "REJECTED"
    assert result.reason == "ROOT_OUTSIDE_CAPTURED_TABLE"
    assert result.public_code == "RISK_V2_LIQUIDATION_UNVERIFIED"
    assert result.evaluated_prefix == ()


def test_l1_no_positive_root_and_shared_boundary() -> None:
    value = lp(leverage=D(1))
    assert value.outcome.kind == "NO_POSITIVE_ROOT"
    assert value.outcome.long_intercept == F(0)
    assert value.outcome.residual_at_zero == F(0)
    boundary = table((("1050", "0.2", "0.2", "5", "0"), ("2000", "0.3", "0.3", "3", "105")))
    value = lp("SHORT", quantity=D(1), entry_price=D(1050), leverage=D(5), table=boundary)
    assert value.outcome.exact_price == F(1050)
    assert value.outcome.candidate_tier_id == "1"


def test_q_bridge_nonmultiple_cost() -> None:
    value = margin(
        quantity=D(1),
        entry_limit=D(1),
        mark=D(1),
        leverage=D(7),
        taker_rate=D(0),
        order_close_rate=D(0),
    )
    result = b.bridge_bybit_reserve_v1(
        value,
        planned_cost_allowance_quote=D(".000000001"),
        reserve_quantum_usdt=D("1e-8"),
        reserve_rounding_revision="APP_RESERVE_CEILING_QUANTUM_V1",
    )
    assert result.initial_margin_quote == D(".14285715")
    assert result.venue_required_total_reserve_quote == D(".14285715")
    assert result.required_reservation_quote == D(".142857151")


def canonical(record: str, payload: dict[str, object]) -> str:
    raw = json.dumps(
        {"schema": "RISK_V2_CANONICAL_V1", "record_type": record, "fields": payload},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def ratio(n: str, d: str = "1") -> dict[str, object]:
    return {"numerator": n, "denominator": d}


def proof(value: Any, full: BybitLinearTierTableV1, **updates: Any) -> str:
    args: dict[str, Any] = {
        "side": "LONG",
        "quantity": D(".5"),
        "entry_price": D(50000),
        "leverage": D(10),
        "extra_margin": D(0),
        "taker_rate": D(".00055"),
        "table": full,
    }
    args.update(updates)
    return b.bybit_liquidation_proof_sha256_v1(value, **args)


def forged(value: Any, **updates: Any) -> Any:
    result = object.__new__(type(value))
    for field in fields(type(value)):
        object.__setattr__(result, field.name, updates.get(field.name, getattr(value, field.name)))
    return result


def test_lp_row_golden_literal_bytes_and_sha() -> None:
    row = b.BybitLiquidationRowProofV1(
        "1", F(0), F(10), F(0), F(199, 200), F(0), F(0), F(199, 20), False
    )
    raw = b'{"fields":{"candidate_price":{"denominator":"1","numerator":"0"},"intercept":{"denominator":"1","numerator":"0"},"lower_quote":{"denominator":"1","numerator":"0"},"residual_lower":{"denominator":"1","numerator":"0"},"residual_upper":{"denominator":"20","numerator":"199"},"self_consistent":false,"slope":{"denominator":"200","numerator":"199"},"tier_id":"1","upper_quote":{"denominator":"1","numerator":"10"}},"record_type":"BybitLiquidationRowProofV1","schema":"RISK_V2_CANONICAL_V1"}'
    assert len(raw) == 485
    assert (
        hashlib.sha256(raw).hexdigest()
        == "fad33ae4e7ea74f1df82af2a03225f0fdb7573b375158e9cde09b385ed1bcab0"
    )
    assert (
        b.bybit_liquidation_row_sha256_v1(row)
        == "fad33ae4e7ea74f1df82af2a03225f0fdb7573b375158e9cde09b385ed1bcab0"
    )


def test_document_lp_proof_codec_golden_is_distinct_from_admissible_graph() -> None:
    raw = b'{"fields":{"consumed_tier_ids":["1"],"domain_upper_quote":"10","entry_price":{"denominator":"1","numerator":"1"},"extra_margin":{"denominator":"1","numerator":"0"},"full_table_sha256":"728be44d0457dd75d8b6c153631f4d615eb209ab765848e8640280e529b056c6","leverage":{"denominator":"1","numerator":"1"},"outcome":{"kind":"NO_POSITIVE_ROOT","long_intercept":{"denominator":"1","numerator":"0"},"reason":"NON_POSITIVE_LONG_INTERCEPT","residual_at_zero":{"denominator":"1","numerator":"0"}},"quantity":{"denominator":"1","numerator":"1"},"raw_manifest_sha256":"0000000000000000000000000000000000000000000000000000000000000000","rows":["fad33ae4e7ea74f1df82af2a03225f0fdb7573b375158e9cde09b385ed1bcab0"],"rule_revision":"BYBIT_LINEAR_FULL_TABLE_LP_V1","side":"LONG","table_identity":{"deduction_rule_revision":"EXACT_CONTINUOUS_MM_DEDUCTION_V1","full_table_sha256":"728be44d0457dd75d8b6c153631f4d615eb209ab765848e8640280e529b056c6","instrument_pin":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","rate_encoding_revision":"BYBIT_LINEAR_RISK_NUMBER_FRACTION_V1","raw_manifest_sha256":"0000000000000000000000000000000000000000000000000000000000000000","reference":{"observation_id":"t1","payload_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","received_at":"2000-01-01T00:00:00.000000+00:00","server_observed_at":"2000-01-01T00:00:00.000000+00:00","source_environment":"FIXTURE","source_id":"fixture","source_rule_revision":"BYBIT_LINEAR_RISK_LIMIT_TABLE_V1"}},"table_source_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","taker_rate":{"denominator":"1","numerator":"0"}},"record_type":"BybitLiquidationProofV1","schema":"RISK_V2_CANONICAL_V1"}'
    assert len(raw) == 1708
    assert (
        hashlib.sha256(raw).hexdigest()
        == "91b9432ddb8d9ef910d2de28e3fa35b70dcf94486621756c59c5bf4a784c6894"
    )
    payload = json.loads(raw)["fields"]
    payload["consumed_tier_ids"] = tuple(payload["consumed_tier_ids"])
    payload["rows"] = tuple(payload["rows"])
    assert (
        _canonical_sha256_v2("BybitLiquidationProofV1", payload)
        == "91b9432ddb8d9ef910d2de28e3fa35b70dcf94486621756c59c5bf4a784c6894"
    )


def test_independent_margin_bridge_and_candidate_recipes() -> None:
    value = margin()
    expected: dict[str, object] = {
        "side": "LONG",
        "quantity": ratio("1", "2"),
        "entry_limit": ratio("50000"),
        "mark": ratio("50500"),
        "leverage": ratio("10"),
        "taker_rate": ratio("11", "20000"),
        "order_close_rate": ratio("11", "20000"),
        "order_im": ratio("2500"),
        "open_estimate": ratio("55", "4"),
        "order_close_reserve": ratio("99", "8"),
        "position_close_estimate": ratio("99", "8"),
        "mark_core": ratio("2525"),
        "entry_core": ratio("2500"),
        "initial_margin_envelope": ratio("2525"),
        "order_cost": ratio("20209", "8"),
        "point_reserve": ratio("20409", "8"),
    }
    margin_sha = canonical("BybitMarginPointV1", expected)
    assert b.bybit_margin_point_sha256_v1(value) == margin_sha
    reserve = b.bridge_bybit_reserve_v1(
        value,
        planned_cost_allowance_quote=D(20),
        reserve_quantum_usdt=D("1e-8"),
        reserve_rounding_revision="APP_RESERVE_CEILING_QUANTUM_V1",
    )
    reserve_sha = canonical(
        "BybitReserveBridgeV1",
        {
            "initial_margin_quote": "2525",
            "venue_required_total_reserve_quote": "2551.125",
            "required_reservation_quote": "2551.125",
            "exact_initial_margin": ratio("2525"),
            "exact_point_reserve": ratio("20409", "8"),
            "exact_required_reservation": ratio("20409", "8"),
            "planned_cost_allowance_quote": "20",
            "reserve_quantum_usdt": "1E-8",
            "reserve_basis": "POINT_ESTIMATE_TWO_IM_ENVELOPE_V1",
            "reserve_rounding_revision": "APP_RESERVE_CEILING_QUANTUM_V1",
        },
    )
    assert b.bybit_reserve_bridge_sha256_v1(reserve) == reserve_sha
    liquid = lp()
    candidate = b.BybitCandidateEvaluationV1(0, D(10), value, reserve, liquid, "GAP_SATISFIED", ())
    assert b.bybit_candidate_evaluation_sha256_v1(candidate) == canonical(
        "BybitCandidateEvaluationV1",
        {
            "grid_index": 0,
            "leverage": "10",
            "margin": margin_sha,
            "reserve": reserve_sha,
            "liquidation": expected_f3_identities(table())[1],
            "liquidation_buffer_result": "GAP_SATISFIED",
            "failure_codes": (),
        },
    )


def test_proof_replay_binds_every_economic_and_source_child() -> None:
    full = table()
    value = lp(table=full)
    assert proof(value, full) == value.proof_sha256
    for name, new in [
        ("side", "SHORT"),
        ("quantity", D(1)),
        ("entry_price", D(50001)),
        ("leverage", D(9)),
        ("extra_margin", D(1)),
        ("taker_rate", D(".00056")),
    ]:
        with pytest.raises(RiskV2Rejected):
            proof(value, full, **{name: new})
    for name, new in [
        ("proof_sha256", "a" * 64),
        ("raw_manifest_sha256", "a" * 64),
        ("full_table_sha256", "a" * 64),
        ("domain_upper_quote", D(1000001)),
    ]:
        with pytest.raises(RiskV2Rejected):
            proof(forged(value, **{name: new}), full)
    other = table((("1000000", "0.01", "0.0050", "100", "0"),))
    assert other.full_table_sha256 == full.full_table_sha256
    assert other.raw_manifest_sha256 != full.raw_manifest_sha256
    with pytest.raises(RiskV2Rejected, match="RISK_V2_SOURCE_MISMATCH"):
        proof(value, other)


def expected_f3_identities(full: BybitLinearTierTableV1) -> tuple[str, str]:
    # F3 LONG K22500; row residual at upper=995000-22500=972500.
    row_sha = canonical(
        "BybitLiquidationRowProofV1",
        {
            "tier_id": "1",
            "lower_quote": ratio("0"),
            "upper_quote": ratio("1000000"),
            "intercept": ratio("22500"),
            "slope": ratio("199", "200"),
            "candidate_price": ratio("9000000", "199"),
            "residual_lower": ratio("-22500"),
            "residual_upper": ratio("972500"),
            "self_consistent": True,
        },
    )
    outcome: dict[str, object] = {
        "kind": "POSITIVE_ROOT",
        "exact_price": ratio("9000000", "199"),
        "candidate_tier_id": "1",
    }
    reference: dict[str, object] = {
        "observation_id": "table1",
        "source_id": "source1",
        "source_environment": "FIXTURE",
        "server_observed_at": "1970-01-01T00:00:00.001000+00:00",
        "received_at": "1970-01-01T00:00:00.001000+00:00",
        "source_rule_revision": "BYBIT_LINEAR_RISK_LIMIT_TABLE_V1",
        "payload_sha256": full.reference.payload_sha256,
    }
    expected: dict[str, object] = {
        "side": "LONG",
        "quantity": ratio("1", "2"),
        "entry_price": ratio("50000"),
        "leverage": ratio("10"),
        "extra_margin": ratio("0"),
        "taker_rate": ratio("11", "20000"),
        "table_source_sha256": bybit_tier_table_sha256_v1(full),
        "table_identity": {
            "instrument_pin": bybit_instrument_pin_sha256_v1(full.instrument_pin),
            "reference": reference,
            "raw_manifest_sha256": full.raw_manifest_sha256,
            "full_table_sha256": full.full_table_sha256,
            "rate_encoding_revision": "BYBIT_LINEAR_RISK_NUMBER_FRACTION_V1",
            "deduction_rule_revision": "EXACT_CONTINUOUS_MM_DEDUCTION_V1",
        },
        "outcome": outcome,
        "domain_upper_quote": "1000000",
        "full_table_sha256": full.full_table_sha256,
        "raw_manifest_sha256": full.raw_manifest_sha256,
        "consumed_tier_ids": ("1",),
        "rows": (row_sha,),
        "rule_revision": "BYBIT_LINEAR_FULL_TABLE_LP_V1",
    }
    expected_sha = canonical("BybitLiquidationProofV1", expected)
    calculation_sha = canonical(
        "BybitLiquidationCalculationV1",
        {
            "outcome": outcome,
            "domain_upper_quote": "1000000",
            "full_table_sha256": full.full_table_sha256,
            "raw_manifest_sha256": full.raw_manifest_sha256,
            "consumed_tier_ids": ("1",),
            "rows": (row_sha,),
            "rule_revision": "BYBIT_LINEAR_FULL_TABLE_LP_V1",
            "proof_sha256": expected_sha,
        },
    )
    return expected_sha, calculation_sha


def test_independent_complete_lp_proof_and_calculation_recipes() -> None:
    full = table()
    value = lp(table=full)
    expected_proof, expected_calculation = expected_f3_identities(full)
    assert value.proof_sha256 == expected_proof
    assert proof(value, full) == expected_proof
    assert b.bybit_liquidation_calculation_sha256_v1(value) == expected_calculation


@pytest.mark.parametrize(
    "code,reason",
    [
        ("RISK_V2_INPUT_INVALID", "INPUT_MISMATCH"),
        ("RISK_V2_SCOPE_MISMATCH", "INPUT_MISMATCH"),
        ("RISK_V2_SOURCE_MISMATCH", "INPUT_MISMATCH"),
        ("RISK_V2_REVISION_MISMATCH", "INPUT_MISMATCH"),
        ("RISK_V2_RESOURCE_LIMIT", "RESOURCE_LIMIT"),
        ("RISK_V2_STALE_EVIDENCE", "EVIDENCE_INVALID"),
        ("RISK_V2_FUTURE_EVIDENCE", "EVIDENCE_INVALID"),
        ("RISK_V2_EXPIRED", "EVIDENCE_INVALID"),
        ("RISK_V2_CAUSAL_ORDER_INVALID", "EVIDENCE_INVALID"),
        ("RISK_V2_RISK_TIER_UNVERIFIED", "SOURCE_INVALID"),
        ("RISK_V2_UNSUPPORTED_MODE", "PROFILE_UNSUPPORTED"),
        ("RISK_V2_UNSUPPORTED_PRODUCT", "PROFILE_UNSUPPORTED"),
        ("RISK_V2_MARGIN_UNVERIFIED", "PROFILE_UNSUPPORTED"),
    ],
)
def test_whitelisted_lp_errors_preserve_exact_public_code(
    code: str, reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject(*args: Any, **kwargs: Any) -> Any:
        raise RiskV2Rejected(code)

    monkeypatch.setattr(b, "bybit_tier_table_sha256_v1", reject)
    result = lp()
    assert type(result) is b.BybitModelRejectedV1
    assert result.reason == reason
    assert result.public_code == code
    assert result.evaluated_prefix == ()


def test_candidate_rejects_crossed_side_and_quantity_even_with_consistent_children() -> None:
    value = margin()
    reserve = b.bridge_bybit_reserve_v1(
        value,
        planned_cost_allowance_quote=D(20),
        reserve_quantum_usdt=D("1e-8"),
        reserve_rounding_revision="APP_RESERVE_CEILING_QUANTUM_V1",
    )
    for liquid in (lp("SHORT"), lp(quantity=D(1))):
        with pytest.raises(RiskV2Rejected, match="RISK_V2_INPUT_INVALID"):
            b.BybitCandidateEvaluationV1(0, D(10), value, reserve, liquid, "GAP_SATISFIED", ())


def test_candidate_and_rejected_prefix_validate_complete_ordered_children() -> None:
    value = margin()
    reserve = b.bridge_bybit_reserve_v1(
        value,
        planned_cost_allowance_quote=D(20),
        reserve_quantum_usdt=D("1e-8"),
        reserve_rounding_revision="APP_RESERVE_CEILING_QUANTUM_V1",
    )
    liquid = lp()
    candidate = b.BybitCandidateEvaluationV1(
        0, D(10), value, reserve, liquid, "GAP_SATISFIED", ("CAPACITY",)
    )
    result = b.BybitModelRejectedV1(
        "REJECTED", "SEARCH", "RESOURCE_LIMIT", "RISK_V2_RESOURCE_LIMIT", D(11), (candidate,)
    )
    assert result.evaluated_prefix == (candidate,)
    for change in (
        {"grid_index": True},
        {"failure_codes": ("CAPACITY", "IMR")},
        {"failure_codes": ("CAPACITY", "CAPACITY")},
        {"liquidation_buffer_result": None},
        {"margin": forged(value, point_reserve=F(0))},
    ):
        with pytest.raises(RiskV2Rejected):
            b.bybit_candidate_evaluation_sha256_v1(forged(candidate, **change))
    with pytest.raises(RiskV2Rejected):
        replace(result, evaluated_prefix=(replace(candidate, grid_index=1),))
    with pytest.raises(RiskV2Rejected):
        replace(result, evaluated_prefix=(replace(candidate, failure_codes=()),))


def test_f6_large_quantum_ceiling_and_source_derived_exponent_boundaries() -> None:
    value = margin(
        quantity=D(1000),
        entry_limit=D(1),
        mark=D(1),
        leverage=D(7),
        taker_rate=D(0),
        order_close_rate=D(0),
    )
    result = b.bridge_bybit_reserve_v1(
        value,
        planned_cost_allowance_quote=D(0),
        reserve_quantum_usdt=D("1e-8"),
        reserve_rounding_revision="APP_RESERVE_CEILING_QUANTUM_V1",
    )
    assert result.required_reservation_quote == D("142.85714286")
    assert margin(entry_limit=D("1e1000"), mark=D("1e1000")).entry_limit == F(10**1000)
    with pytest.raises(RiskV2Rejected, match="RISK_V2_RESOURCE_LIMIT"):
        margin(mark=D("1e1001"))
    with pytest.raises(RiskV2Rejected, match="RISK_V2_RESOURCE_LIMIT"):
        margin(quantity=D("1e65537"))


def test_negative_intercept_long_and_positive_short_with_extra_margin() -> None:
    value = lp(quantity=D(1), entry_price=D(100), leverage=D(1), extra_margin=D(1), taker_rate=D(0))
    assert value.outcome.long_intercept == F(-1)
    assert value.outcome.residual_at_zero == F(1)
    assert value.rows[0].candidate_price == F(-200, 199)
    assert value.rows[0].self_consistent is False
    value = lp(
        "SHORT",
        quantity=D(1),
        entry_price=D(100),
        leverage=D(10),
        extra_margin=D(1),
        taker_rate=D(0),
    )
    assert value.outcome.exact_price == F(22200, 201)


def test_row_residual_and_no_root_tamper_are_rejected() -> None:
    value = lp()
    for change in (
        {"intercept": F(1)},
        {"residual_lower": F(0)},
        {"self_consistent": False},
        {"slope": F(0)},
        {"tier_id": ""},
    ):
        with pytest.raises(RiskV2Rejected):
            b.bybit_liquidation_row_sha256_v1(forged(value.rows[0], **change))
    no_root = lp(leverage=D(1))
    with pytest.raises(RiskV2Rejected):
        b.bybit_liquidation_calculation_sha256_v1(
            forged(no_root, outcome=forged(no_root.outcome, residual_at_zero=F(1)))
        )
    with pytest.raises(RiskV2Rejected):
        b.bybit_liquidation_calculation_sha256_v1(forged(value, consumed_tier_ids=("2",)))


@pytest.mark.parametrize(
    "name,new",
    [
        ("initial_margin_envelope", F(2524)),
        ("point_reserve", F(1)),
        ("open_estimate", F(0)),
        ("order_im", F(2501)),
        ("entry_limit", F(1, 3)),
    ],
)
def test_bridge_replays_all_supplied_margin_parts(name: str, new: F) -> None:
    with pytest.raises(RiskV2Rejected, match="RISK_V2_INPUT_INVALID"):
        b.bridge_bybit_reserve_v1(
            forged(margin(), **{name: new}),
            planned_cost_allowance_quote=D(20),
            reserve_quantum_usdt=D("1e-8"),
            reserve_rounding_revision="APP_RESERVE_CEILING_QUANTUM_V1",
        )


@pytest.mark.parametrize("q", ["0.000000010", "0.0000001", "-1e-8"])
def test_quantum_is_exact_tuple_not_same_value_or_other_step(q: str) -> None:
    with pytest.raises(RiskV2Rejected):
        b.bridge_bybit_reserve_v1(
            margin(),
            planned_cost_allowance_quote=D(0),
            reserve_quantum_usdt=D(q),
            reserve_rounding_revision="APP_RESERVE_CEILING_QUANTUM_V1",
        )


def test_f2_recurrence_and_nonmonotonic_table_are_consumed_in_full() -> None:
    full = table(
        (
            ("50000", "0.1", "0.005", "10", "0"),
            ("100000", "0.1", "0.015", "10", "500"),
            ("150000", "0.1", "0.025", "10", "1500"),
            ("500000", "0.1", "0.035", "10", "3000"),
        )
    )
    result = lp("SHORT", table=full)
    assert result.consumed_tier_ids == ("1", "2", "3", "4")
    assert result.rows[3].slope == F(207, 200)
    assert result.rows[3].intercept == F(30500)
    assert F(400000) * (result.rows[3].slope - 1) - (result.rows[3].intercept - F(27500)) == F(
        11000
    )
    nonmonotonic = table(
        (
            ("100", "0.2", "0.1", "5", "0"),
            ("200", "0.4", "0.3", "2", "20"),
            ("300", "0.3", "0.25", "3", "10"),
        )
    )
    result = lp(quantity=D(1), entry_price=D(250), leverage=D(2), table=nonmonotonic)
    assert result.consumed_tier_ids == ("1", "2", "3")


def test_derived_5001_digits_are_not_source256_and_canonical_bound_is_separate() -> None:
    huge = D((0, (1,) + (0,) * 5000, 0))
    value = margin(
        quantity=huge,
        leverage=huge,
        entry_limit=D(1),
        mark=D(1),
        taker_rate=D(0),
        order_close_rate=D(0),
    )
    assert value.initial_margin_envelope == F(1)
    assert len(b.bybit_margin_point_sha256_v1(value)) == 64
    with pytest.raises(RiskV2Rejected, match="RISK_V2_RESOURCE_LIMIT"):
        margin(entry_limit=huge)
    result = lp(entry_price=huge)
    assert result.reason == "RESOURCE_LIMIT"
    assert result.public_code == "RISK_V2_RESOURCE_LIMIT"
    larger = D((0, (1,) + (0,) * 35000, 0))
    value = margin(
        quantity=larger,
        leverage=larger,
        entry_limit=D(1),
        mark=D(1),
        taker_rate=D(0),
        order_close_rate=D(0),
    )
    with pytest.raises(RiskV2Rejected, match="RISK_V2_RESOURCE_LIMIT"):
        b.bybit_margin_point_sha256_v1(value)


def test_forged_margin_cannot_move_derived_quantity_into_source_price_role() -> None:
    huge = D("1e1001")
    value = margin(
        quantity=huge,
        leverage=huge,
        entry_limit=D(1),
        mark=D(1),
        taker_rate=D(0),
        order_close_rate=D(0),
    )
    # Arithmetic remains identical; source-role P/M exceed adjusted exponent1000.
    bad = forged(value, quantity=F(1), entry_limit=F(10**1001), mark=F(10**1001))
    with pytest.raises(RiskV2Rejected, match="RISK_V2_RESOURCE_LIMIT"):
        b.bybit_margin_point_sha256_v1(bad)


def test_strict_preallocation_guards_and_cross_cancellation() -> None:
    # Operand bit sum=1,000,001 is rejected although this actual product fits 1m.
    with pytest.raises(RiskV2Rejected, match="RISK_V2_RESOURCE_LIMIT"):
        b._product(1 << 999999, 1)
    assert b._product(0, 1 << 999999) == 0
    left, right = F(1 << 600000, 3), F(3, 1 << 600000)
    assert b._mul(left, right) == F(1)
    with pytest.raises(RiskV2Rejected, match="RISK_V2_RESOURCE_LIMIT"):
        b._mul(F(1 << 600000), F(1 << 600000))
    with pytest.raises(RiskV2Rejected, match="RISK_V2_RESOURCE_LIMIT"):
        b._add(F(1 << 999999), F(1 << 999999))
    assert b._sub(F(1 << 999998), F(1 << 999998)) == F(0)
    with pytest.raises(RiskV2Rejected, match="RISK_V2_INPUT_INVALID"):
        b._div(F(1), F(0))


def test_exact_types_forgery_and_closed_rejection_matrix() -> None:
    class Hostile:
        def __eq__(self, other: object) -> bool:
            raise AssertionError("untrusted equality invoked")

    result = lp(table=Hostile())
    assert result.reason == "INPUT_MISMATCH"
    assert result.public_code == "RISK_V2_INPUT_INVALID"
    result = lp(leverage=D(0))
    assert result.reason == "INPUT_MISMATCH"
    assert result.candidate_leverage is None
    for reason, code in [
        ("SOURCE_INVALID", "RISK_V2_INPUT_INVALID"),
        ("INPUT_MISMATCH", "RISK_V2_MARGIN_LIMIT"),
    ]:
        with pytest.raises(RiskV2Rejected):
            b.BybitModelRejectedV1("REJECTED", "LIQUIDATION", reason, code, None, ())  # type: ignore[arg-type]
    full = table()
    bad = forged(full, rate_encoding_revision="UNKNOWN")
    assert lp(table=bad).public_code == "RISK_V2_INPUT_INVALID"
    bad = forged(full, rows=(forged(full.rows[0], mm_deduction_usdt=Hostile()),))
    assert lp(table=bad).public_code == "RISK_V2_INPUT_INVALID"


def test_unlisted_risk_and_programmer_faults_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    def unlisted(*args: Any, **kwargs: Any) -> Any:
        raise RiskV2Rejected("RISK_V2_MARGIN_LIMIT")

    monkeypatch.setattr(b, "bybit_tier_table_sha256_v1", unlisted)
    with pytest.raises(RiskV2Rejected, match="RISK_V2_MARGIN_LIMIT"):
        lp()

    def unowned_lp(*args: Any, **kwargs: Any) -> Any:
        raise RiskV2Rejected("RISK_V2_LIQUIDATION_UNVERIFIED")

    monkeypatch.setattr(b, "bybit_tier_table_sha256_v1", unowned_lp)
    with pytest.raises(RiskV2Rejected, match="RISK_V2_LIQUIDATION_UNVERIFIED"):
        lp()

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic programmer fault")

    monkeypatch.setattr(b, "bybit_tier_table_sha256_v1", unexpected)
    with pytest.raises(RuntimeError, match="synthetic programmer fault"):
        lp()


def test_ambient_context_digit_limit_and_stamps_unchanged() -> None:
    global_digits = sys.get_int_max_str_digits()
    with localcontext() as context:
        context.prec, context.capitals = 1, 0
        for signal in context.traps:
            context.traps[signal] = True
        before = (
            context.prec,
            context.rounding,
            context.capitals,
            dict(context.traps),
            dict(context.flags),
        )
        value = margin()
        liquid = lp()
        b.bybit_margin_point_sha256_v1(value)
        b.bybit_liquidation_calculation_sha256_v1(liquid)
        after = (
            context.prec,
            context.rounding,
            context.capitals,
            dict(context.traps),
            dict(context.flags),
        )
        assert before == after
    assert getcontext().prec > 0
    assert sys.get_int_max_str_digits() == global_digits
    assert all(
        "time" not in field.name and "computed_at" != field.name
        for field in fields(b.BybitLiquidationCalculationV1)
    )
