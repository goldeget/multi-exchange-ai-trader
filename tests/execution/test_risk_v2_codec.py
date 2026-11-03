"""Independent synthetic full-wire goldens and closed-boundary tests."""

import hashlib
import json
import sys
from copy import deepcopy
from dataclasses import MISSING, FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, getcontext, localcontext
from functools import partial
from typing import Any, Literal, cast

import pytest

from multi_exchange_trader.execution import linear_risk_evidence_v2 as e
from multi_exchange_trader.execution import risk_v2_contracts as c
from multi_exchange_trader.execution.modes import StrategyReference
from multi_exchange_trader.execution.product_scope import ProductScope
from multi_exchange_trader.execution.protection import ProtectionPolicy, ProtectionScope
from multi_exchange_trader.execution.risk_v2_codec import (
    DurableEntryIntentV2,
    decode_entry_intent_v2,
    encode_entry_intent_v2,
)
from multi_exchange_trader.trading_product import InstrumentKey

NOW = datetime(2026, 11, 3, tzinfo=UTC)
EXPIRY = NOW + timedelta(seconds=1)
H = "a" * 64


def actual_intent(branch: str = "PLAN") -> DurableEntryIntentV2:
    """Actual graph uses accepted constructors, separately from literal expectations."""
    strategy = StrategyReference("STRATEGY", "STRATEGY_V1")
    key = InstrumentKey("BYBIT", "LINEAR", "XAAUSDT")
    scope = c.RiskScopeV2(ProductScope(ProtectionScope("한글", "DEMO"), "LINEAR"), key, strategy)
    pin = c.InstrumentPinV2(
        key,
        "XAAUSDT-LINEAR.BYBIT",
        "CATALOG_V1",
        H,
        "BYBIT_INSTRUMENT_PARSER_V1",
        "BYBIT_LINEAR_USDT_PERPETUAL_V1",
        "SOURCE",
        "FIXTURE",
        NOW,
        NOW,
        H,
    )
    envelope = c.FixedLimitEnvelopeV2(
        2,
        scope,
        pin,
        "POLICY_V1",
        H,
        "BUY",
        Decimal("1.00"),
        Decimal("100.00"),
        "LIMIT",
        "IOC",
        "ENTRY",
        False,
        "QUOTE",
        EXPIRY,
    )
    cost = c.RiskComputationRefV2("COST", "COST_V1", "FIXTURE", NOW, H)
    sizing = c.LinearSizedEntryV2(
        scope,
        NOW,
        H,
        envelope,
        cost,
        H,
        Decimal("90.00"),
        Decimal("10.00"),
        Decimal("2.00"),
        Decimal("12.00"),
        Decimal("12.0000"),
        Decimal("2.0000"),
        Decimal("0.50"),
        "LINEAR_FIXED_LIMIT_LOSS_SIZE_V2",
    )
    valuation = c.LinearAdmissionValuationV2(
        scope,
        NOW,
        "ACCOUNT",
        "RESERVATIONS",
        ("MARK",),
        Decimal("-0.00"),
        Decimal(0),
        Decimal("100.00"),
        Decimal("100.00"),
        "LINEAR_MARK_GROSS_AT_ADMISSION_V1",
    )
    tier = e.LinearTierBoundV2(
        key,
        "TIERS",
        "MARK",
        Decimal(100),
        ("TIER_Z", "TIER_A"),
        Decimal(1),
        Decimal(10),
        Decimal("0.1"),
        "LOCAL_ZERO_ORIGIN_LATTICE_V1",
        Decimal("1.0"),
        Decimal("10.0"),
        "LINEAR_MARK_GROSS_UPPER_BOUND_V1",
    )
    minimum = e.MinimumAmountAssessmentV2(
        scope,
        c.RiskComputationRefV2("MINIMUM", "MINIMUM_V1", "FIXTURE", NOW, H),
        c.envelope_sha256_v2(envelope),
        H,
        "MINIMUM_V1",
        ("MARK",),
        Decimal("100.00"),
        Decimal(5),
        Decimal("100.0000"),
        EXPIRY,
    )
    binding = e.LinearRiskBindingV2(
        scope,
        H,
        c.envelope_sha256_v2(envelope),
        c.sizing_sha256_v2(sizing),
        pin,
        "MAPPING_V1",
        "COST",
        H,
        "ACCOUNT",
        H,
        "COVERAGE_V1",
        "RESERVATIONS",
        H,
        "OWNER_1",
        H,
        c.valuation_sha256_v2(valuation),
        "TIERS",
        H,
        e.tier_bound_sha256_v2(tier),
        H,
        e.minimum_assessment_sha256_v2(minimum),
        H,
    )
    preparation = e.LinearEntryPreparationV2(
        sizing,
        valuation,
        tier,
        minimum,
        binding,
        Decimal(60),
        Decimal("0.012"),
        "RISK_V2_EXACT_FINITE_ELSE_CEILING80_V1",
    )
    margin = None
    liquidation = None
    readback = None
    reserve = None
    buffer_result: Literal["GAP_SATISFIED", "MODEL_NO_POSITIVE_TRIGGER"] | None = None
    blockers: tuple[str, ...] = (
        "RISK_V2_MARGIN_UNVERIFIED",
        "RISK_V2_LIQUIDATION_UNVERIFIED",
        "RISK_V2_LEVERAGE_READBACK_UNVERIFIED",
    )
    if branch != "PLAN":
        binding_hash = e.binding_sha256_v2(binding)
        margin = e.LinearMarginAssessmentV2(
            binding_hash,
            c.RiskComputationRefV2("MARGIN", "MARGIN_V1", "FIXTURE", NOW, binding_hash),
            "MARGIN_V1",
            Decimal("1.00"),
            Decimal("2.0"),
            "LOCAL_ZERO_ORIGIN_LATTICE_V1",
            "SMALLEST_FEASIBLE_PERMITTED_GRID_V1",
            Decimal("50.00000000"),
            Decimal("52.00000000"),
            ("TIER_Z", "TIER_A"),
            EXPIRY,
            "POINT_ESTIMATE_TWO_IM_ENVELOPE_V1",
            "APP_RESERVE_CEILING_QUANTUM_V1",
            Decimal("1E-8"),
            H,
            H,
        )
        outcome: e.LiquidationPositiveRootV2 | e.LiquidationNoPositiveRootV2
        if branch == "POSITIVE_ROOT":
            outcome = e.LiquidationPositiveRootV2(
                "POSITIVE_ROOT",
                e.ExactRiskRatioV2(Decimal(200), Decimal(3)),
                "TIER_Z",
                Decimal(1000),
                H,
                H,
                ("TIER_Z", "TIER_A"),
                "LP_V1",
            )
            buffer_result = "GAP_SATISFIED"
        else:
            outcome = e.LiquidationNoPositiveRootV2(
                "NO_POSITIVE_ROOT",
                "NON_POSITIVE_LONG_INTERCEPT",
                Decimal(1000),
                H,
                H,
                ("TIER_Z", "TIER_A"),
                "LP_V1",
                H,
                H,
            )
            buffer_result = "MODEL_NO_POSITIVE_TRIGGER"
        margin_hash = e.margin_assessment_sha256_v2(margin)
        liquidation = e.LinearLiquidationAssessmentV2(
            binding_hash,
            margin_hash,
            c.RiskComputationRefV2("LP", "LP_V1", "FIXTURE", NOW, margin_hash),
            "LP_V1",
            Decimal("1.00"),
            Decimal("2.0"),
            "LONG",
            Decimal("100.00"),
            "ISOLATED",
            "ONE_WAY",
            ("TIER_Z", "TIER_A"),
            outcome,
            EXPIRY,
            H,
            H,
        )
        readback = e.LinearLeverageReadbackV2(
            binding_hash,
            c.RiskObservationRefV2("READBACK", "SOURCE", "FIXTURE", "SOURCE_V1", NOW, NOW, H),
            "ACCOUNT",
            "CONTROL",
            NOW,
            "LONG",
            Decimal("2.0"),
            "ISOLATED",
            "ONE_WAY",
        )
        reserve = Decimal("52.00000000")
        blockers = ()
    assessment = e.LinearEntryAssessmentV2(
        "PLAN" if branch == "PLAN" else "SUBMIT",
        NOW,
        preparation,
        margin,
        liquidation,
        readback,
        reserve,
        blockers,
        "LINEAR_FIXED_LIMIT_MARK_ADMISSION_V2",
        buffer_result,
    )
    return DurableEntryIntentV2(
        2,
        "COMMAND",
        "POSITION",
        "PLAN",
        strategy,
        envelope,
        assessment,
        ProtectionPolicy("POLICY_V1", 0, Decimal("90.00"), Decimal("120.000"), "MARK_PRICE"),
        ("0" * 64, H),
    )


def test_intent_has_exact_nine_required_frozen_fields() -> None:
    expected = (
        "schema_version",
        "command_key",
        "position_id",
        "plan_id",
        "strategy",
        "envelope",
        "assessment",
        "protection_policy",
        "source_record_sha256s",
    )
    assert tuple(f.name for f in fields(DurableEntryIntentV2)) == expected
    assert all(
        f.default is MISSING and f.default_factory is MISSING for f in fields(DurableEntryIntentV2)
    )
    with pytest.raises(TypeError):
        DurableEntryIntentV2()  # type: ignore[call-arg]
    intent = actual_intent()
    with pytest.raises(FrozenInstanceError):
        intent.command_key = "OTHER"  # type: ignore[misc]


@pytest.mark.parametrize("branch", ("PLAN", "POSITIVE_ROOT", "NO_POSITIVE_ROOT"))
def test_three_actual_graphs_construct_before_codec_behavior(branch: str) -> None:
    intent = actual_intent(branch)
    assert type(intent.assessment) is e.LinearEntryAssessmentV2
    assert c.envelope_sha256_v2(intent.envelope) == GOLDEN_HASHES["envelope"]
    assert e.binding_sha256_v2(intent.assessment.preparation.binding) == GOLDEN_HASHES["binding"]
    if intent.assessment.margin is not None:
        assert e.margin_assessment_sha256_v2(intent.assessment.margin) == GOLDEN_HASHES["margin"]


@pytest.mark.parametrize("branch", ("PLAN", "POSITIVE_ROOT", "NO_POSITIVE_ROOT"))
def test_complete_literal_wire_golden(branch: str) -> None:
    intent = actual_intent(branch)
    raw = GOLDEN_WIRES[branch]
    assert len(raw.encode("ascii")) == GOLDEN_LENGTHS[branch]
    assert hashlib.sha256(raw.encode("ascii")).hexdigest() == GOLDEN_SHA256S[branch]
    assert encode_entry_intent_v2(intent) == raw
    restored = decode_entry_intent_v2(raw)
    assert type(restored) is DurableEntryIntentV2
    assert restored.envelope.base_quantity.as_tuple() == (0, (1, 0, 0), -2)
    assert restored.assessment.preparation.valuation.account_gross_quote.as_tuple() == (1, (0,), -2)
    assert restored.envelope.instrument_pin.response_time == NOW
    assert_literal_leaves(restored, json.loads(raw)["fields"])
    assert encode_entry_intent_v2(restored) == raw


def assert_literal_leaves(value: Any, literal: Any) -> None:
    """Every Decimal tuple and UTC leaf comes from the independent full wire."""
    if type(value) is Decimal:
        assert value.as_tuple() == Decimal(literal).as_tuple()
    elif type(value) is datetime:
        assert value.tzinfo is UTC
        assert value.isoformat(timespec="microseconds") == literal
    elif type(value) is tuple:
        assert list(value) == literal
    elif hasattr(type(value), "__dataclass_fields__"):
        for field in fields(value):
            assert_literal_leaves(object.__getattribute__(value, field.name), literal[field.name])
    else:
        assert type(value) is type(literal)
        assert value == literal


def canonical_frame(tree: dict[str, Any]) -> str:
    body = {k: v for k, v in tree.items() if k != "content_sha256"}
    raw = json.dumps(body, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    body["content_sha256"] = hashlib.sha256(raw.encode("ascii")).hexdigest()
    return json.dumps(body, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def reject(call: Any, code: str = "RISK_V2_INPUT_INVALID") -> None:
    with pytest.raises(c.RiskV2Rejected) as error:
        call()
    assert type(error.value) is c.RiskV2Rejected
    assert error.value.code == code


def forged(value: Any, name: str, replacement: Any) -> Any:
    result = object.__new__(type(value))
    for f in fields(value):
        object.__setattr__(
            result,
            f.name,
            replacement if f.name == name else object.__getattribute__(value, f.name),
        )
    return result


@pytest.mark.parametrize(
    "generation,code",
    [
        (True, "RISK_V2_INPUT_INVALID"),
        (-1, "RISK_V2_INPUT_INVALID"),
        (2**63, "RISK_V2_RESOURCE_LIMIT"),
        (10**5000, "RISK_V2_RESOURCE_LIMIT"),
    ],
    ids=["bool", "negative", "max-plus-one", "5001-digits"],
)
def test_generation_constructor_encoder_decoder(generation: Any, code: str) -> None:
    intent = actual_intent()
    policy = forged(intent.protection_policy, "generation", generation)
    reject(lambda: replace(intent, protection_policy=policy), code)
    reject(lambda: encode_entry_intent_v2(forged(intent, "protection_policy", policy)), code)
    if type(generation) is int and generation.bit_length() <= 64:
        tree = json.loads(GOLDEN_WIRES["PLAN"])
        tree["fields"]["protection_policy"]["generation"] = generation
        reject(lambda: decode_entry_intent_v2(canonical_frame(tree)), code)


def test_generation_max_and_historical_inert_stages() -> None:
    for branch in ("PLAN", "POSITIVE_ROOT", "NO_POSITIVE_ROOT"):
        intent = actual_intent(branch)
        intent = replace(
            intent, protection_policy=replace(intent.protection_policy, generation=2**63 - 1)
        )
        restored = decode_entry_intent_v2(encode_entry_intent_v2(intent))
        assert restored.protection_policy.generation == 9223372036854775807
        assert restored.assessment.stage == intent.assessment.stage
        assert not hasattr(restored, "receipt")
        assert not hasattr(restored, "qualified")


@pytest.mark.parametrize(
    "value,code",
    [
        ([], "RISK_V2_INPUT_INVALID"),
        ((), "RISK_V2_INPUT_INVALID"),
        ((H, H), "RISK_V2_INPUT_INVALID"),
        ((H, "0" * 64), "RISK_V2_INPUT_INVALID"),
        (("A" * 64,), "RISK_V2_INPUT_INVALID"),
        (("bad",), "RISK_V2_INPUT_INVALID"),
        ((1,), "RISK_V2_INPUT_INVALID"),
        ((H,) * 1025, "RISK_V2_RESOURCE_LIMIT"),
    ],
)
def test_source_refs(value: Any, code: str) -> None:
    reject(lambda: replace(actual_intent(), source_record_sha256s=value), code)


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema",), "FUTURE"),
        (("wire_version",), True),
        (("wire_version",), 3),
        (("record_type",), "RiskAssessmentReceiptV2"),
        (("fields", "schema_version"), True),
        (("fields", "envelope", "base_quantity"), 1.0),
        (("fields", "envelope", "limit_price"), "1e2"),
        (("fields", "envelope", "limit_price"), "+100.00"),
        (("fields", "envelope", "valid_until"), "2026-11-03T00:00:01Z"),
        (("fields", "protection_policy", "generation"), 9223372036854775808),
        (("fields", "assessment", "liquidation", "outcome", "exact_price", "numerator"), "200.0"),
        (("fields", "assessment", "liquidation", "outcome", "exact_price", "denominator"), "0"),
        (("fields", "assessment", "margin", "model_input_sha256"), "b" * 64),
        (("fields", "assessment", "liquidation", "margin_assessment_sha256"), "b" * 64),
        (("fields", "assessment", "preparation", "binding", "sizing_sha256"), "b" * 64),
        (("fields", "envelope", "base_quantity"), "1.0"),
        (("fields", "protection_policy", "stop_price"), "90.0"),
        (("fields", "protection_policy", "take_profit_price"), "99"),
    ],
)
def test_recomputed_checksum_does_not_hide_invalid_graph(path: tuple[str, ...], value: Any) -> None:
    tree = json.loads(GOLDEN_WIRES["POSITIVE_ROOT"])
    node = tree
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    stale = json.dumps(tree, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    for raw in (stale, canonical_frame(tree)):
        with pytest.raises(c.RiskV2Rejected):
            decode_entry_intent_v2(raw)


@pytest.mark.parametrize(
    "needle",
    [
        '"schema":',
        '"command_key":',
        '"product_scope":',
        '"numerator":',
    ],
)
def test_decoded_duplicate_aliases_at_every_depth(needle: str) -> None:
    raw = GOLDEN_WIRES["POSITIVE_ROOT"]
    name = needle[1:-2]
    alias = f'"\\u{ord(name[0]):04x}{name[1:]}":null,'
    raw = raw.replace(needle, alias + needle, 1)
    reject(lambda: decode_entry_intent_v2(raw))


@pytest.mark.parametrize(
    "raw,code",
    [
        ("", "RISK_V2_INPUT_INVALID"),
        ("{", "RISK_V2_INPUT_INVALID"),
        (" " * 65537, "RISK_V2_RESOURCE_LIMIT"),
        ("한" * 30000, "RISK_V2_RESOURCE_LIMIT"),
        ("\ud800", "RISK_V2_INPUT_INVALID"),
        ("[" * 33 + "]" * 33, "RISK_V2_RESOURCE_LIMIT"),
        ('{"a":' + "1" * 20 + "}", "RISK_V2_RESOURCE_LIMIT"),
        ('{"a":NaN}', "RISK_V2_INPUT_INVALID"),
        ('{"a":Infinity}', "RISK_V2_INPUT_INVALID"),
        ('{"a":1.2}', "RISK_V2_INPUT_INVALID"),
    ],
)
def test_wire_resource_and_syntax(raw: str, code: str) -> None:
    reject(lambda: decode_entry_intent_v2(raw), code)


def graph_records(value: Any, prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    result = [(prefix, value)]
    for f in fields(value):
        child = object.__getattribute__(value, f.name)
        if hasattr(type(child), "__dataclass_fields__"):
            result.extend(graph_records(child, prefix + (f.name,)))
    return result


def replace_path(root: Any, path: tuple[str, ...], value: Any) -> Any:
    if not path:
        return value
    child = object.__getattribute__(root, path[0])
    return forged(root, path[0], replace_path(child, path[1:], value))


def test_all_24_contextual_classes_missing_and_subclass_hooks() -> None:
    seen = set()
    for branch in ("POSITIVE_ROOT", "NO_POSITIVE_ROOT"):
        root = actual_intent(branch)
        for path, node in graph_records(root):
            if type(node) in seen:
                continue
            seen.add(type(node))
            absent = object.__new__(type(node))
            reject(partial(encode_entry_intent_v2, replace_path(root, path, absent)))
            if path:
                changed = replace_path(root, path, absent)
                reject(
                    partial(replace, root, **{path[0]: object.__getattribute__(changed, path[0])})
                )

            def forbidden(self: Any, name: str) -> Any:
                raise AssertionError("untrusted record hook ran")

            subclass = type("Hostile", (type(node),), {"__getattribute__": forbidden})
            hostile: Any = object.__new__(subclass)
            reject(partial(encode_entry_intent_v2, replace_path(root, path, hostile)))
    assert len(seen) == 24


def test_scalar_hooks_and_preflight_before_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from multi_exchange_trader.execution import risk_v2_codec as codec

    root = actual_intent("POSITIVE_ROOT")
    huge = forged(root.assessment.preparation, "capacity_quote", Decimal((0, (1,) * 65536, 0)))
    root = forged(root, "assessment", forged(root.assessment, "preparation", huge))

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("payload/formatting ran before preflight")

    monkeypatch.setattr(c, "_envelope_payload_v2", forbidden)
    monkeypatch.setattr(e, "_assessment_payload_v2", forbidden)
    monkeypatch.setattr(codec, "decimal_text_v2", forbidden)
    reject(lambda: encode_entry_intent_v2(root), "RISK_V2_RESOURCE_LIMIT")


def test_every_context_unknown_missing_extra_and_wrong_slot() -> None:
    def mappings(value: Any, path: tuple[Any, ...] = ()) -> list[tuple[tuple[Any, ...], Any]]:
        result = []
        if type(value) is dict:
            result.append((path, value))
            for name, child in value.items():
                result.extend(mappings(child, path + (name,)))
        elif type(value) is list:
            for index, child in enumerate(value):
                result.extend(mappings(child, path + (index,)))
        return result

    for branch in ("POSITIVE_ROOT", "NO_POSITIVE_ROOT"):
        original = json.loads(GOLDEN_WIRES[branch])
        for path, mapping in mappings(original):
            for mode in ("extra", "missing", "wrong_slot"):
                tree = deepcopy(original)
                node = tree
                for part in path:
                    node = node[part]
                if mode == "extra":
                    node["future"] = "unknown"
                elif mode == "missing":
                    del node[next(iter(mapping))]
                elif path:
                    parent = tree
                    for part in path[:-1]:
                        parent = parent[part]
                    parent[path[-1]] = []
                else:
                    continue
                # Recomputing a deliberately removed outer checksum would repair
                # that fixture. Every other contextual mutation recomputes it.
                raw = json.dumps(tree, separators=(",", ":")) if not path else canonical_frame(tree)
                reject(partial(decode_entry_intent_v2, raw))


def test_canonical_aliases_and_scalar_subclasses() -> None:
    tree = json.loads(GOLDEN_WIRES["PLAN"])
    assert decode_entry_intent_v2(canonical_frame(tree)).assessment.stage == "PLAN"
    for raw in (
        json.dumps(tree),
        GOLDEN_WIRES["PLAN"] + "\n",
        GOLDEN_WIRES["PLAN"].replace('"generation":0', '"generation":-0'),
    ):
        reject(partial(decode_entry_intent_v2, raw))
    root = actual_intent()

    class HostileDecimal(Decimal):
        def as_tuple(self) -> Any:
            raise AssertionError("Decimal subclass hook")

    class HostileTime(datetime):
        def astimezone(self, tz: Any = None) -> Any:
            raise AssertionError("datetime subclass hook")

    for bad in (HostileDecimal("90"), 90, True):
        policy = forged(root.protection_policy, "stop_price", bad)
        reject(partial(replace, root, protection_policy=policy))
    bad_time = HostileTime(2026, 11, 3, tzinfo=UTC)
    envelope = forged(root.envelope, "valid_until", bad_time)
    reject(lambda: replace(root, envelope=envelope))
    reject(lambda: replace(root, schema_version=cast(Any, True)))


def test_source_and_derived_decimal_limits_and_hostile_context() -> None:
    root = actual_intent()
    for bad in (Decimal((0, (1,) * 257, 0)), Decimal("1E+1001")):
        policy = forged(root.protection_policy, "stop_price", bad)
        reject(partial(replace, root, protection_policy=policy), "RISK_V2_RESOURCE_LIMIT")
    preparation = replace(root.assessment.preparation, capacity_quote=Decimal("1" * 300))
    root = replace(root, assessment=replace(root.assessment, preparation=preparation))
    before_limit = sys.get_int_max_str_digits()
    with localcontext() as context:
        context.prec = 1
        for signal in context.traps:
            context.traps[signal] = True
        context.clear_flags()
        before = context.copy()
        raw = encode_entry_intent_v2(root)
        restored = decode_entry_intent_v2(raw)
        assert restored.assessment.preparation.capacity_quote.as_tuple() == (0, (1,) * 300, 0)
        assert context.prec == before.prec
        assert context.traps == before.traps
        assert context.flags == before.flags
    assert sys.get_int_max_str_digits() == before_limit
    assert getcontext().prec > 1


def test_full_wire_exact_65536_then_65537_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    from multi_exchange_trader.execution import risk_v2_codec as codec

    root = actual_intent()
    # capacity_quote is an existing derived value, unbound by a semantic digest.
    # Its original literal is 60 (two characters); every other byte is frozen.
    count = 65536 - GOLDEN_LENGTHS["PLAN"] + 2
    preparation = replace(root.assessment.preparation, capacity_quote=Decimal((0, (5,) * count, 0)))
    root = replace(root, assessment=replace(root.assessment, preparation=preparation))
    raw = encode_entry_intent_v2(root)
    assert len(raw) == 65536
    assert encode_entry_intent_v2(decode_entry_intent_v2(raw)) == raw
    excessive = forged(preparation, "capacity_quote", Decimal((0, (5,) * (count + 1), 0)))
    excessive_root = forged(root, "assessment", forged(root.assessment, "preparation", excessive))

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("formatting/helper ran for 65537 bytes")

    monkeypatch.setattr(c, "_envelope_payload_v2", forbidden)
    monkeypatch.setattr(e, "_assessment_payload_v2", forbidden)
    monkeypatch.setattr(codec, "decimal_text_v2", forbidden)
    reject(lambda: encode_entry_intent_v2(excessive_root), "RISK_V2_RESOURCE_LIMIT")


def test_repeated_graph_occurrences_count_before_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    root = actual_intent()
    # One shared scope appears six times in the complete wire.
    account = forged(root.envelope.scope.product_scope.account, "account_id", "한" * 256)
    product = forged(root.envelope.scope.product_scope, "account", account)
    scope = forged(root.envelope.scope, "product_scope", product)
    # Repeated immutable envelope quantity occurs at root and under sizing.
    quantity = Decimal((0, (1,) * 30000, 0))
    env = forged(forged(root.envelope, "scope", scope), "base_quantity", quantity)
    sizing = forged(root.assessment.preparation.sizing, "envelope", env)
    preparation = forged(root.assessment.preparation, "sizing", sizing)
    root = forged(
        forged(root, "envelope", env),
        "assessment",
        forged(root.assessment, "preparation", preparation),
    )

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("helpers ran before repeated aggregate preflight")

    monkeypatch.setattr(c, "_envelope_payload_v2", forbidden)
    monkeypatch.setattr(e, "_assessment_payload_v2", forbidden)
    reject(lambda: encode_entry_intent_v2(root), "RISK_V2_RESOURCE_LIMIT")


def test_decoder_preflight_before_parse_and_constructor(monkeypatch: pytest.MonkeyPatch) -> None:
    from multi_exchange_trader.execution import risk_v2_codec as codec

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("parser or constructor reached before its preflight")

    with monkeypatch.context() as patch:
        patch.setattr(json, "loads", forbidden)
        for raw in ("[" * 33 + "]" * 33, "1" * 20, " " * 65537):
            reject(partial(decode_entry_intent_v2, raw), "RISK_V2_RESOURCE_LIMIT")
    tree = json.loads(GOLDEN_WIRES["PLAN"])
    tree["fields"]["assessment"]["preparation"]["capacity_quote"] = "1E+99999999999999"
    raw = canonical_frame(tree)
    monkeypatch.setattr(codec, "Decimal", forbidden)
    reject(lambda: decode_entry_intent_v2(raw), "RISK_V2_RESOURCE_LIMIT")


def test_coherent_opaque_proof_rewrite_remains_inert() -> None:
    tree = json.loads(GOLDEN_WIRES["NO_POSITIVE_ROOT"])
    tree["fields"]["assessment"]["liquidation"]["outcome"]["proof_sha256"] = "b" * 64
    tree["fields"]["plan_id"] = "OTHER_OPAQUE_PLAN"
    restored = decode_entry_intent_v2(canonical_frame(tree))
    assert type(restored.assessment.liquidation) is e.LinearLiquidationAssessmentV2
    assert type(restored.assessment.liquidation.outcome) is e.LiquidationNoPositiveRootV2
    assert restored.assessment.liquidation.outcome.proof_sha256 == "b" * 64
    assert not hasattr(restored, "qualified")
    assert not hasattr(restored, "receipt")


def test_1024_source_references_over_whole_budget_before_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = actual_intent()
    root = forged(root, "source_record_sha256s", tuple(f"{index:064x}" for index in range(1024)))

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("semantic helpers ran before whole source-reference budget")

    monkeypatch.setattr(c, "_envelope_payload_v2", forbidden)
    monkeypatch.setattr(e, "_assessment_payload_v2", forbidden)
    reject(partial(encode_entry_intent_v2, root), "RISK_V2_RESOURCE_LIMIT")


@pytest.mark.parametrize(
    "path,value",
    [
        (("margin",), {}),
        (("liquidation",), {}),
        (("leverage_readback",), {}),
        (("blockers",), ["RISK_V2_PLANNING_ONLY"]),
        (("required_reservation_quote",), "51.00000000"),
        (("liquidation_buffer_result",), "MODEL_NO_POSITIVE_TRIGGER"),
        (("margin", "reference", "input_sha256"), "b" * 64),
        (("margin", "reference", "model_revision"), "OTHER"),
        (("margin", "exact_calculation_sha256"), "b" * 64),
        (("margin", "covered_tier_ids"), ["TIER_A", "TIER_Z"]),
        (("margin", "reserve_quantum_usdt"), "0.00000001"),
        (("liquidation", "reference", "input_sha256"), "b" * 64),
        (("liquidation", "covered_tier_ids"), ["OTHER"]),
        (("liquidation", "outcome", "candidate_tier_id"), "OTHER"),
        (("liquidation", "outcome", "consumed_tier_ids"), ["TIER_Z", "TIER_Z"]),
        (("liquidation", "outcome", "exact_price", "numerator"), "-0"),
        (("liquidation", "outcome", "exact_price", "numerator"), "2E+2"),
        (("liquidation", "outcome", "exact_price", "denominator"), "6"),
        (("leverage_readback", "after_control_at"), "2026-11-03T00:00:00.000001+00:00"),
        (("assessed_at",), "2026-11-03T00:00:01.000000+00:00"),
        (("preparation", "minimum_amount", "evaluated_quote_amount"), "4"),
    ],
)
def test_full_assessment_semantic_mutations(path: tuple[str, ...], value: Any) -> None:
    tree = json.loads(GOLDEN_WIRES["POSITIVE_ROOT"])
    node = tree["fields"]["assessment"]
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    with pytest.raises(c.RiskV2Rejected):
        decode_entry_intent_v2(canonical_frame(tree))


@pytest.mark.parametrize(
    "raw", ["+60", "60_0", " 60", "60 ", "06", "6e+1", "6E+01", "NaN", "Infinity"]
)
def test_noncanonical_decimal_leaf(raw: str) -> None:
    tree = json.loads(GOLDEN_WIRES["PLAN"])
    tree["fields"]["assessment"]["preparation"]["capacity_quote"] = raw
    reject(lambda: decode_entry_intent_v2(canonical_frame(tree)))


def test_complete_plan_and_no_root_contextual_rules() -> None:
    tree = json.loads(GOLDEN_WIRES["POSITIVE_ROOT"])
    tree["fields"]["assessment"].update(stage="PLAN", blockers=["RISK_V2_PLANNING_ONLY"])
    restored = decode_entry_intent_v2(canonical_frame(tree))
    assert restored.assessment.stage == "PLAN"
    assert restored.assessment.blockers == ("RISK_V2_PLANNING_ONLY",)
    for path, value in (
        (("side",), "SHORT"),
        (("margin_mode",), "CROSS"),
        (("outcome", "model_input_sha256"), "b" * 64),
        (("outcome", "kind"), "FUTURE"),
    ):
        tree = json.loads(GOLDEN_WIRES["NO_POSITIVE_ROOT"])
        node = tree["fields"]["assessment"]["liquidation"]
        for name in path[:-1]:
            node = node[name]
        node[path[-1]] = value
        with pytest.raises(c.RiskV2Rejected):
            decode_entry_intent_v2(canonical_frame(tree))


def test_source_tuple_limits_optional_failures_and_time_no_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    root = actual_intent()

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("current clock/freshness queried")

    monkeypatch.setattr(time, "time", forbidden)
    monkeypatch.setattr(c, "_fresh_v2", forbidden)
    restored = decode_entry_intent_v2(
        encode_entry_intent_v2(replace(root, source_record_sha256s=(H,)))
    )
    assert restored.source_record_sha256s == (H,)
    for name in ("margin", "liquidation", "leverage_readback"):
        assessment = forged(root.assessment, name, object())
        reject(partial(replace, root, assessment=assessment))
    for path in (
        ("source_record_sha256s",),
        ("assessment", "preparation", "valuation", "mark_observation_ids"),
    ):
        node: Any = root
        for part in path:
            node = object.__getattribute__(node, part)
        reject(partial(encode_entry_intent_v2, replace_path(root, path, list(node))))


def test_decode_oversized_generation_and_arrays_before_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from multi_exchange_trader.execution import risk_v2_codec as codec

    raw = GOLDEN_WIRES["PLAN"].replace('"generation":0', '"generation":' + "1" + "0" * 5000)
    reject(lambda: decode_entry_intent_v2(raw), "RISK_V2_RESOURCE_LIMIT")
    tree = json.loads(GOLDEN_WIRES["PLAN"])
    tree["fields"]["assessment"]["preparation"]["minimum_amount"]["input_observation_ids"] = [
        "X"
    ] * 1025
    raw = canonical_frame(tree)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("constructor reached before array preflight")

    monkeypatch.setattr(codec, "_reconstruct", forbidden)
    reject(lambda: decode_entry_intent_v2(raw), "RISK_V2_RESOURCE_LIMIT")


def test_scanner_treats_escaped_strings_as_inert() -> None:
    from multi_exchange_trader.execution import risk_v2_codec as codec

    raw = json.dumps({"a": '[{"' * 1000 + "\\" * 1000})
    codec._scan(raw)


# Complete literal trees/wires below are frozen by the separate stdlib-only
# construction receipt. No production projection or digest helper supplied them.

GOLDEN_HASHES = {
    "envelope": "22e29f771c4819879dc9848542e87f25bbeb53939ccd63ed71476e56d947a47d",
    "sizing": "2eade0f00239e0030e37ed5247632922c2d349f769fa8e0916e358d06ec346e9",
    "valuation": "6edc481f73fec4f9d208ae117c22eabb57c3a598f9e794e00784e6f750725829",
    "tier_bound": "a99892a405545e09a39c1be93a0c658a07e8874a072a080375d443c401645df9",
    "minimum": "12ea10437a3189d5b4c955b0ba362291d41c0439c5ea167a6268cebaaf4d8887",
    "binding": "1b291413c30788c56a4151d973b3ad696072b278f407ef2a9c47b6c31b5feb68",
    "margin": "fe54c6d6ea3692e8b7cac6cb9b59e19f84abf13864d119577ea3c294601287c0",
}

GOLDEN_LENGTHS = {"PLAN": 8658, "POSITIVE_ROOT": 11381, "NO_POSITIVE_ROOT": 11524}

GOLDEN_SHA256S = {
    "PLAN": "67e6f564302780b2fdf6b197effd721f688fef6e65395c941a6782ab3630cf9b",
    "POSITIVE_ROOT": "e45195df8a31954b052586bebf7a4762a934bb98e930031080066ee109e62f93",
    "NO_POSITIVE_ROOT": "c48fcac50bdcf48f64b470bf5499e860226b3a358c309529a24df97525928a8d",
}

GOLDEN_CHECKSUMS = {
    "PLAN": "5441cc36b1777cf7f83c72192d25c8c063b379d0edf6e3cfce1b38f09b157e5d",
    "POSITIVE_ROOT": "2ea1cfbacbda405273ee3add128f633ff6b5e3085fe3d18f634cfea25816978c",
    "NO_POSITIVE_ROOT": "e7c29dba1ea0076a3ddd52424de2232fa414a035cd2a602fa2277e4a578e4197",
}

GOLDEN_WIRES = {
    "PLAN": (
        '{"content_sha256":"5441cc36b1777cf7f83c72192d25c8c063b379d0edf6e3cfce1b38f09b157e5d","fields'
        '":{"assessment":{"assessed_at":"2026-11-03T00:00:00.000000+00:00","blockers":["RISK_V2_MARGI'
        'N_UNVERIFIED","RISK_V2_LIQUIDATION_UNVERIFIED","RISK_V2_LEVERAGE_READBACK_UNVERIFIED"],"form'
        'ula_version":"LINEAR_FIXED_LIMIT_MARK_ADMISSION_V2","leverage_readback":null,"liquidation":n'
        'ull,"liquidation_buffer_result":null,"margin":null,"preparation":{"binding":{"account_covera'
        'ge_revision":"COVERAGE_V1","account_observation_id":"ACCOUNT","account_snapshot_sha256":"aaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","cost_allowance_sha256":"aaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","cost_calculation_id":"COST","'
        'envelope_sha256":"22e29f771c4819879dc9848542e87f25bbeb53939ccd63ed71476e56d947a47d","instrum'
        'ent_pin":{"catalog_revision":"CATALOG_V1","key":{"product":"LINEAR","symbol":"XAAUSDT","venu'
        'e":"BYBIT"},"native_instrument_id":"XAAUSDT-LINEAR.BYBIT","parser_revision":"BYBIT_INSTRUMEN'
        'T_PARSER_V1","raw_row_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaa","received_at":"2026-11-03T00:00:00.000000+00:00","response_time":"2026-11-03T00:00:00.0'
        '00000+00:00","source_environment":"FIXTURE","source_id":"SOURCE","source_rule_revision":"BYB'
        'IT_LINEAR_USDT_PERPETUAL_V1","specification_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaa"},"mark_set_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaa","minimum_assessment_sha256":"12ea10437a3189d5b4c955b0ba362291d41c0439'
        'c5ea167a6268cebaaf4d8887","policy_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaa","requirements_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaa","reservation_owner_generation":"OWNER_1","reservation_snapshot_id":"RESERVATIO'
        'NS","reservation_snapshot_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaa","risk_domain_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaa","scope":{"instrument":{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"product_'
        'scope":{"account":{"account_id":"\\ud55c\\uae00","environment":"DEMO"},"product":"LINEAR"},"st'
        'rategy":{"revision":"STRATEGY_V1","strategy_id":"STRATEGY"}},"sizing_sha256":"2eade0f00239e0'
        '030e37ed5247632922c2d349f769fa8e0916e358d06ec346e9","source_mapping_revision":"MAPPING_V1","'
        'tier_bound_sha256":"a99892a405545e09a39c1be93a0c658a07e8874a072a080375d443c401645df9","tier_'
        'observation_id":"TIERS","tier_snapshot_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaa","valuation_sha256":"6edc481f73fec4f9d208ae117c22eabb57c3a598f9e794e007'
        '84e6f750725829"},"capacity_quote":"60","display_ratio_rule":"RISK_V2_EXACT_FINITE_ELSE_CEILI'
        'NG80_V1","estimated_loss_fraction_display":"0.012","minimum_amount":{"envelope_sha256":"22e2'
        '9f771c4819879dc9848542e87f25bbeb53939ccd63ed71476e56d947a47d","evaluated_quote_amount":"100.'
        '0000","evaluation_price":"100.00","input_observation_ids":["MARK"],"instrument_revision":"aa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","minimum_quote_amount":"5","'
        'reference":{"calculation_id":"MINIMUM","computed_at":"2026-11-03T00:00:00.000000+00:00","inp'
        'ut_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","model_revisio'
        'n":"MINIMUM_V1","target_environment":"FIXTURE"},"rule_revision":"MINIMUM_V1","scope":{"instr'
        'ument":{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"product_scope":{"account":{"'
        'account_id":"\\ud55c\\uae00","environment":"DEMO"},"product":"LINEAR"},"strategy":{"revision":'
        '"STRATEGY_V1","strategy_id":"STRATEGY"}},"valid_until":"2026-11-03T00:00:01.000000+00:00"},"'
        'sizing":{"assessed_at":"2026-11-03T00:00:00.000000+00:00","cost_allowance_quote":"2.0000","c'
        'ost_allowance_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","co'
        'st_reference":{"calculation_id":"COST","computed_at":"2026-11-03T00:00:00.000000+00:00","inp'
        'ut_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","model_revisio'
        'n":"COST_V1","target_environment":"FIXTURE"},"envelope":{"base_quantity":"1.00","instrument_'
        'pin":{"catalog_revision":"CATALOG_V1","key":{"product":"LINEAR","symbol":"XAAUSDT","venue":"'
        'BYBIT"},"native_instrument_id":"XAAUSDT-LINEAR.BYBIT","parser_revision":"BYBIT_INSTRUMENT_PA'
        'RSER_V1","raw_row_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"'
        ',"received_at":"2026-11-03T00:00:00.000000+00:00","response_time":"2026-11-03T00:00:00.00000'
        '0+00:00","source_environment":"FIXTURE","source_id":"SOURCE","source_rule_revision":"BYBIT_L'
        'INEAR_USDT_PERPETUAL_V1","specification_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaa"},"limit_price":"100.00","order_type":"LIMIT","origin_quote_id":"QUO'
        'TE","policy_revision":"POLICY_V1","policy_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaa","reduce_only":false,"role":"ENTRY","schema_version":2,"scope":{"ins'
        'trument":{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"product_scope":{"account":'
        '{"account_id":"\\ud55c\\uae00","environment":"DEMO"},"product":"LINEAR"},"strategy":{"revision'
        '":"STRATEGY_V1","strategy_id":"STRATEGY"}},"side":"BUY","time_in_force":"IOC","valid_until":'
        '"2026-11-03T00:00:01.000000+00:00"},"estimated_loss_quote":"12.0000","formula_version":"LINE'
        'AR_FIXED_LIMIT_LOSS_SIZE_V2","funding_allowance_quote":"0.50","policy_sha256":"aaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","scope":{"instrument":{"product":"LINEA'
        'R","symbol":"XAAUSDT","venue":"BYBIT"},"product_scope":{"account":{"account_id":"\\ud55c\\uae0'
        '0","environment":"DEMO"},"product":"LINEAR"},"strategy":{"revision":"STRATEGY_V1","strategy_'
        'id":"STRATEGY"}},"stop_distance_quote_per_base":"10.00","stop_price":"90.00","unit_cost_quot'
        'e":"2.00","unit_loss_quote":"12.00"},"tier_bound":{"bound_rule":"LINEAR_MARK_GROSS_UPPER_BOU'
        'ND_V1","continuous_leverage_lower":"1","continuous_leverage_upper":"10","covered_tier_ids":['
        '"TIER_Z","TIER_A"],"instrument":{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"lev'
        'erage_grid_rule":"LOCAL_ZERO_ORIGIN_LATTICE_V1","leverage_step":"0.1","mark_observation_id":'
        '"MARK","permitted_leverage_max":"10.0","permitted_leverage_min":"1.0","symbol_gross_upper_qu'
        'ote":"100","tier_observation_id":"TIERS"},"valuation":{"account_gross_quote":"-0.00","accoun'
        't_observation_id":"ACCOUNT","assessed_at":"2026-11-03T00:00:00.000000+00:00","combined_gross'
        '_quote":"100.00","mark_observation_ids":["MARK"],"proposed_mark_quote":"100.00","reservation'
        '_snapshot_id":"RESERVATIONS","reserved_gross_quote":"0","scope":{"instrument":{"product":"LI'
        'NEAR","symbol":"XAAUSDT","venue":"BYBIT"},"product_scope":{"account":{"account_id":"\\ud55c\\u'
        'ae00","environment":"DEMO"},"product":"LINEAR"},"strategy":{"revision":"STRATEGY_V1","strate'
        'gy_id":"STRATEGY"}},"valuation_rule":"LINEAR_MARK_GROSS_AT_ADMISSION_V1"}},"required_reserva'
        'tion_quote":null,"stage":"PLAN"},"command_key":"COMMAND","envelope":{"base_quantity":"1.00",'
        '"instrument_pin":{"catalog_revision":"CATALOG_V1","key":{"product":"LINEAR","symbol":"XAAUSD'
        'T","venue":"BYBIT"},"native_instrument_id":"XAAUSDT-LINEAR.BYBIT","parser_revision":"BYBIT_I'
        'NSTRUMENT_PARSER_V1","raw_row_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaa","received_at":"2026-11-03T00:00:00.000000+00:00","response_time":"2026-11-03T00'
        ':00:00.000000+00:00","source_environment":"FIXTURE","source_id":"SOURCE","source_rule_revisi'
        'on":"BYBIT_LINEAR_USDT_PERPETUAL_V1","specification_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"limit_price":"100.00","order_type":"LIMIT","origin_qu'
        'ote_id":"QUOTE","policy_revision":"POLICY_V1","policy_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","reduce_only":false,"role":"ENTRY","schema_version":2,"'
        'scope":{"instrument":{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"product_scope"'
        ':{"account":{"account_id":"\\ud55c\\uae00","environment":"DEMO"},"product":"LINEAR"},"strategy'
        '":{"revision":"STRATEGY_V1","strategy_id":"STRATEGY"}},"side":"BUY","time_in_force":"IOC","v'
        'alid_until":"2026-11-03T00:00:01.000000+00:00"},"plan_id":"PLAN","position_id":"POSITION","p'
        'rotection_policy":{"generation":0,"revision":"POLICY_V1","stop_price":"90.00","take_profit_p'
        'rice":"120.000","trigger_source":"MARK_PRICE"},"schema_version":2,"source_record_sha256s":["'
        '0000000000000000000000000000000000000000000000000000000000000000","aaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],"strategy":{"revision":"STRATEGY_V1","strategy_id"'
        ':"STRATEGY"}},"record_type":"DurableEntryIntentV2","schema":"RISK_V2_DURABLE_INTENT","wire_v'
        'ersion":2}'
    ),
    "POSITIVE_ROOT": (
        '{"content_sha256":"2ea1cfbacbda405273ee3add128f633ff6b5e3085fe3d18f634cfea25816978c","fields'
        '":{"assessment":{"assessed_at":"2026-11-03T00:00:00.000000+00:00","blockers":[],"formula_ver'
        'sion":"LINEAR_FIXED_LIMIT_MARK_ADMISSION_V2","leverage_readback":{"account_observation_id":"'
        'ACCOUNT","after_control_at":"2026-11-03T00:00:00.000000+00:00","applied_leverage":"2.0","bin'
        'ding_sha256":"1b291413c30788c56a4151d973b3ad696072b278f407ef2a9c47b6c31b5feb68","control_com'
        'mand_key":"CONTROL","margin_mode":"ISOLATED","position_mode":"ONE_WAY","reference":{"observa'
        'tion_id":"READBACK","payload_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaa","received_at":"2026-11-03T00:00:00.000000+00:00","server_observed_at":"2026-11-0'
        '3T00:00:00.000000+00:00","source_environment":"FIXTURE","source_id":"SOURCE","source_rule_re'
        'vision":"SOURCE_V1"},"side":"LONG"},"liquidation":{"binding_sha256":"1b291413c30788c56a4151d'
        '973b3ad696072b278f407ef2a9c47b6c31b5feb68","covered_tier_ids":["TIER_Z","TIER_A"],"entry_lim'
        'it_price":"100.00","exact_calculation_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaa","leverage":"2.0","margin_assessment_sha256":"fe54c6d6ea3692e8b7cac6cb9b'
        '59e19f84abf13864d119577ea3c294601287c0","margin_mode":"ISOLATED","model_input_sha256":"aaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","model_revision":"LP_V1","outco'
        'me":{"candidate_tier_id":"TIER_Z","consumed_tier_ids":["TIER_Z","TIER_A"],"domain_upper_quot'
        'e":"1000","exact_price":{"denominator":"3","numerator":"200"},"full_table_sha256":"aaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","kind":"POSITIVE_ROOT","raw_manifes'
        't_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","rule_revision"'
        ':"LP_V1"},"position_mode":"ONE_WAY","quantity":"1.00","reference":{"calculation_id":"LP","co'
        'mputed_at":"2026-11-03T00:00:00.000000+00:00","input_sha256":"fe54c6d6ea3692e8b7cac6cb9b59e1'
        '9f84abf13864d119577ea3c294601287c0","model_revision":"LP_V1","target_environment":"FIXTURE"}'
        ',"side":"LONG","valid_until":"2026-11-03T00:00:01.000000+00:00"},"liquidation_buffer_result"'
        ':"GAP_SATISFIED","margin":{"binding_sha256":"1b291413c30788c56a4151d973b3ad696072b278f407ef2'
        'a9c47b6c31b5feb68","covered_tier_ids":["TIER_Z","TIER_A"],"exact_calculation_sha256":"aaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","initial_margin_quote":"50.00000'
        '000","leverage":"2.0","leverage_grid_rule":"LOCAL_ZERO_ORIGIN_LATTICE_V1","model_input_sha25'
        '6":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","model_revision":"MARG'
        'IN_V1","quantity":"1.00","reference":{"calculation_id":"MARGIN","computed_at":"2026-11-03T00'
        ':00:00.000000+00:00","input_sha256":"1b291413c30788c56a4151d973b3ad696072b278f407ef2a9c47b6c'
        '31b5feb68","model_revision":"MARGIN_V1","target_environment":"FIXTURE"},"reserve_basis":"POI'
        'NT_ESTIMATE_TWO_IM_ENVELOPE_V1","reserve_quantum_usdt":"1E-8","reserve_rounding_revision":"A'
        'PP_RESERVE_CEILING_QUANTUM_V1","selection_rule":"SMALLEST_FEASIBLE_PERMITTED_GRID_V1","valid'
        '_until":"2026-11-03T00:00:01.000000+00:00","venue_required_total_reserve_quote":"52.00000000'
        '"},"preparation":{"binding":{"account_coverage_revision":"COVERAGE_V1","account_observation_'
        'id":"ACCOUNT","account_snapshot_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaa","cost_allowance_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaa","cost_calculation_id":"COST","envelope_sha256":"22e29f771c4819879dc9848542e87f'
        '25bbeb53939ccd63ed71476e56d947a47d","instrument_pin":{"catalog_revision":"CATALOG_V1","key":'
        '{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"native_instrument_id":"XAAUSDT-LINE'
        'AR.BYBIT","parser_revision":"BYBIT_INSTRUMENT_PARSER_V1","raw_row_sha256":"aaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","received_at":"2026-11-03T00:00:00.000000+0'
        '0:00","response_time":"2026-11-03T00:00:00.000000+00:00","source_environment":"FIXTURE","sou'
        'rce_id":"SOURCE","source_rule_revision":"BYBIT_LINEAR_USDT_PERPETUAL_V1","specification_revi'
        'sion":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"mark_set_sha256":'
        '"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","minimum_assessment_sha25'
        '6":"12ea10437a3189d5b4c955b0ba362291d41c0439c5ea167a6268cebaaf4d8887","policy_sha256":"aaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","requirements_sha256":"aaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","reservation_owner_generation":"OW'
        'NER_1","reservation_snapshot_id":"RESERVATIONS","reservation_snapshot_sha256":"aaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","risk_domain_sha256":"aaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","scope":{"instrument":{"product":"LINEAR","'
        'symbol":"XAAUSDT","venue":"BYBIT"},"product_scope":{"account":{"account_id":"\\ud55c\\uae00","'
        'environment":"DEMO"},"product":"LINEAR"},"strategy":{"revision":"STRATEGY_V1","strategy_id":'
        '"STRATEGY"}},"sizing_sha256":"2eade0f00239e0030e37ed5247632922c2d349f769fa8e0916e358d06ec346'
        'e9","source_mapping_revision":"MAPPING_V1","tier_bound_sha256":"a99892a405545e09a39c1be93a0c'
        '658a07e8874a072a080375d443c401645df9","tier_observation_id":"TIERS","tier_snapshot_sha256":"'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","valuation_sha256":"6edc48'
        '1f73fec4f9d208ae117c22eabb57c3a598f9e794e00784e6f750725829"},"capacity_quote":"60","display_'
        'ratio_rule":"RISK_V2_EXACT_FINITE_ELSE_CEILING80_V1","estimated_loss_fraction_display":"0.01'
        '2","minimum_amount":{"envelope_sha256":"22e29f771c4819879dc9848542e87f25bbeb53939ccd63ed7147'
        '6e56d947a47d","evaluated_quote_amount":"100.0000","evaluation_price":"100.00","input_observa'
        'tion_ids":["MARK"],"instrument_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaa","minimum_quote_amount":"5","reference":{"calculation_id":"MINIMUM","computed'
        '_at":"2026-11-03T00:00:00.000000+00:00","input_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaa","model_revision":"MINIMUM_V1","target_environment":"FIXTURE"},'
        '"rule_revision":"MINIMUM_V1","scope":{"instrument":{"product":"LINEAR","symbol":"XAAUSDT","v'
        'enue":"BYBIT"},"product_scope":{"account":{"account_id":"\\ud55c\\uae00","environment":"DEMO"}'
        ',"product":"LINEAR"},"strategy":{"revision":"STRATEGY_V1","strategy_id":"STRATEGY"}},"valid_'
        'until":"2026-11-03T00:00:01.000000+00:00"},"sizing":{"assessed_at":"2026-11-03T00:00:00.0000'
        '00+00:00","cost_allowance_quote":"2.0000","cost_allowance_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","cost_reference":{"calculation_id":"COST","computed'
        '_at":"2026-11-03T00:00:00.000000+00:00","input_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaa","model_revision":"COST_V1","target_environment":"FIXTURE"},"en'
        'velope":{"base_quantity":"1.00","instrument_pin":{"catalog_revision":"CATALOG_V1","key":{"pr'
        'oduct":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"native_instrument_id":"XAAUSDT-LINEAR.B'
        'YBIT","parser_revision":"BYBIT_INSTRUMENT_PARSER_V1","raw_row_sha256":"aaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","received_at":"2026-11-03T00:00:00.000000+00:00'
        '","response_time":"2026-11-03T00:00:00.000000+00:00","source_environment":"FIXTURE","source_'
        'id":"SOURCE","source_rule_revision":"BYBIT_LINEAR_USDT_PERPETUAL_V1","specification_revision'
        '":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"limit_price":"100.00"'
        ',"order_type":"LIMIT","origin_quote_id":"QUOTE","policy_revision":"POLICY_V1","policy_sha256'
        '":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","reduce_only":false,"ro'
        'le":"ENTRY","schema_version":2,"scope":{"instrument":{"product":"LINEAR","symbol":"XAAUSDT",'
        '"venue":"BYBIT"},"product_scope":{"account":{"account_id":"\\ud55c\\uae00","environment":"DEMO'
        '"},"product":"LINEAR"},"strategy":{"revision":"STRATEGY_V1","strategy_id":"STRATEGY"}},"side'
        '":"BUY","time_in_force":"IOC","valid_until":"2026-11-03T00:00:01.000000+00:00"},"estimated_l'
        'oss_quote":"12.0000","formula_version":"LINEAR_FIXED_LIMIT_LOSS_SIZE_V2","funding_allowance_'
        'quote":"0.50","policy_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaa","scope":{"instrument":{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"product_'
        'scope":{"account":{"account_id":"\\ud55c\\uae00","environment":"DEMO"},"product":"LINEAR"},"st'
        'rategy":{"revision":"STRATEGY_V1","strategy_id":"STRATEGY"}},"stop_distance_quote_per_base":'
        '"10.00","stop_price":"90.00","unit_cost_quote":"2.00","unit_loss_quote":"12.00"},"tier_bound'
        '":{"bound_rule":"LINEAR_MARK_GROSS_UPPER_BOUND_V1","continuous_leverage_lower":"1","continuo'
        'us_leverage_upper":"10","covered_tier_ids":["TIER_Z","TIER_A"],"instrument":{"product":"LINE'
        'AR","symbol":"XAAUSDT","venue":"BYBIT"},"leverage_grid_rule":"LOCAL_ZERO_ORIGIN_LATTICE_V1",'
        '"leverage_step":"0.1","mark_observation_id":"MARK","permitted_leverage_max":"10.0","permitte'
        'd_leverage_min":"1.0","symbol_gross_upper_quote":"100","tier_observation_id":"TIERS"},"valua'
        'tion":{"account_gross_quote":"-0.00","account_observation_id":"ACCOUNT","assessed_at":"2026-'
        '11-03T00:00:00.000000+00:00","combined_gross_quote":"100.00","mark_observation_ids":["MARK"]'
        ',"proposed_mark_quote":"100.00","reservation_snapshot_id":"RESERVATIONS","reserved_gross_quo'
        'te":"0","scope":{"instrument":{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"produ'
        'ct_scope":{"account":{"account_id":"\\ud55c\\uae00","environment":"DEMO"},"product":"LINEAR"},'
        '"strategy":{"revision":"STRATEGY_V1","strategy_id":"STRATEGY"}},"valuation_rule":"LINEAR_MAR'
        'K_GROSS_AT_ADMISSION_V1"}},"required_reservation_quote":"52.00000000","stage":"SUBMIT"},"com'
        'mand_key":"COMMAND","envelope":{"base_quantity":"1.00","instrument_pin":{"catalog_revision":'
        '"CATALOG_V1","key":{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"native_instrumen'
        't_id":"XAAUSDT-LINEAR.BYBIT","parser_revision":"BYBIT_INSTRUMENT_PARSER_V1","raw_row_sha256"'
        ':"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","received_at":"2026-11-0'
        '3T00:00:00.000000+00:00","response_time":"2026-11-03T00:00:00.000000+00:00","source_environm'
        'ent":"FIXTURE","source_id":"SOURCE","source_rule_revision":"BYBIT_LINEAR_USDT_PERPETUAL_V1",'
        '"specification_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
        ',"limit_price":"100.00","order_type":"LIMIT","origin_quote_id":"QUOTE","policy_revision":"PO'
        'LICY_V1","policy_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"reduce_only":false,"role":"ENTRY","schema_version":2,"scope":{"instrument":{"product":"LINE'
        'AR","symbol":"XAAUSDT","venue":"BYBIT"},"product_scope":{"account":{"account_id":"\\ud55c\\uae'
        '00","environment":"DEMO"},"product":"LINEAR"},"strategy":{"revision":"STRATEGY_V1","strategy'
        '_id":"STRATEGY"}},"side":"BUY","time_in_force":"IOC","valid_until":"2026-11-03T00:00:01.0000'
        '00+00:00"},"plan_id":"PLAN","position_id":"POSITION","protection_policy":{"generation":0,"re'
        'vision":"POLICY_V1","stop_price":"90.00","take_profit_price":"120.000","trigger_source":"MAR'
        'K_PRICE"},"schema_version":2,"source_record_sha256s":["0000000000000000000000000000000000000'
        '000000000000000000000000000","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aa"],"strategy":{"revision":"STRATEGY_V1","strategy_id":"STRATEGY"}},"record_type":"DurableE'
        'ntryIntentV2","schema":"RISK_V2_DURABLE_INTENT","wire_version":2}'
    ),
    "NO_POSITIVE_ROOT": (
        '{"content_sha256":"e7c29dba1ea0076a3ddd52424de2232fa414a035cd2a602fa2277e4a578e4197","fields'
        '":{"assessment":{"assessed_at":"2026-11-03T00:00:00.000000+00:00","blockers":[],"formula_ver'
        'sion":"LINEAR_FIXED_LIMIT_MARK_ADMISSION_V2","leverage_readback":{"account_observation_id":"'
        'ACCOUNT","after_control_at":"2026-11-03T00:00:00.000000+00:00","applied_leverage":"2.0","bin'
        'ding_sha256":"1b291413c30788c56a4151d973b3ad696072b278f407ef2a9c47b6c31b5feb68","control_com'
        'mand_key":"CONTROL","margin_mode":"ISOLATED","position_mode":"ONE_WAY","reference":{"observa'
        'tion_id":"READBACK","payload_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaa","received_at":"2026-11-03T00:00:00.000000+00:00","server_observed_at":"2026-11-0'
        '3T00:00:00.000000+00:00","source_environment":"FIXTURE","source_id":"SOURCE","source_rule_re'
        'vision":"SOURCE_V1"},"side":"LONG"},"liquidation":{"binding_sha256":"1b291413c30788c56a4151d'
        '973b3ad696072b278f407ef2a9c47b6c31b5feb68","covered_tier_ids":["TIER_Z","TIER_A"],"entry_lim'
        'it_price":"100.00","exact_calculation_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaa","leverage":"2.0","margin_assessment_sha256":"fe54c6d6ea3692e8b7cac6cb9b'
        '59e19f84abf13864d119577ea3c294601287c0","margin_mode":"ISOLATED","model_input_sha256":"aaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","model_revision":"LP_V1","outco'
        'me":{"consumed_tier_ids":["TIER_Z","TIER_A"],"domain_upper_quote":"1000","full_table_sha256"'
        ':"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","kind":"NO_POSITIVE_ROOT'
        '","model_input_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","p'
        'roof_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","raw_manifes'
        't_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","reason":"NON_P'
        'OSITIVE_LONG_INTERCEPT","rule_revision":"LP_V1"},"position_mode":"ONE_WAY","quantity":"1.00"'
        ',"reference":{"calculation_id":"LP","computed_at":"2026-11-03T00:00:00.000000+00:00","input_'
        'sha256":"fe54c6d6ea3692e8b7cac6cb9b59e19f84abf13864d119577ea3c294601287c0","model_revision":'
        '"LP_V1","target_environment":"FIXTURE"},"side":"LONG","valid_until":"2026-11-03T00:00:01.000'
        '000+00:00"},"liquidation_buffer_result":"MODEL_NO_POSITIVE_TRIGGER","margin":{"binding_sha25'
        '6":"1b291413c30788c56a4151d973b3ad696072b278f407ef2a9c47b6c31b5feb68","covered_tier_ids":["T'
        'IER_Z","TIER_A"],"exact_calculation_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaa","initial_margin_quote":"50.00000000","leverage":"2.0","leverage_grid_rule'
        '":"LOCAL_ZERO_ORIGIN_LATTICE_V1","model_input_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaa","model_revision":"MARGIN_V1","quantity":"1.00","reference":{"ca'
        'lculation_id":"MARGIN","computed_at":"2026-11-03T00:00:00.000000+00:00","input_sha256":"1b29'
        '1413c30788c56a4151d973b3ad696072b278f407ef2a9c47b6c31b5feb68","model_revision":"MARGIN_V1","'
        'target_environment":"FIXTURE"},"reserve_basis":"POINT_ESTIMATE_TWO_IM_ENVELOPE_V1","reserve_'
        'quantum_usdt":"1E-8","reserve_rounding_revision":"APP_RESERVE_CEILING_QUANTUM_V1","selection'
        '_rule":"SMALLEST_FEASIBLE_PERMITTED_GRID_V1","valid_until":"2026-11-03T00:00:01.000000+00:00'
        '","venue_required_total_reserve_quote":"52.00000000"},"preparation":{"binding":{"account_cov'
        'erage_revision":"COVERAGE_V1","account_observation_id":"ACCOUNT","account_snapshot_sha256":"'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","cost_allowance_sha256":"a'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","cost_calculation_id":"COST'
        '","envelope_sha256":"22e29f771c4819879dc9848542e87f25bbeb53939ccd63ed71476e56d947a47d","inst'
        'rument_pin":{"catalog_revision":"CATALOG_V1","key":{"product":"LINEAR","symbol":"XAAUSDT","v'
        'enue":"BYBIT"},"native_instrument_id":"XAAUSDT-LINEAR.BYBIT","parser_revision":"BYBIT_INSTRU'
        'MENT_PARSER_V1","raw_row_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaa","received_at":"2026-11-03T00:00:00.000000+00:00","response_time":"2026-11-03T00:00:0'
        '0.000000+00:00","source_environment":"FIXTURE","source_id":"SOURCE","source_rule_revision":"'
        'BYBIT_LINEAR_USDT_PERPETUAL_V1","specification_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"mark_set_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaa","minimum_assessment_sha256":"12ea10437a3189d5b4c955b0ba362291d41c0'
        '439c5ea167a6268cebaaf4d8887","policy_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaa","requirements_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaa","reservation_owner_generation":"OWNER_1","reservation_snapshot_id":"RESERVA'
        'TIONS","reservation_snapshot_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaa","risk_domain_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaa","scope":{"instrument":{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"produ'
        'ct_scope":{"account":{"account_id":"\\ud55c\\uae00","environment":"DEMO"},"product":"LINEAR"},'
        '"strategy":{"revision":"STRATEGY_V1","strategy_id":"STRATEGY"}},"sizing_sha256":"2eade0f0023'
        '9e0030e37ed5247632922c2d349f769fa8e0916e358d06ec346e9","source_mapping_revision":"MAPPING_V1'
        '","tier_bound_sha256":"a99892a405545e09a39c1be93a0c658a07e8874a072a080375d443c401645df9","ti'
        'er_observation_id":"TIERS","tier_snapshot_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaa","valuation_sha256":"6edc481f73fec4f9d208ae117c22eabb57c3a598f9e794e'
        '00784e6f750725829"},"capacity_quote":"60","display_ratio_rule":"RISK_V2_EXACT_FINITE_ELSE_CE'
        'ILING80_V1","estimated_loss_fraction_display":"0.012","minimum_amount":{"envelope_sha256":"2'
        '2e29f771c4819879dc9848542e87f25bbeb53939ccd63ed71476e56d947a47d","evaluated_quote_amount":"1'
        '00.0000","evaluation_price":"100.00","input_observation_ids":["MARK"],"instrument_revision":'
        '"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","minimum_quote_amount":"5'
        '","reference":{"calculation_id":"MINIMUM","computed_at":"2026-11-03T00:00:00.000000+00:00","'
        'input_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","model_revi'
        'sion":"MINIMUM_V1","target_environment":"FIXTURE"},"rule_revision":"MINIMUM_V1","scope":{"in'
        'strument":{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"product_scope":{"account"'
        ':{"account_id":"\\ud55c\\uae00","environment":"DEMO"},"product":"LINEAR"},"strategy":{"revisio'
        'n":"STRATEGY_V1","strategy_id":"STRATEGY"}},"valid_until":"2026-11-03T00:00:01.000000+00:00"'
        '},"sizing":{"assessed_at":"2026-11-03T00:00:00.000000+00:00","cost_allowance_quote":"2.0000"'
        ',"cost_allowance_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"cost_reference":{"calculation_id":"COST","computed_at":"2026-11-03T00:00:00.000000+00:00","'
        'input_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","model_revi'
        'sion":"COST_V1","target_environment":"FIXTURE"},"envelope":{"base_quantity":"1.00","instrume'
        'nt_pin":{"catalog_revision":"CATALOG_V1","key":{"product":"LINEAR","symbol":"XAAUSDT","venue'
        '":"BYBIT"},"native_instrument_id":"XAAUSDT-LINEAR.BYBIT","parser_revision":"BYBIT_INSTRUMENT'
        '_PARSER_V1","raw_row_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aa","received_at":"2026-11-03T00:00:00.000000+00:00","response_time":"2026-11-03T00:00:00.00'
        '0000+00:00","source_environment":"FIXTURE","source_id":"SOURCE","source_rule_revision":"BYBI'
        'T_LINEAR_USDT_PERPETUAL_V1","specification_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaa"},"limit_price":"100.00","order_type":"LIMIT","origin_quote_id":"'
        'QUOTE","policy_revision":"POLICY_V1","policy_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaa","reduce_only":false,"role":"ENTRY","schema_version":2,"scope":{"'
        'instrument":{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"product_scope":{"accoun'
        't":{"account_id":"\\ud55c\\uae00","environment":"DEMO"},"product":"LINEAR"},"strategy":{"revis'
        'ion":"STRATEGY_V1","strategy_id":"STRATEGY"}},"side":"BUY","time_in_force":"IOC","valid_unti'
        'l":"2026-11-03T00:00:01.000000+00:00"},"estimated_loss_quote":"12.0000","formula_version":"L'
        'INEAR_FIXED_LIMIT_LOSS_SIZE_V2","funding_allowance_quote":"0.50","policy_sha256":"aaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","scope":{"instrument":{"product":"LI'
        'NEAR","symbol":"XAAUSDT","venue":"BYBIT"},"product_scope":{"account":{"account_id":"\\ud55c\\u'
        'ae00","environment":"DEMO"},"product":"LINEAR"},"strategy":{"revision":"STRATEGY_V1","strate'
        'gy_id":"STRATEGY"}},"stop_distance_quote_per_base":"10.00","stop_price":"90.00","unit_cost_q'
        'uote":"2.00","unit_loss_quote":"12.00"},"tier_bound":{"bound_rule":"LINEAR_MARK_GROSS_UPPER_'
        'BOUND_V1","continuous_leverage_lower":"1","continuous_leverage_upper":"10","covered_tier_ids'
        '":["TIER_Z","TIER_A"],"instrument":{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"'
        'leverage_grid_rule":"LOCAL_ZERO_ORIGIN_LATTICE_V1","leverage_step":"0.1","mark_observation_i'
        'd":"MARK","permitted_leverage_max":"10.0","permitted_leverage_min":"1.0","symbol_gross_upper'
        '_quote":"100","tier_observation_id":"TIERS"},"valuation":{"account_gross_quote":"-0.00","acc'
        'ount_observation_id":"ACCOUNT","assessed_at":"2026-11-03T00:00:00.000000+00:00","combined_gr'
        'oss_quote":"100.00","mark_observation_ids":["MARK"],"proposed_mark_quote":"100.00","reservat'
        'ion_snapshot_id":"RESERVATIONS","reserved_gross_quote":"0","scope":{"instrument":{"product":'
        '"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"product_scope":{"account":{"account_id":"\\ud55'
        'c\\uae00","environment":"DEMO"},"product":"LINEAR"},"strategy":{"revision":"STRATEGY_V1","str'
        'ategy_id":"STRATEGY"}},"valuation_rule":"LINEAR_MARK_GROSS_AT_ADMISSION_V1"}},"required_rese'
        'rvation_quote":"52.00000000","stage":"SUBMIT"},"command_key":"COMMAND","envelope":{"base_qua'
        'ntity":"1.00","instrument_pin":{"catalog_revision":"CATALOG_V1","key":{"product":"LINEAR","s'
        'ymbol":"XAAUSDT","venue":"BYBIT"},"native_instrument_id":"XAAUSDT-LINEAR.BYBIT","parser_revi'
        'sion":"BYBIT_INSTRUMENT_PARSER_V1","raw_row_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaa","received_at":"2026-11-03T00:00:00.000000+00:00","response_time":'
        '"2026-11-03T00:00:00.000000+00:00","source_environment":"FIXTURE","source_id":"SOURCE","sour'
        'ce_rule_revision":"BYBIT_LINEAR_USDT_PERPETUAL_V1","specification_revision":"aaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"limit_price":"100.00","order_type":"LIM'
        'IT","origin_quote_id":"QUOTE","policy_revision":"POLICY_V1","policy_sha256":"aaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","reduce_only":false,"role":"ENTRY","schem'
        'a_version":2,"scope":{"instrument":{"product":"LINEAR","symbol":"XAAUSDT","venue":"BYBIT"},"'
        'product_scope":{"account":{"account_id":"\\ud55c\\uae00","environment":"DEMO"},"product":"LINE'
        'AR"},"strategy":{"revision":"STRATEGY_V1","strategy_id":"STRATEGY"}},"side":"BUY","time_in_f'
        'orce":"IOC","valid_until":"2026-11-03T00:00:01.000000+00:00"},"plan_id":"PLAN","position_id"'
        ':"POSITION","protection_policy":{"generation":0,"revision":"POLICY_V1","stop_price":"90.00",'
        '"take_profit_price":"120.000","trigger_source":"MARK_PRICE"},"schema_version":2,"source_reco'
        'rd_sha256s":["0000000000000000000000000000000000000000000000000000000000000000","aaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],"strategy":{"revision":"STRATEGY_V1"'
        ',"strategy_id":"STRATEGY"}},"record_type":"DurableEntryIntentV2","schema":"RISK_V2_DURABLE_I'
        'NTENT","wire_version":2}'
    ),
}


# Adopted independent-review corrections R1/R2. The entire original 96-test
# module and complete golden literals above remain an exact byte prefix.
@pytest.mark.parametrize(
    "path,value",
    [
        (("envelope", "scope", "product_scope", "account", "environment"), "PAPER"),
        (("protection_policy", "stop_price"), "0"),
        (("protection_policy", "stop_price"), "-90.00"),
        (("protection_policy", "take_profit_price"), "0"),
        (("protection_policy", "take_profit_price"), "-120"),
    ],
    ids=["account-environment", "zero-stop", "negative-stop", "zero-target", "negative-target"],
)
def test_correction_r1_exact_legacy_rejection_boundary(path: tuple[str, ...], value: str) -> None:
    tree = json.loads(GOLDEN_WIRES["PLAN"])
    node = tree["fields"]
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    # Direct stdlib JSON/hash independently recomputes a valid outer checksum.
    raw = canonical_frame(tree)
    root = actual_intent()
    replacement: Any = value if path[-1] == "environment" else Decimal(value)
    changed = replace_path(root, path, replacement)
    reject(partial(replace, root, **{path[0]: object.__getattribute__(changed, path[0])}))
    reject(partial(encode_entry_intent_v2, changed))
    reject(partial(decode_entry_intent_v2, raw))


@pytest.mark.parametrize("generation", [-1000000000000000000, -9223372036854775807, -9223372036854775808])
def test_correction_r2_negative_19_magnitude_digits_owner(generation: int) -> None:
    tree = json.loads(GOLDEN_WIRES["PLAN"])
    tree["fields"]["protection_policy"]["generation"] = generation
    raw = canonical_frame(tree)
    root = actual_intent()
    policy = forged(root.protection_policy, "generation", generation)
    reject(partial(replace, root, protection_policy=policy))
    reject(partial(encode_entry_intent_v2, forged(root, "protection_policy", policy)))
    reject(partial(decode_entry_intent_v2, raw))


@pytest.mark.parametrize("generation", [-1000000000000000000, -9223372036854775807, -9223372036854775808])
def test_correction_r2_scanner_and_callback_magnitude_bound(generation: int) -> None:
    from multi_exchange_trader.execution import risk_v2_codec as codec

    token = str(generation)
    assert codec._integer(token) == generation
    codec._scan('{"generation":' + token + "}")


@pytest.mark.parametrize("seam", ["ProtectionScope", "ProtectionPolicy"])
def test_correction_r1_preserves_shared_codes_and_programming_failures(
    seam: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from multi_exchange_trader.execution import risk_v2_codec as codec

    def meaningful(**kwargs: Any) -> Any:
        raise c.RiskV2Rejected("RISK_V2_REVISION_MISMATCH")

    monkeypatch.setattr(codec, seam, meaningful)
    reject(partial(decode_entry_intent_v2, GOLDEN_WIRES["PLAN"]), "RISK_V2_REVISION_MISMATCH")

    def programming_failure(**kwargs: Any) -> Any:
        raise RuntimeError("deliberate programming-failure sentinel")

    monkeypatch.setattr(codec, seam, programming_failure)
    with pytest.raises(RuntimeError, match="deliberate programming-failure sentinel"):
        decode_entry_intent_v2(GOLDEN_WIRES["PLAN"])


def test_correction_r2_positive_and_canonical_resource_controls() -> None:
    from multi_exchange_trader.execution import risk_v2_codec as codec

    before_limit = sys.get_int_max_str_digits()
    tree = json.loads(GOLDEN_WIRES["PLAN"])
    tree["fields"]["protection_policy"]["generation"] = 9223372036854775807
    restored = decode_entry_intent_v2(canonical_frame(tree))
    assert restored.protection_policy.generation == 9223372036854775807
    for generation in (9223372036854775808, 10000000000000000000, -10000000000000000000):
        tree["fields"]["protection_policy"]["generation"] = generation
        reject(partial(decode_entry_intent_v2, canonical_frame(tree)), "RISK_V2_RESOURCE_LIMIT")
    for token in ("1" * 20, "-" + "1" * 20, "1" * 5001, "-" + "1" * 5001):
        reject(partial(codec._integer, token), "RISK_V2_RESOURCE_LIMIT")
        reject(partial(codec._scan, '{"generation":' + token + "}"), "RISK_V2_RESOURCE_LIMIT")
    raw = GOLDEN_WIRES["PLAN"].replace('"generation":0', '"generation":-0')
    reject(partial(decode_entry_intent_v2, raw))
    for token in ("1.0", "1e18", "NaN", "Infinity", "-Infinity"):
        raw = GOLDEN_WIRES["PLAN"].replace('"generation":0', '"generation":' + token)
        reject(partial(decode_entry_intent_v2, raw))
    assert sys.get_int_max_str_digits() == before_limit
