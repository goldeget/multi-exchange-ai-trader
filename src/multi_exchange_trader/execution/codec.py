"""Closed JSON schema for offline execution records; never imports payload types.

Decimals retain their string representation. UTC timestamps, immutable tuples,
and every conflict's original evidence survive persistence. Invalid restores fail.
"""

from __future__ import annotations

import json
import types
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal, Union, get_args, get_origin, get_type_hints

from . import protection as p
from . import risk as r

_RECORDS: tuple[type[object], ...] = (
    p.ProtectionScope, p.OwnedOrder, p.FillEvent, p.ProjectionConflict,
    p.ProtectionProjection, p.ProtectionPolicy, p.ProtectionEvidence,
    p.PositionEvidence, p.ProtectionAssessment, p.FlatEvidence,
    r.RiskScope, r.UserRiskPolicy, r.EntryProposal, r.MarketEvidence,
    r.InstrumentEvidence, r.RiskTier, r.TierEvidence, r.AccountEvidence,
    r.ReservationTotals, r.LiquidationEvidence, r.LeverageReadback, r.EntryDecision,
)
_REGISTRY = {cls.__name__: cls for cls in _RECORDS}


def _matches(value: object, expected: object) -> bool:
    origin = get_origin(expected)
    args = get_args(expected)
    if origin in (Union, types.UnionType):
        return any(_matches(value, item) for item in args)
    if origin is Literal:
        return value in args and any(type(value) is type(item) for item in args)
    if origin is tuple:
        return (type(value) is tuple and len(args) == 2 and args[1] is Ellipsis
                and all(_matches(item, args[0]) for item in value))
    return type(value) is expected


def _validate_record(value: object) -> None:
    if not is_dataclass(value) or isinstance(value, type):
        raise TypeError("explicit supported record required")
    hints = get_type_hints(type(value))
    if any(not _matches(getattr(value, field.name), hints[field.name])
           for field in fields(value)):
        raise ValueError("record field differs from explicit schema")


def _pack(value: object, depth: int = 0) -> object:
    if depth > 32:
        raise ValueError("record nesting exceeds bound")
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is Decimal:
        if (not value.is_finite() or len(value.as_tuple().digits) > 65536
                or abs(int(value.as_tuple().exponent)) > 65536):
            raise ValueError("finite bounded Decimal required")
        return {"decimal": str(value)}
    if type(value) is datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("UTC datetime required")
        return {"utc": value.isoformat()}
    if type(value) is date:
        return {"date": value.isoformat()}
    if type(value) is tuple:
        return {"tuple": [_pack(item, depth + 1) for item in value]}
    if type(value) in _RECORDS:
        _validate_record(value)
        assert is_dataclass(value) and not isinstance(value, type)
        return {"record": type(value).__name__, "fields": {
            item.name: _pack(getattr(value, item.name), depth + 1) for item in fields(value)}}
    raise TypeError("unsupported execution codec value")


def _unpack(value: object, depth: int = 0) -> object:
    if depth > 32:
        raise ValueError("record nesting exceeds bound")
    if value is None or type(value) in (str, bool, int):
        return value
    if not isinstance(value, dict):
        raise TypeError("explicit tagged JSON required")
    if set(value) == {"decimal"} and type(value["decimal"]) is str:
        result = Decimal(value["decimal"])
        _pack(result)
        return result
    if set(value) == {"utc"} and type(value["utc"]) is str:
        instant = datetime.fromisoformat(value["utc"])
        _pack(instant)
        return instant
    if set(value) == {"date"} and type(value["date"]) is str:
        return date.fromisoformat(value["date"])
    if set(value) == {"tuple"} and type(value["tuple"]) is list:
        return tuple(_unpack(item, depth + 1) for item in value["tuple"])
    if (set(value) == {"record", "fields"} and type(value["record"]) is str
            and type(value["fields"]) is dict):
        cls = _REGISTRY.get(value["record"])
        if cls is None:
            raise ValueError("unknown record schema")
        hints = get_type_hints(cls)
        raw = value["fields"]
        if set(raw) != set(hints):
            raise ValueError("missing or extra record fields")
        kwargs = {key: _unpack(item, depth + 1) for key, item in raw.items()}
        if any(not _matches(kwargs[key], expected) for key, expected in hints.items()):
            raise ValueError("record field differs from explicit schema")
        restored = cls(**kwargs)
        _validate_record(restored)
        return restored
    raise ValueError("unknown JSON schema")


def encode(value: object) -> str:
    payload = json.dumps(_pack(value), ensure_ascii=True, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    if len(payload.encode("utf-8")) > 65536:
        raise ValueError("execution record exceeds 64 KiB")
    return payload


def decode(payload: str) -> object:
    if type(payload) is not str or len(payload.encode("utf-8")) > 65536:
        raise ValueError("bounded JSON string required")
    return _unpack(json.loads(payload))
