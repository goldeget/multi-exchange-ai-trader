"""Offline provenance/quality contracts; synthetic rows never prove live collection."""

import copy
from decimal import Decimal

import pytest


def api():
    from multi_exchange_trader.research import data

    return data


def test_synthetic_identity_precision_and_immutable_roundtrip(tmp_path):
    d = api().synthetic_dataset()
    assert d["market"] == "synthetic_perpetual" and d["synthetic"] is True
    assert d["source"] == {"generator": "SYNTHETIC_PAPER_V1"}
    assert len(d["bars"]) == 240
    key = api().save_dataset(tmp_path, d)
    assert key == d["dataset_id"] and api().load_dataset(tmp_path, key) == d
    assert api().synthetic_dataset() == d


@pytest.mark.parametrize(
    "fault",
    [
        "gap",
        "duplicate",
        "reverse",
        "nan",
        "float",
        "negative",
        "ohlc",
        "unclosed",
        "market",
        "source",
        "future_collection",
    ],
)
def test_bad_quality_or_provenance_fails_even_with_recomputed_checksum(fault):
    d = copy.deepcopy(api().synthetic_dataset())
    if fault == "gap":
        d["bars"].pop(4)
    elif fault == "duplicate":
        d["bars"][4] = d["bars"][3].copy()
    elif fault == "reverse":
        d["bars"][3:5] = reversed(d["bars"][3:5])
    elif fault == "nan":
        d["bars"][4]["close"] = "NaN"
    elif fault == "float":
        d["bars"][4]["close"] = 60000.0
    elif fault == "negative":
        d["bars"][4]["volume"] = "-1"
    elif fault == "ohlc":
        d["bars"][4]["high"] = "1.0"
    elif fault == "unclosed":
        d["end_ms"] = d["start_ms"] + 1
    elif fault == "market":
        d["market"] = "spot"
    elif fault == "source":
        d["source"] = {"url": "https://fapi.binance.com/fapi/v1/klines"}
    else:
        d["collected_at"] = "2999-01-01T00:00:00+00:00"
    d["dataset_id"] = api().checksum({k: v for k, v in d.items() if k != "dataset_id"})
    with pytest.raises((ValueError, TypeError)):
        api().validate_dataset(d)


def test_checksum_and_path_tampering_fail_closed(tmp_path):
    d = api().synthetic_dataset()
    key = api().save_dataset(tmp_path, d)
    p = tmp_path / (key + ".json")
    p.write_text(p.read_text().replace("60000.0", "61000.0", 1))
    with pytest.raises(ValueError):
        api().load_dataset(tmp_path, key)
    with pytest.raises(ValueError):
        api().load_dataset(tmp_path, "../secret")


def test_eth_fixture_has_own_units_and_source():
    d = api().synthetic_dataset("ETH-PAPER")
    assert d["symbol"] == "ETH-PAPER"
    assert Decimal(d["bars"][0]["open"]) == Decimal("3000.00")
    assert d["dataset_id"] != api().synthetic_dataset()["dataset_id"]
