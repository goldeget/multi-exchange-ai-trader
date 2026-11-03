"""Synthetic full-domain proofs, never actual account/source qualification."""

import hashlib
import json
from dataclasses import MISSING, fields, replace
from datetime import datetime, timedelta, tzinfo
from decimal import Decimal as D
from decimal import Inexact, Rounded, localcontext
from typing import Any, cast

import pytest
from risk_v2_support import (
    DIGEST,
    NOW,
    computation_ref_v2,
    instrument_v2,
    observation_ref_v2,
    policy_v2,
)

from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
from multi_exchange_trader.execution import risk_domain_v2 as d
from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.protection import ProtectionScope
from multi_exchange_trader.market_data.instrument_catalog import (
    build_instrument_catalog,
    require_catalog_selection,
)
from multi_exchange_trader.market_data.instrument_definition import (
    INSTRUMENT_ENDPOINT,
    CatalogSource,
    parse_bybit_instrument_response,
)
from multi_exchange_trader.trading_product import InstrumentKey

X = InstrumentKey("BYBIT", "LINEAR", "XAAUSDT")
Y = InstrumentKey("BYBIT", "LINEAR", "YBBUSDT")


def _inputs() -> dict[str, Any]:
    x = instrument_v2()
    row = json.loads(x.raw_row_canonical)
    row.update(symbol="YBBUSDT", baseCoin="YBB")
    y = parse_bybit_instrument_response(
        {"retCode": 0, "result": {"category": "linear", "list": [row]}, "time": 1793663999000},
        expected=Y,
        source=CatalogSource("FIXTURE_CATALOG", "FIXTURE", INSTRUMENT_ENDPOINT, NOW),
    )
    catalog = build_instrument_catalog(
        (x, y), declared_universe=(X, Y), now=NOW, max_age=timedelta(seconds=30)
    )
    policy = policy_v2()
    account = c.LinearAccountRiskSnapshotV2(
        policy.scope.product_scope.account,
        observation_ref_v2("ACCOUNT"),
        "ACCOUNT_COVERAGE",
        (c.ExposureQuantityV2("AY", Y, "ACCOUNT_POSITION", "SELL", D(2)),),
        "UTC_REALIZED_NET_FEES_FUNDING",
        NOW.date(),
        D(1000),
        D(500),
        D(7),
        D(8),
        2,
        D(0),
        D(0),
        0,
        "ISOLATED",
        "ONE_WAY",
        (),
    )
    reservations = c.LinearReservationSnapshotV2(
        account.account,
        "RESERVATION",
        "OWNER_V1",
        1,
        DIGEST,
        NOW,
        None,
        (c.ExposureQuantityV2("RX", X, "PENDING_ENTRY", "BUY", D(".5")),),
        D(3),
        D(4),
        3,
    )
    marks = (
        c.LinearMarkQuoteV2(X, observation_ref_v2("MARK_X"), D(140)),
        c.LinearMarkQuoteV2(Y, observation_ref_v2("MARK_Y"), D(50)),
    )
    rows = (
        e.LinearRiskTierV2("z_first", D(0), D(100), D(1), D("1.5"), D(".7"), D(".2"), D(8)),
        e.LinearRiskTierV2("a_second", D(100), D(500), D(1), D(100), D(".2"), D(".1"), D(9)),
    )
    members = tuple(
        d.LinearRiskDomainMemberV2(
            c.pin_instrument_v2(observation, catalog_revision=catalog.revision),
            mark,
            e.LinearTierSnapshotV2(
                observation.key,
                observation_ref_v2("TIERS"),
                observation.specification_revision,
                rows,
            ),
            () if index == 0 else ("AY",),
            ("RX",) if index == 0 else (),
            D(0) if index == 0 else D(7),
            D(0) if index == 0 else D(8),
            0 if index == 0 else 2,
            D(3) if index == 0 else D(0),
            D(4) if index == 0 else D(0),
            3 if index == 0 else 0,
            observation_ref_v2("MARGIN"),
            computation_ref_v2(),
        )
        for index, (observation, mark) in enumerate(zip((x, y), marks, strict=True))
    )
    return {
        "policy": policy,
        "catalog": catalog,
        "catalog_revision": catalog.revision,
        "coverage_revision": "DOMAIN_V1",
        "source_generation": "GENERATION_V1",
        "account": account,
        "reservations": reservations,
        "marks": marks,
        "members": members,
        "source_record_sha256s": (DIGEST,),
        "now": NOW,
    }


def _coverage() -> d.LinearRiskDomainCoverageV2:
    i = _inputs()
    return d.LinearRiskDomainCoverageV2(
        i["account"].account,
        i["catalog_revision"],
        X,
        "DOMAIN_V1",
        "GENERATION_V1",
        i["members"],
        c.account_snapshot_sha256_v2(i["account"]),
        c.reservation_snapshot_sha256_v2(i["reservations"]),
        c.mark_set_sha256_v2(i["marks"]),
        (DIGEST,),
    )


def _forge(original: Any, **changes: Any) -> Any:
    result = object.__new__(type(original))
    for field in fields(original):
        object.__setattr__(
            result, field.name, changes.get(field.name, getattr(original, field.name))
        )
    return result


def _reject(code: str = "INPUT_INVALID") -> Any:
    return pytest.raises(c.RiskV2Rejected, match=f"^RISK_V2_{code}$")


def test_exact_domain_field_lists_no_defaults() -> None:
    assert [f.name for f in fields(d.LinearRiskDomainMemberV2)] == [
        "instrument_pin",
        "mark",
        "tiers",
        "account_component_ids",
        "reservation_component_ids",
        "account_risk_quote",
        "account_margin_quote",
        "account_position_count",
        "reserved_risk_quote",
        "reserved_margin_quote",
        "reserved_position_count",
        "account_margin_reference",
        "account_risk_reference",
    ]
    assert [f.name for f in fields(d.LinearRiskDomainCoverageV2)] == [
        "account_scope",
        "catalog_revision",
        "selected_entry_key",
        "coverage_revision",
        "source_generation",
        "members",
        "account_snapshot_sha256",
        "reservation_snapshot_sha256",
        "mark_set_sha256",
        "source_record_sha256s",
    ]
    for cls in (d.LinearRiskDomainMemberV2, d.LinearRiskDomainCoverageV2):
        assert cast(Any, cls).__dataclass_params__.frozen
        assert all(f.default is MISSING and f.default_factory is MISSING for f in fields(cls))


def test_coverage_hash_closed_payload() -> None:
    value = _coverage()
    # Independent scalar oracle: no production payload or canonical helper.
    key_x = {"venue": "BYBIT", "product": "LINEAR", "symbol": "XAAUSDT"}
    key_y = {"venue": "BYBIT", "product": "LINEAR", "symbol": "YBBUSDT"}
    server, received = "2026-11-02T23:59:59.000000+00:00", "2026-11-03T00:00:00.000000+00:00"

    def reference(identifier: str) -> dict[str, object]:
        return {
            "observation_id": identifier,
            "source_id": "FIXTURE_SOURCE",
            "source_environment": "FIXTURE",
            "source_rule_revision": "FIXTURE_SOURCE_V1",
            "server_observed_at": server,
            "received_at": received,
            "payload_sha256": DIGEST,
        }

    literal_members = []
    for index, key in enumerate((key_x, key_y)):
        pin = value.members[index].instrument_pin
        literal_members.append(
            {
                "instrument_pin": {
                    "key": key,
                    "native_instrument_id": key["symbol"] + "-LINEAR.BYBIT",
                    "catalog_revision": value.catalog_revision,
                    "specification_revision": pin.specification_revision,
                    "parser_revision": "BYBIT_INSTRUMENT_PARSER_V1",
                    "source_rule_revision": "BYBIT_LINEAR_USDT_PERPETUAL_V1",
                    "source_id": "FIXTURE_CATALOG",
                    "source_environment": "FIXTURE",
                    "response_time": server,
                    "received_at": received,
                    "raw_row_sha256": pin.raw_row_sha256,
                },
                "mark": {
                    "instrument": key,
                    "reference": reference("MARK_X" if index == 0 else "MARK_Y"),
                    "mark_price": "140" if index == 0 else "50",
                },
                "tiers": {
                    "instrument": key,
                    "reference": reference("TIERS"),
                    "instrument_revision": pin.specification_revision,
                    "rows": [
                        {
                            "tier_id": "z_first",
                            "lower_exclusive_quote": "0",
                            "upper_inclusive_quote": "100",
                            "min_leverage": "1",
                            "max_leverage": "1.5",
                            "initial_margin_rate": "0.7",
                            "maintenance_margin_rate": "0.2",
                            "maintenance_margin_deduction": "8",
                        },
                        {
                            "tier_id": "a_second",
                            "lower_exclusive_quote": "100",
                            "upper_inclusive_quote": "500",
                            "min_leverage": "1",
                            "max_leverage": "100",
                            "initial_margin_rate": "0.2",
                            "maintenance_margin_rate": "0.1",
                            "maintenance_margin_deduction": "9",
                        },
                    ],
                },
                "account_component_ids": [] if index == 0 else ["AY"],
                "reservation_component_ids": ["RX"] if index == 0 else [],
                "account_risk_quote": "0" if index == 0 else "7",
                "account_margin_quote": "0" if index == 0 else "8",
                "account_position_count": 0 if index == 0 else 2,
                "reserved_risk_quote": "3" if index == 0 else "0",
                "reserved_margin_quote": "4" if index == 0 else "0",
                "reserved_position_count": 3 if index == 0 else 0,
                "account_margin_reference": reference("MARGIN"),
                "account_risk_reference": {
                    "calculation_id": "FIXTURE_COST",
                    "model_revision": "FIXTURE_COST_V1",
                    "target_environment": "FIXTURE",
                    "computed_at": received,
                    "input_sha256": DIGEST,
                },
            }
        )
    fields_payload = {
        "account_scope": {"account_id": "FIXTURE_ACCOUNT", "environment": "DEMO"},
        "catalog_revision": value.catalog_revision,
        "selected_entry_key": key_x,
        "coverage_revision": "DOMAIN_V1",
        "source_generation": "GENERATION_V1",
        "members": literal_members,
        "account_snapshot_sha256": value.account_snapshot_sha256,
        "reservation_snapshot_sha256": value.reservation_snapshot_sha256,
        "mark_set_sha256": value.mark_set_sha256,
        "source_record_sha256s": [DIGEST],
    }
    encoded = json.dumps(
        {
            "schema": "RISK_V2_CANONICAL_V1",
            "record_type": "LinearRiskDomainCoverageV2",
            "fields": fields_payload,
        },
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    assert d.risk_domain_sha256_v2(value) == hashlib.sha256(encoded).hexdigest()


@pytest.mark.parametrize("bad", [(), ("b" * 64, DIGEST), (DIGEST, DIGEST), ("A" * 64,), [DIGEST]])
def test_member_id_source_hash_order(bad: object) -> None:
    with _reject():
        d.risk_domain_sha256_v2(_forge(_coverage(), source_record_sha256s=bad))


def test_coverage_hash_revalidates_forged_records() -> None:
    value = _coverage()
    bad = _forge(value.members[1], account_position_count=True)
    with _reject():
        d.risk_domain_sha256_v2(replace(value, members=(value.members[0], bad)))


def test_selected_x_keeps_held_y() -> None:
    i = _inputs()
    selected = require_catalog_selection(
        i["catalog"],
        allowed=(X,),
        selected=(X,),
        expected_product="LINEAR",
        expected_source_environment="FIXTURE",
        expected_revision=i["catalog_revision"],
        now=NOW,
    )
    assert tuple(observation.key for observation in selected) == (X,)
    value = d.build_linear_risk_domain_coverage_v2(**i)
    assert value.selected_entry_key == X
    assert tuple(member.instrument_pin.key for member in value.members) == (X, Y)
    assert value.members[1].account_component_ids == ("AY",)
    assert value.source_record_sha256s == (DIGEST,)


@pytest.mark.parametrize("kind", ["missing", "extra", "account_id", "reservation_id"])
def test_missing_extra_member_or_ids_rejected(kind: str) -> None:
    i = _inputs()
    x, y = i["members"]
    if kind == "missing":
        i["members"] = (x,)
    elif kind == "extra":
        i["members"] = (x, x, y)
    elif kind == "account_id":
        i["members"] = (x, replace(y, account_component_ids=()))
    else:
        i["members"] = (replace(x, reservation_component_ids=()), y)
    with _reject("INPUT_INVALID" if kind == "extra" else "HELD_COVERAGE_REQUIRED"):
        d.build_linear_risk_domain_coverage_v2(**i)


@pytest.mark.parametrize(
    "name",
    [
        "account_risk_quote",
        "account_margin_quote",
        "account_position_count",
        "reserved_risk_quote",
        "reserved_margin_quote",
        "reserved_position_count",
    ],
)
def test_member_aggregates_match_headers(name: str) -> None:
    i = _inputs()
    x, y = i["members"]
    amount = 1 if name.endswith("count") else D(1)
    i["members"] = (replace(x, **{name: amount}), y)
    with _reject("HELD_COVERAGE_REQUIRED"):
        d.build_linear_risk_domain_coverage_v2(**i)


def test_builder_binds_actual_supplied_snapshot_hashes() -> None:
    i = _inputs()
    value = d.build_linear_risk_domain_coverage_v2(**i)
    assert value.account_snapshot_sha256 == c.account_snapshot_sha256_v2(i["account"])
    assert value.reservation_snapshot_sha256 == c.reservation_snapshot_sha256_v2(i["reservations"])
    assert value.mark_set_sha256 == c.mark_set_sha256_v2(i["marks"])


@pytest.mark.parametrize("price,passes", [("250", True), ("250.0000000000000000000000001", False)])
def test_u_uses_held_pending_gross_only(price: str, passes: bool) -> None:
    i = _inputs()
    mark = replace(i["marks"][1], mark_price=D(price))
    i["marks"] = (i["marks"][0], mark)
    i["members"] = (i["members"][0], replace(i["members"][1], mark=mark))
    if passes:
        assert d.build_linear_risk_domain_coverage_v2(**i).members[1].mark == mark
    else:
        with _reject("RISK_TIER_UNVERIFIED"):
            d.build_linear_risk_domain_coverage_v2(**i)


def test_u_opposing_sides_are_not_netted_and_x_leverage_does_not_filter_y() -> None:
    i = _inputs()
    i["policy"] = replace(i["policy"], leverage_min=D(2))
    # Held Y tier min/max [1,1.5] is irrelevant to candidate X leverage_min=2.
    assert d.build_linear_risk_domain_coverage_v2(**i).members[1].tiers.rows[0].max_leverage == D(
        "1.5"
    )
    y = i["members"][1]
    component = c.ExposureQuantityV2("AZ", Y, "ACCOUNT_POSITION", "BUY", D(9))
    i["account"] = replace(i["account"], components=(*i["account"].components, component))
    i["members"] = (i["members"][0], replace(y, account_component_ids=("AY", "AZ")))
    with _reject("RISK_TIER_UNVERIFIED"):
        d.build_linear_risk_domain_coverage_v2(**i)


def _set_path(value: Any, path: tuple[str | int, ...], changed: object) -> Any:
    if not path:
        return changed
    head, *tail = path
    if isinstance(head, int):
        items = list(value)
        items[head] = _set_path(items[head], tuple(tail), changed)
        return tuple(items)
    return _forge(value, **{head: _set_path(getattr(value, head), tuple(tail), changed)})


def _catalog_inputs(i: dict[str, Any], catalog: Any) -> dict[str, Any]:
    members = tuple(
        replace(
            member,
            instrument_pin=c.pin_instrument_v2(observation, catalog_revision=catalog.revision),
        )
        for member, observation in zip(i["members"], catalog.observations, strict=True)
    )
    return {**i, "catalog": catalog, "catalog_revision": catalog.revision, "members": members}


@pytest.mark.parametrize(
    "which",
    [
        "account_server",
        "account_receipt",
        "reservation",
        "tier_server",
        "tier_receipt",
        "margin_server",
        "margin_receipt",
        "computed",
        "pin",
    ],
)
def test_all_visible_sources_fresh_independently(which: str) -> None:
    i = _inputs()
    old = NOW - timedelta(seconds=11)
    if which.startswith("account_"):
        ref = i["account"].reference
        ref = replace(
            ref, server_observed_at=old, received_at=old if which.endswith("receipt") else NOW
        )
        i["account"] = replace(i["account"], reference=ref)
    elif which == "reservation":
        i["reservations"] = replace(i["reservations"], observed_at=old)
    elif which == "pin":
        catalog = i["catalog"]
        first = replace(
            catalog.observations[0],
            response_time=old,
            source=replace(catalog.observations[0].source, received_at=old),
        )
        catalog = build_instrument_catalog(
            (first, catalog.observations[1]),
            declared_universe=(X, Y),
            now=NOW,
            max_age=catalog.max_age,
        )
        i = _catalog_inputs(i, catalog)
    else:
        first, second = i["members"]
        if which == "computed":
            first = replace(
                first, account_risk_reference=replace(first.account_risk_reference, computed_at=old)
            )
        else:
            ref = (
                first.tiers.reference
                if which.startswith("tier")
                else first.account_margin_reference
            )
            ref = replace(
                ref, server_observed_at=old, received_at=old if which.endswith("receipt") else NOW
            )
            first = (
                replace(first, tiers=replace(first.tiers, reference=ref))
                if which.startswith("tier")
                else replace(first, account_margin_reference=ref)
            )
        i["members"] = (first, second)
    with _reject("STALE_EVIDENCE"):
        d.build_linear_risk_domain_coverage_v2(**i)


@pytest.mark.parametrize("which", ["server_observed_at", "received_at"])
def test_quote_times_use_quote_age_independently(which: str) -> None:
    i = _inputs()
    ref = replace(
        i["marks"][0].reference,
        server_observed_at=NOW - timedelta(seconds=3),
        received_at=NOW - timedelta(seconds=3) if which == "received_at" else NOW,
    )
    mark = replace(i["marks"][0], reference=ref)
    i["marks"] = (mark, i["marks"][1])
    i["members"] = (replace(i["members"][0], mark=mark), i["members"][1])
    with _reject("STALE_EVIDENCE"):
        d.build_linear_risk_domain_coverage_v2(**i)


def test_equal_timestamps_and_policy_age_boundary() -> None:
    i = _inputs()
    i["policy"] = replace(i["policy"], max_quote_age_seconds=D(1), max_evidence_age_seconds=D(1))
    assert d.build_linear_risk_domain_coverage_v2(**i).members
    i["policy"] = replace(i["policy"], max_quote_age_seconds=D(".999999"))
    with _reject("STALE_EVIDENCE"):
        d.build_linear_risk_domain_coverage_v2(**i)
    i = _inputs()
    marks = tuple(
        replace(mark, reference=replace(mark.reference, server_observed_at=NOW))
        for mark in i["marks"]
    )
    i["marks"] = marks
    i["members"] = tuple(
        replace(member, mark=mark) for member, mark in zip(i["members"], marks, strict=True)
    )
    assert (
        d.build_linear_risk_domain_coverage_v2(**i).members[0].mark.reference.server_observed_at
        == NOW
    )


def test_catalog_expiry_boundary_and_exact_supplied_max_age() -> None:
    i = _inputs()
    catalog = build_instrument_catalog(
        i["catalog"].observations,
        declared_universe=(X, Y),
        now=NOW,
        max_age=timedelta(seconds=1, microseconds=1),
    )
    assert d.build_linear_risk_domain_coverage_v2(**_catalog_inputs(i, catalog)).members
    i = _catalog_inputs(i, catalog)
    i["now"] = NOW + timedelta(microseconds=1)
    with _reject():
        d.build_linear_risk_domain_coverage_v2(**i)


@pytest.mark.parametrize("which", ["mark", "account", "reservation", "tier", "margin", "computed"])
def test_future_and_reversed_receipts(which: str) -> None:
    i = _inputs()
    future = NOW + timedelta(microseconds=1)
    if which == "reservation":
        i["reservations"] = replace(i["reservations"], observed_at=future)
    elif which == "account":
        i["account"] = replace(
            i["account"], reference=replace(i["account"].reference, received_at=future)
        )
    else:
        first, second = i["members"]
        if which == "computed":
            first = replace(
                first,
                account_risk_reference=replace(first.account_risk_reference, computed_at=future),
            )
        elif which == "mark":
            mark = replace(first.mark, reference=replace(first.mark.reference, received_at=future))
            i["marks"] = (mark, i["marks"][1])
            first = replace(first, mark=mark)
        elif which == "tier":
            first = replace(
                first,
                tiers=replace(
                    first.tiers, reference=replace(first.tiers.reference, received_at=future)
                ),
            )
        else:
            first = replace(
                first,
                account_margin_reference=replace(
                    first.account_margin_reference, received_at=future
                ),
            )
        i["members"] = (first, second)
    with _reject("FUTURE_EVIDENCE"):
        d.build_linear_risk_domain_coverage_v2(**i)
    bad = _forge(
        _inputs()["members"][0].account_margin_reference, server_observed_at=future, received_at=NOW
    )
    i = _inputs()
    i["members"] = (replace(i["members"][0], account_margin_reference=bad), i["members"][1])
    with _reject("CAUSAL_ORDER_INVALID"):
        d.build_linear_risk_domain_coverage_v2(**i)


def test_fresh_computation_cannot_launder_old_source() -> None:
    test_all_visible_sources_fresh_independently("margin_server")


@pytest.mark.parametrize("which", ["mark", "tier", "margin", "computed"])
def test_public_private_environment_links(which: str) -> None:
    i = _inputs()
    first, second = i["members"]
    if which == "mark":
        mark = replace(
            first.mark, reference=replace(first.mark.reference, source_environment="PUBLIC_TESTNET")
        )
        i["marks"] = (mark, i["marks"][1])
        first = replace(first, mark=mark)
    elif which == "tier":
        first = replace(
            first,
            tiers=replace(
                first.tiers,
                reference=replace(first.tiers.reference, source_environment="PUBLIC_TESTNET"),
            ),
        )
    elif which == "margin":
        first = replace(
            first,
            account_margin_reference=replace(
                first.account_margin_reference, source_environment="LIVE"
            ),
        )
    else:
        first = replace(
            first,
            account_risk_reference=replace(first.account_risk_reference, target_environment="LIVE"),
        )
    i["members"] = (first, second)
    with _reject("SOURCE_MISMATCH"):
        d.build_linear_risk_domain_coverage_v2(**i)


def test_opaque_computation_hash_not_reconstructed_and_source_values_not_authenticated() -> None:
    i = _inputs()
    first = replace(
        i["members"][0],
        account_risk_reference=replace(
            i["members"][0].account_risk_reference, input_sha256="b" * 64
        ),
    )
    i["members"] = (first, i["members"][1])
    i["source_record_sha256s"] = ("0" * 64, "f" * 64)
    value = d.build_linear_risk_domain_coverage_v2(**i)
    assert value.source_record_sha256s == ("0" * 64, "f" * 64)
    assert value.members[0].account_risk_reference.input_sha256 == "b" * 64


def test_policy_scope_not_selection_authority_and_hash_has_no_original_input_proof() -> None:
    value = _coverage()
    # Standalone hash cannot recover original held Y/account totals or currentness.
    changed = replace(
        value,
        members=(value.members[0],),
        mark_set_sha256=c.mark_set_sha256_v2((value.members[0].mark,)),
        account_snapshot_sha256="0" * 64,
    )
    assert len(d.risk_domain_sha256_v2(changed)) == 64
    i = _inputs()
    i["members"], i["marks"] = changed.members, (changed.members[0].mark,)
    with _reject("HELD_COVERAGE_REQUIRED"):
        d.build_linear_risk_domain_coverage_v2(**i)


class _Hostile:
    def __eq__(self, other: object) -> bool:
        raise AssertionError("HOSTILE_EQUALITY")

    def __repr__(self) -> str:
        raise AssertionError("HOSTILE_REPR")

    def __iter__(self) -> Any:
        raise AssertionError("HOSTILE_ITERATION")


class _HostileTimezone(tzinfo):
    def utcoffset(self, dt: datetime | None) -> Any:
        raise AssertionError("HOSTILE_TIMEZONE")

    def dst(self, dt: datetime | None) -> Any:
        raise AssertionError("HOSTILE_TIMEZONE")

    def tzname(self, dt: datetime | None) -> str:
        raise AssertionError("HOSTILE_TIMEZONE")


@pytest.mark.parametrize(
    "bad", [_Hostile(), True, [], datetime(2026, 11, 3, tzinfo=_HostileTimezone())]
)
def test_typed_error_precedes_cross_record_comparison(bad: object) -> None:
    i = _inputs()
    # A competing earlier account mismatch must not mask malformed later member fields.
    i["reservations"] = replace(
        i["reservations"], account=replace(i["account"].account, account_id="OTHER")
    )
    field = "account_position_count" if type(bad) is bool else "account_risk_quote"
    if type(bad) is list:
        field = "account_component_ids"
    if type(bad) is datetime:
        bad = _forge(i["members"][1].account_risk_reference, computed_at=bad)
        field = "account_risk_reference"
    i["members"] = (i["members"][0], _forge(i["members"][1], **{field: bad}))
    with _reject():
        d.build_linear_risk_domain_coverage_v2(**i)
    with _reject():
        d.risk_domain_sha256_v2(_forge(_coverage(), members=i["members"]))


def test_missing_field_subclass_and_earlier_intrinsic_error_do_not_mask_later_structure() -> None:
    value = _coverage()
    incomplete = object.__new__(c.RiskComputationRefV2)
    first = replace(
        value.members[0],
        instrument_pin=replace(value.members[0].instrument_pin, catalog_revision="b" * 64),
    )
    last = replace(value.members[1], account_risk_reference=incomplete)
    with _reject():
        d.risk_domain_sha256_v2(replace(value, members=(first, last)))

    class Subclass(d.LinearRiskDomainCoverageV2):
        pass

    forged = object.__new__(Subclass)
    with _reject():
        d.risk_domain_sha256_v2(forged)


@pytest.mark.parametrize(
    "which", ["schema", "declared_order", "observation_order", "expiry", "revision", "max_age"]
)
def test_catalog_original_integrity_is_not_normalized(which: str) -> None:
    i = _inputs()
    catalog = i["catalog"]
    changes: dict[str, dict[str, Any]] = {
        "schema": {"schema_version": True},
        "declared_order": {"declared_universe": (Y, X)},
        "observation_order": {"observations": catalog.observations[::-1]},
        "expiry": {"expires_at": catalog.expires_at + timedelta(microseconds=1)},
        "revision": {"revision": "b" * 64},
        "max_age": {"max_age": 30},
    }
    i["catalog"] = _forge(catalog, **changes[which])
    with _reject():
        d.build_linear_risk_domain_coverage_v2(**i)


def test_zero_u_is_valid_and_counts_are_not_id_cardinalities() -> None:
    i = _inputs()
    i["account"] = replace(
        i["account"], components=(), total_risk=D(0), total_margin=D(0), position_count=0
    )
    i["reservations"] = replace(
        i["reservations"], components=(), risk_amount=D(0), margin_amount=D(0), position_count=0
    )
    i["members"] = (
        replace(
            i["members"][0],
            reservation_component_ids=(),
            reserved_risk_quote=D(0),
            reserved_margin_quote=D(0),
            reserved_position_count=0,
        ),
    )
    i["marks"] = i["marks"][:1]
    assert d.build_linear_risk_domain_coverage_v2(**i).members[0].account_position_count == 0


@pytest.mark.parametrize(
    "which",
    [
        "members",
        "marks",
        "account_components",
        "reservation_components",
        "member_ids",
        "source_records",
        "tier_rows",
        "global_ids",
    ],
)
def test_container_resource_limits(which: str) -> None:
    i = _inputs()
    if which in ("members", "marks"):
        i[which] = (i[which][0],) * 101
    elif which == "account_components":
        i["account"] = _forge(i["account"], components=i["account"].components * 1025)
    elif which == "reservation_components":
        i["reservations"] = _forge(
            i["reservations"], components=i["reservations"].components * 1025
        )
    elif which == "member_ids":
        i["members"] = (
            replace(i["members"][0], account_component_ids=("A",) * 1025),
            i["members"][1],
        )
    elif which == "source_records":
        i["source_record_sha256s"] = (DIGEST,) * 1025
    elif which == "tier_rows":
        first = replace(
            i["members"][0],
            tiers=_forge(i["members"][0].tiers, rows=i["members"][0].tiers.rows * 513),
        )
        i["members"] = (first, i["members"][1])
    else:
        i["members"] = tuple(
            replace(member, account_component_ids=tuple(f"A{index}_{n:04d}" for n in range(513)))
            for index, member in enumerate(i["members"])
        )
    with _reject("RESOURCE_LIMIT"):
        d.build_linear_risk_domain_coverage_v2(**i)


def test_derived_large_coefficient_and_decimal_spelling_context_preserved() -> None:
    value = _coverage()
    amount = D("1" * 257)
    changed = replace(
        value, members=(replace(value.members[0], account_risk_quote=amount), value.members[1])
    )
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = context.traps[Rounded] = True
        before = (context.prec, dict(context.traps), dict(context.flags))
        digest = d.risk_domain_sha256_v2(changed)
        assert before == (context.prec, dict(context.traps), dict(context.flags))
    assert len(digest) == 64
    assert digest != d.risk_domain_sha256_v2(
        replace(
            changed,
            members=(
                replace(changed.members[0], account_risk_quote=D("1" * 257 + ".0")),
                changed.members[1],
            ),
        )
    )


@pytest.mark.parametrize("bad", [D("1" * 257), D("1E-1001"), D("1E1001")])
def test_source_decimal_limits_remain_distinct(bad: D) -> None:
    i = _inputs()
    bad_mark = _forge(i["marks"][0], mark_price=bad)
    i["marks"] = (bad_mark, i["marks"][1])
    i["members"] = (replace(i["members"][0], mark=bad_mark), i["members"][1])
    with _reject("RESOURCE_LIMIT"):
        d.build_linear_risk_domain_coverage_v2(**i)


def test_whole_canonical_65536_byte_boundary() -> None:
    value = _coverage()
    # The size oracle uses the standard encoder and the already independently
    # verified scalar payload. A single decimal coefficient fills exact bytes.
    scalar = {
        "schema": "RISK_V2_CANONICAL_V1",
        "record_type": "LinearRiskDomainCoverageV2",
        "fields": d._coverage_payload_v2(value),
    }
    baseline = len(json.dumps(scalar, sort_keys=True, separators=(",", ":")).encode("ascii"))
    digits = 65536 - baseline + 1
    exact = replace(
        value,
        members=(replace(value.members[0], account_risk_quote=D("1" * digits)), value.members[1]),
    )
    assert len(d.risk_domain_sha256_v2(exact)) == 64
    oversized = replace(
        exact,
        members=(
            replace(exact.members[0], account_risk_quote=D("1" * (digits + 1))),
            exact.members[1],
        ),
    )
    with _reject("RESOURCE_LIMIT"):
        d.risk_domain_sha256_v2(oversized)


_MEMBER_MUTATIONS: list[tuple[tuple[str | int, ...], object, str | None]] = [
    (("instrument_pin", "key"), Y, "SCOPE_MISMATCH"),
    (("instrument_pin", "native_instrument_id"), "OTHER", "SCOPE_MISMATCH"),
    (("instrument_pin", "catalog_revision"), "b" * 64, "REVISION_MISMATCH"),
    (("instrument_pin", "specification_revision"), "b" * 64, "REVISION_MISMATCH"),
    (("instrument_pin", "parser_revision"), "OTHER", "REVISION_MISMATCH"),
    (("instrument_pin", "source_rule_revision"), "OTHER", "REVISION_MISMATCH"),
    (("instrument_pin", "source_id"), "OTHER", None),
    (("instrument_pin", "source_environment"), "PUBLIC_TESTNET", "SOURCE_MISMATCH"),
    (("instrument_pin", "response_time"), NOW - timedelta(seconds=2), None),
    (("instrument_pin", "received_at"), NOW + timedelta(microseconds=1), None),
    (("instrument_pin", "raw_row_sha256"), "b" * 64, None),
    (("mark", "instrument"), Y, "SCOPE_MISMATCH"),
    (("mark", "mark_price"), D("140.0"), None),
    (("tiers", "instrument"), Y, "SCOPE_MISMATCH"),
    (("tiers", "instrument_revision"), "b" * 64, "REVISION_MISMATCH"),
    (("tiers", "rows", 0, "tier_id"), "other", None),
    (("tiers", "rows", 0, "lower_exclusive_quote"), D(1), "INPUT_INVALID"),
    (("tiers", "rows", 0, "upper_inclusive_quote"), D(101), "INPUT_INVALID"),
    (("tiers", "rows", 0, "min_leverage"), D("1.1"), None),
    (("tiers", "rows", 0, "max_leverage"), D(2), None),
    (("tiers", "rows", 0, "initial_margin_rate"), D(".8"), None),
    (("tiers", "rows", 0, "maintenance_margin_rate"), D(".3"), None),
    (("tiers", "rows", 0, "maintenance_margin_deduction"), D(10), None),
    (("account_component_ids",), ("NEW",), None),
    (("reservation_component_ids",), ("NEW",), None),
    (("account_risk_quote",), D("0.0"), None),
    (("account_margin_quote",), D(1), None),
    (("account_position_count",), 1, None),
    (("reserved_risk_quote",), D(4), None),
    (("reserved_margin_quote",), D(5), None),
    (("reserved_position_count",), 4, None),
    (("account_risk_reference", "calculation_id"), "OTHER", None),
    (("account_risk_reference", "model_revision"), "OTHER", None),
    (("account_risk_reference", "target_environment"), "DEMO", None),
    (("account_risk_reference", "computed_at"), NOW - timedelta(microseconds=1), None),
    (("account_risk_reference", "input_sha256"), "b" * 64, None),
]
for _root_path in (("mark", "reference"), ("tiers", "reference"), ("account_margin_reference",)):
    for _name, _changed in (
        ("observation_id", "OTHER"),
        ("source_id", "OTHER"),
        ("source_rule_revision", "OTHER"),
        ("source_environment", "PUBLIC_TESTNET"),
        ("server_observed_at", NOW - timedelta(seconds=2)),
        ("received_at", NOW + timedelta(microseconds=1)),
        ("payload_sha256", "b" * 64),
    ):
        _MEMBER_MUTATIONS.append(
            (
                *(_root_path + (_name,), _changed),
                "SOURCE_MISMATCH" if _name == "source_environment" else None,
            )
        )


@pytest.mark.parametrize("path,changed,code", _MEMBER_MUTATIONS)
def test_every_nested_field_changes_hash_or_fixed_rejection(
    path: tuple[str | int, ...],
    changed: object,
    code: str | None,
) -> None:
    value = _coverage()
    member = _set_path(value.members[0], path, changed)
    mutated = replace(value, members=(member, value.members[1]))
    if code is None:
        if path[0] == "mark":
            mutated = replace(
                mutated, mark_set_sha256=c.mark_set_sha256_v2((member.mark, value.members[1].mark))
            )
        assert d.risk_domain_sha256_v2(mutated) != d.risk_domain_sha256_v2(value)
    else:
        with _reject(code):
            d.risk_domain_sha256_v2(mutated)


@pytest.mark.parametrize(
    "field,changed,code",
    [
        ("account_scope", ProtectionScope("OTHER", "DEMO"), None),
        ("catalog_revision", "b" * 64, "REVISION_MISMATCH"),
        ("selected_entry_key", Y, None),
        ("coverage_revision", "OTHER", None),
        ("source_generation", "OTHER", None),
        ("account_snapshot_sha256", "b" * 64, None),
        ("reservation_snapshot_sha256", "b" * 64, None),
        ("mark_set_sha256", "b" * 64, "SOURCE_MISMATCH"),
        ("source_record_sha256s", ("b" * 64,), None),
        ("members", (), "INPUT_INVALID"),
    ],
)
def test_every_coverage_field_changes_hash_or_fixed_rejection(
    field: str,
    changed: object,
    code: str | None,
) -> None:
    value = _coverage()
    mutated = _forge(value, **{field: changed})
    if code is None:
        assert d.risk_domain_sha256_v2(mutated) != d.risk_domain_sha256_v2(value)
    else:
        with _reject(code):
            d.risk_domain_sha256_v2(mutated)


def test_source_record_maximum_values_bound_before_whole_byte_limit() -> None:
    sources = tuple(f"{n:064x}" for n in range(1024))
    assert d._source_record_sha256s_v2(sources) == sources
    with _reject("RESOURCE_LIMIT"):
        d.risk_domain_sha256_v2(replace(_coverage(), source_record_sha256s=sources))


def test_1024_member_ids_and_global_duplicates_or_overlap() -> None:
    value = _coverage()
    ids = tuple(f"A{n:04d}" for n in range(1024))
    member = replace(value.members[0], account_component_ids=ids, reservation_component_ids=())
    other = replace(value.members[1], account_component_ids=())
    assert len(d.risk_domain_sha256_v2(replace(value, members=(member, other)))) == 64
    for bad in (
        (replace(member, account_component_ids=("AY",)), value.members[1]),
        (replace(member, account_component_ids=("RX",), reservation_component_ids=("RX",)), other),
    ):
        with _reject("EXPOSURE_OVERLAP"):
            d.risk_domain_sha256_v2(replace(value, members=bad))


@pytest.mark.parametrize(
    "which", ["mark", "catalog", "account_overlap", "pin", "tier_revision", "mode", "scope"]
)
def test_supplied_fixed_error_boundaries(which: str) -> None:
    i = _inputs()
    code = "SOURCE_MISMATCH"
    if which == "mark":
        i["marks"] = i["marks"][:1]
        code = "MARK_COVERAGE_REQUIRED"
    elif which == "catalog":
        catalog = build_instrument_catalog(
            i["catalog"].observations[:1],
            declared_universe=(X,),
            now=NOW,
            max_age=timedelta(seconds=30),
        )
        i["catalog"], i["catalog_revision"] = catalog, catalog.revision
        code = "HELD_COVERAGE_REQUIRED"
    elif which == "account_overlap":
        res = i["reservations"]
        i["reservations"] = replace(
            res, components=(replace(res.components[0], component_id="AY"),)
        )
        code = "EXPOSURE_OVERLAP"
    elif which == "pin":
        i["members"] = (
            replace(
                i["members"][0],
                instrument_pin=replace(i["members"][0].instrument_pin, raw_row_sha256="b" * 64),
            ),
            i["members"][1],
        )
    elif which == "tier_revision":
        first = i["members"][0]
        i["members"] = (
            replace(first, tiers=replace(first.tiers, instrument_revision="b" * 64)),
            i["members"][1],
        )
        code = "REVISION_MISMATCH"
    elif which == "mode":
        i["account"] = replace(i["account"], margin_mode="CROSS")
        code = "SCOPE_MISMATCH"
    else:
        i["reservations"] = replace(i["reservations"], account=ProtectionScope("OTHER", "DEMO"))
        code = "SCOPE_MISMATCH"
    with _reject(code) as caught:
        d.build_linear_risk_domain_coverage_v2(**i)
    assert str(caught.value) == caught.value.code


def test_unsupported_product_is_not_filtered() -> None:
    i = _inputs()
    spot = InstrumentKey("BYBIT", "SPOT", "YBBUSDT")
    i["account"] = _forge(
        i["account"], components=(_forge(i["account"].components[0], instrument=spot),)
    )
    with _reject("UNSUPPORTED_PRODUCT"):
        d.build_linear_risk_domain_coverage_v2(**i)


@pytest.mark.parametrize("which", ["digits", "scale"])
def test_a02_decimal_limits_are_public_catalog_rejections(which: str) -> None:
    i = _inputs()
    observation = i["catalog"].observations[0]
    raw = "1" * 65 if which == "digits" else "0." + "1" * 33
    bad = _forge(
        observation.spec.price_tick, raw=raw, value=D(raw), scale=0 if which == "digits" else 33
    )
    observation = _forge(observation, spec=_forge(observation.spec, price_tick=bad))
    i["catalog"] = _forge(i["catalog"], observations=(observation, i["catalog"].observations[1]))
    with _reject():
        d.build_linear_risk_domain_coverage_v2(**i)


def test_tier_row_maximum_preserves_range_order_and_hits_byte_cap() -> None:
    value = _coverage()
    rows = tuple(
        e.LinearRiskTierV2(
            f"T{1024 - index:04d}", D(index), D(index + 1), D(1), D(2), D(".5"), D(".2"), D(0)
        )
        for index in range(1024)
    )
    tier = replace(value.members[0].tiers, rows=rows)
    assert len(e._tier_snapshot_payload_v2(tier)["rows"]) == 1024  # type: ignore[arg-type]
    with _reject("RESOURCE_LIMIT"):
        d.risk_domain_sha256_v2(
            replace(value, members=(replace(value.members[0], tiers=tier), value.members[1]))
        )


def test_source_list_and_member_ids_must_be_canonical_not_normalized() -> None:
    value = _coverage()
    for ids in (("Z", "A"), ("A", "A"), ("",), ("한글",)):
        with _reject():
            d.risk_domain_sha256_v2(
                replace(
                    value,
                    members=(
                        replace(value.members[0], account_component_ids=ids),
                        value.members[1],
                    ),
                )
            )


def test_100_domain_member_bound_is_independent_from_byte_cap() -> None:
    value = _coverage()
    # Exactly 100 is within the container budget; duplicate keys fail structure/order.
    with _reject():
        d.risk_domain_sha256_v2(replace(value, members=(value.members[0],) * 100))
    with _reject("RESOURCE_LIMIT"):
        d.risk_domain_sha256_v2(replace(value, members=(value.members[0],) * 101))


def test_math_resource_guards_and_fixed_unknown_error_constructor() -> None:
    value = _coverage()
    for bad in (D((0, (1,), 65537)), D((0, (1,) * 65537, 0))):
        with _reject("RESOURCE_LIMIT"):
            d.risk_domain_sha256_v2(
                replace(
                    value,
                    members=(replace(value.members[0], account_risk_quote=bad), value.members[1]),
                )
            )
    with _reject("RESOURCE_LIMIT"):
        d.risk_domain_sha256_v2(
            replace(
                value,
                members=(
                    replace(value.members[0], account_position_count=1 << 1_000_000),
                    value.members[1],
                ),
            )
        )
    with pytest.raises(ValueError, match="^INVALID_RISK_V2_REJECTION_CODE$"):
        c.RiskV2Rejected("UNKNOWN")


def test_private_member_payload_also_checks_structure_before_intrinsic_links() -> None:
    member = _coverage().members[0]
    member = replace(
        member,
        instrument_pin=_forge(member.instrument_pin, parser_revision="OTHER"),
        account_position_count=True,
    )
    with _reject():
        d._member_payload_v2(member)
