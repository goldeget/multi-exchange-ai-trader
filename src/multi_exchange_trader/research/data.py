"""Content-addressed local datasets: provenance/quality are mandatory, never inferred."""

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

SCHEMA = "RESEARCH_DATA_V1"
SPOT_URL = "https://data-api.binance.vision/api/v3/klines"
HOUR = 3_600_000
FIELDS = {
    "schema",
    "market",
    "symbol",
    "synthetic",
    "interval_ms",
    "start_ms",
    "end_ms",
    "collected_at",
    "source",
    "bars",
    "dataset_id",
}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def checksum(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def decimal(value: Any, *, zero: bool = False) -> Decimal:
    if (
        not isinstance(value, str)
        or len(value) > 32
        or not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d{1,8})?", value)
    ):
        raise ValueError("dataset decimal string precision")
    d = Decimal(value)
    if d < 0 or (not zero and d == 0) or d > 1_000_000_000:
        raise ValueError("dataset value bounds")
    return d


def seal(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "dataset_id": checksum(payload)}


def rows_to_bars(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise TypeError("spot kline list required")
    bars = []
    for row in rows:
        if (
            not isinstance(row, list)
            or len(row) != 12
            or type(row[0]) is not int
            or type(row[6]) is not int
        ):
            raise ValueError("spot kline schema")
        if row[6] != row[0] + HOUR - 1:
            raise ValueError("spot kline close boundary")
        bars.append(
            dict(zip(("time_ms", "open", "high", "low", "close", "volume"), row[:6], strict=True))
        )
    return bars


def validate_dataset(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) not in (FIELDS, FIELDS | {"raw_body"}):
        raise ValueError("dataset schema fields")
    if value["schema"] != SCHEMA:
        raise ValueError("dataset schema version")
    payload = {k: v for k, v in value.items() if k != "dataset_id"}
    if value["dataset_id"] != checksum(payload):
        raise ValueError("dataset checksum mismatch")
    step, start, end = (value[k] for k in ("interval_ms", "start_ms", "end_ms"))
    if (
        any(type(n) is not int for n in (step, start, end))
        or step not in (60_000, HOUR)
        or start < 0
        or start % step
        or end <= start
        or (end - start) % step
    ):
        raise ValueError("dataset interval/period alignment")
    try:
        t = datetime.fromisoformat(value["collected_at"])
        offset = t.utcoffset()
        if (
            offset is None
            or offset.total_seconds() != 0
            or t > datetime.now(UTC)
            or end > int(t.timestamp() * 1000)
        ):
            raise ValueError("dataset future/unclosed period")
    except (TypeError, AttributeError):
        raise ValueError("dataset UTC collection time") from None
    synthetic = value["synthetic"]
    if type(synthetic) is not bool:
        raise ValueError("dataset synthetic flag")
    if synthetic:
        if (
            value["market"] != "synthetic_perpetual"
            or value["symbol"] not in ("BTC-PAPER", "ETH-PAPER")
            or value["source"] != {"generator": "SYNTHETIC_PAPER_V1"}
            or "raw_body" in value
        ):
            raise ValueError("synthetic perpetual provenance")
    else:
        if (
            value["market"] != "spot"
            or value["symbol"] not in ("BTCUSDT", "ETHUSDT")
            or step != HOUR
        ):
            raise ValueError("actual spot provenance")
        source = value["source"]
        if (
            not isinstance(source, dict)
            or set(source) != {"url", "raw_sha256", "http_status", "request"}
            or source["url"] != SPOT_URL
            or source["http_status"] != 200
        ):
            raise ValueError("fixed spot source required")
        request = {
            "symbol": value["symbol"],
            "interval": "1h",
            "startTime": start,
            "endTime": end - 1,
            "limit": 1000,
        }
        if source["request"] != request:
            raise ValueError("spot request provenance mismatch")
        raw = value.get("raw_body")
        if (
            not isinstance(raw, str)
            or len(raw.encode()) > 1_000_000
            or hashlib.sha256(raw.encode()).hexdigest() != source["raw_sha256"]
        ):
            raise ValueError("raw response checksum")
        if rows_to_bars(json.loads(raw)) != value["bars"]:
            raise ValueError("raw versus normalized spot mismatch")
    bars = value["bars"]
    if (
        not isinstance(bars, list)
        or not 1 <= len(bars) <= 1000
        or len(bars) != (end - start) // step
    ):
        raise ValueError("dataset gap/count")
    for index, bar in enumerate(bars):
        if (
            not isinstance(bar, dict)
            or set(bar) != {"time_ms", "open", "high", "low", "close", "volume"}
            or type(bar["time_ms"]) is not int
            or bar["time_ms"] != start + index * step
        ):
            raise ValueError("dataset duplicate/gap/order")
        o, h, lo, c = (decimal(bar[k]) for k in ("open", "high", "low", "close"))
        decimal(bar["volume"], zero=True)
        tick = Decimal("0.1") if value["symbol"] == "BTC-PAPER" else Decimal("0.01")
        if lo > min(o, c) or h < max(o, c) or lo > h or any(p % tick for p in (o, h, lo, c)):
            raise ValueError("dataset OHLC/tick consistency")
    return value


def synthetic_dataset(symbol: str = "BTC-PAPER") -> dict[str, Any]:
    if symbol not in ("BTC-PAPER", "ETH-PAPER"):
        raise ValueError("synthetic instrument")
    base = Decimal("60000.0") if symbol == "BTC-PAPER" else Decimal("3000.00")
    step = Decimal("40.0") if symbol == "BTC-PAPER" else Decimal("2.00")
    start = 1767225600000
    bars = []
    previous = base
    for i in range(240):
        phase = i % 48
        close = base + step * (phase if phase <= 24 else 48 - phase)
        bars.append(
            {
                "time_ms": start + i * 60_000,
                "open": str(previous),
                "high": str(max(previous, close) + step),
                "low": str(min(previous, close) - step),
                "close": str(close),
                "volume": "10.00",
            }
        )
        previous = close
    return validate_dataset(
        seal(
            {
                "schema": SCHEMA,
                "market": "synthetic_perpetual",
                "symbol": symbol,
                "synthetic": True,
                "interval_ms": 60_000,
                "start_ms": start,
                "end_ms": start + len(bars) * 60_000,
                "collected_at": "2026-01-02T00:00:00+00:00",
                "source": {"generator": "SYNTHETIC_PAPER_V1"},
                "bars": bars,
            }
        )
    )


def save_dataset(root: Path, value: dict[str, Any]) -> str:
    validate_dataset(value)
    root.mkdir(parents=True, exist_ok=True)
    key = value["dataset_id"]
    target = root / (key + ".json")
    raw = canonical(value) + "\n"
    try:
        with target.open("x") as f:
            f.write(raw)
    except FileExistsError:
        if target.read_text() != raw:
            raise ValueError("immutable dataset conflict") from None
    return str(key)


def load_dataset(root: Path, key: str) -> dict[str, Any]:
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
        raise ValueError("dataset ID required")
    try:
        value = validate_dataset(json.loads((root / (key + ".json")).read_text()))
    except OSError:
        raise ValueError("dataset unavailable") from None
    if value["dataset_id"] != key:
        raise ValueError("dataset path identity mismatch")
    return value
