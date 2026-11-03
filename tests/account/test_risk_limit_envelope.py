"""Synthetic byte, shape, token and transport-value boundary tests."""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

import pytest

from multi_exchange_trader.account import risk_limit_envelope as risk_module
from multi_exchange_trader.account.instrument_envelope import HttpsOrigin
from multi_exchange_trader.account.risk_limit_envelope import (
    BybitRiskLimitEnvelopeReadV1,
    JsonNumberTokenV1,
    _decode_risk_limit_json_v1,
    validate_bybit_risk_limit_envelope_v1,
)
from multi_exchange_trader.execution.risk_v2_math import RiskV2Rejected
from multi_exchange_trader.trading_product import InstrumentKey

RAW = b'{"retCode":0,"result":{"category":"linear","list":[],"nextPageCursor":""},"time":1}'
STAMP = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=1)


def envelope(raw: bytes = RAW, **changes: Any) -> BybitRiskLimitEnvelopeReadV1:
    values: dict[str, Any] = {
        "key": InstrumentKey("BYBIT", "LINEAR", "BTCUSDT"),
        "method": "GET",
        "endpoint": "/v5/market/risk-limit",
        "params": (("category", "linear"), ("symbol", "BTCUSDT")),
        "sent_origin": HttpsOrigin("https", "api.bybit.com", 443),
        "response_origin": HttpsOrigin("https", "api.bybit.com", 443),
        "response_status": 200,
        "request_started_at": STAMP,
        "received_at": STAMP,
        "started_mono": 0.0,
        "received_mono": 1.0,
        "response_time": STAMP,
        "decoded_body_bytes": raw,
        "decoded_body_sha256": hashlib.sha256(raw).hexdigest(),
        "accepted_attempt": 1,
        "physical_start": 0,
        "physical_end": 1,
        "logical_ordinal": 1,
        "observation_id": "obs1",
        "source_id": "source1",
        "source_generation": "gen1",
        "source_environment": "FIXTURE",
    }
    values.update(changes)
    result = BybitRiskLimitEnvelopeReadV1(**values)
    validate_bybit_risk_limit_envelope_v1(result)
    return result


def test_decode_number_spelling_without_float() -> None:
    decoded = _decode_risk_limit_json_v1(b'{"a":0.0050,"b":5e-3,"i":1}')
    assert decoded == {
        "a": JsonNumberTokenV1("0.0050"),
        "b": JsonNumberTokenV1("5e-3"),
        "i": JsonNumberTokenV1("1"),
    }


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"\\u0061":2}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":01}',
        b'{"a":+1}',
        b'{"a":.1}',
        b'{"a":1.}',
        b'{"a":1e}',
        b'{"a":true,}',
        b'{"a":"\\ud800"}',
        b"\xef\xbb\xbf{}",
        b"\xff",
        b"{}{}",
        b"[]",
        b"",
    ],
)
def test_malformed_or_decoded_escape_duplicate_rejected(raw: bytes) -> None:
    with pytest.raises(RiskV2Rejected, match="^RISK_V2_INPUT_INVALID$"):
        _decode_risk_limit_json_v1(raw)


def test_body_boundary_before_decoding() -> None:
    assert _decode_risk_limit_json_v1(b"{}" + b" " * 65534) == {}
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        _decode_risk_limit_json_v1(b"{}" + b" " * 65535)


def test_depth_boundary() -> None:
    assert _decode_risk_limit_json_v1(b'{"x":' + b"[" * 31 + b"0" + b"]" * 31 + b"}")
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        _decode_risk_limit_json_v1(b'{"x":' + b"[" * 32 + b"0" + b"]" * 32 + b"}")


def test_node_and_key_boundaries() -> None:
    assert _decode_risk_limit_json_v1(b'{"x":[' + b"0," * 8189 + b"0]}")
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        _decode_risk_limit_json_v1(b'{"x":[' + b"0," * 8190 + b"0]}")
    raw = ("{" + ",".join(f'"k{i}":0' for i in range(64)) + "}").encode()
    assert len(_decode_risk_limit_json_v1(raw)) == 64
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        _decode_risk_limit_json_v1(raw[:-1] + b',"extra":0}')


@pytest.mark.parametrize(
    "changes",
    [
        {"accepted_attempt": 0},
        {"accepted_attempt": True},
        {"physical_start": -1},
        {"physical_end": 0, "physical_start": 1},
        {"logical_ordinal": 2**31},
        {"response_status": True},
        {"response_status": 201},
        {"started_mono": 0},
        {"received_mono": float("inf")},
        {"received_mono": -1.0},
        {"decoded_body_sha256": "0" * 64},
        {"method": "POST"},
        {"endpoint": "/v5/market/instruments-info"},
        {"observation_id": "bad id"},
        {"source_generation": ""},
        {"source_environment": "LIVE"},
        {"params": (("symbol", "BTCUSDT"), ("category", "linear"))},
        {"params": (("category", "linear"), ("symbol", "BTCUSDT"), ("cursor", ""))},
        {"response_origin": HttpsOrigin("https", "api-testnet.bybit.com", 443)},
        {"source_environment": "PUBLIC_TESTNET"},
    ],
)
def test_invalid_envelope_values(changes: dict[str, Any]) -> None:
    with pytest.raises(RiskV2Rejected):
        envelope(**changes)


def test_envelope_clock_and_hash_and_body_privacy() -> None:
    value = envelope(
        request_started_at=STAMP + timedelta(seconds=1), received_at=STAMP + timedelta(seconds=2)
    )
    assert "decoded_body_bytes" not in repr(value)
    with pytest.raises(RiskV2Rejected, match="CAUSAL_ORDER_INVALID"):
        envelope(received_at=STAMP - timedelta(microseconds=1))
    with pytest.raises(RiskV2Rejected):
        envelope(
            response_time=STAMP + timedelta(milliseconds=1),
            received_at=STAMP + timedelta(seconds=1),
        )


def test_exact_types_before_hostile_hooks_and_partial_record() -> None:
    class Hostile:
        def __eq__(self, other: object) -> bool:
            raise AssertionError("hook executed")

    class EvilZone(tzinfo):
        def utcoffset(self, dt: datetime | None) -> timedelta:
            raise AssertionError("hook executed")

        def dst(self, dt: datetime | None) -> timedelta:
            raise AssertionError("hook executed")

        def tzname(self, dt: datetime | None) -> str:
            raise AssertionError("hook executed")

    value = envelope()
    object.__setattr__(value, "response_status", Hostile())
    with pytest.raises(RiskV2Rejected):
        validate_bybit_risk_limit_envelope_v1(value)
    with pytest.raises(RiskV2Rejected):
        validate_bybit_risk_limit_envelope_v1(object.__new__(BybitRiskLimitEnvelopeReadV1))
    with pytest.raises(RiskV2Rejected):
        envelope(received_at=datetime(2026, 1, 1, tzinfo=EvilZone()))

    class SubBytes(bytes):
        pass

    with pytest.raises(RiskV2Rejected):
        _decode_risk_limit_json_v1(SubBytes(RAW))
    with pytest.raises(RiskV2Rejected):
        replace(envelope(), decoded_body_bytes=b"{}")


def test_optional_metadata_has_same_parser_bounds_and_fixed_errors() -> None:
    raw = RAW.replace(
        b'"retCode":0', b'"retMsg":"PRIVATE PROVIDER TEXT","retExtInfo":{},"retCode":0'
    )
    assert envelope(raw)
    invalid = RAW.replace(b'"retCode":0', b'"retMsg":"PRIVATE PROVIDER TEXT","retCode":1')
    with pytest.raises(RiskV2Rejected) as caught:
        envelope(invalid)
    assert str(caught.value) == "RISK_V2_INPUT_INVALID"
    assert "PRIVATE" not in str(caught.value)
    nested = b"[" * 32 + b"0" + b"]" * 32
    with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
        envelope(RAW.replace(b'"retCode":0', b'"retExtInfo":{"x":' + nested + b'},"retCode":0'))


def test_unexpected_parser_fault_is_not_converted(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*args: object) -> str:
        raise RuntimeError("synthetic programmer fault")

    monkeypatch.setattr(risk_module, "scanstring", unexpected)
    with pytest.raises(RuntimeError, match="synthetic programmer fault"):
        _decode_risk_limit_json_v1(b'{"x":1}')


def test_forged_origin_and_key_subclass_rejected_before_equality() -> None:
    class Hostile:
        def __eq__(self, other: object) -> bool:
            raise AssertionError("hook executed")

    value = envelope()
    object.__setattr__(value.response_origin, "host", Hostile())
    with pytest.raises(RiskV2Rejected):
        validate_bybit_risk_limit_envelope_v1(value)

    class SubKey(InstrumentKey):
        pass

    with pytest.raises(RiskV2Rejected):
        envelope(key=SubKey("BYBIT", "LINEAR", "BTCUSDT"))


def test_number_token_raw_bounds_before_numeric_allocation() -> None:
    for token in (b"1" * 257, b"1e1001", b"1e" + b"9" * 1021, b"1" * 1025):
        with pytest.raises(RiskV2Rejected, match="RESOURCE_LIMIT"):
            _decode_risk_limit_json_v1(b'{"x":' + token + b"}")
