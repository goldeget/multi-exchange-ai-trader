from concurrent.futures import ThreadPoolExecutor

import pytest

from multi_exchange_trader.execution.risk import RiskScope
from multi_exchange_trader.execution.runtime_policy import KEYS, RuntimePolicyBook


def test_unset_settings_survive_restart_without_authority(tmp_path):
    path = tmp_path / "policy.sqlite3"
    book = RuntimePolicyBook(path)
    draft = book.read()["policy"]
    assert set(draft) == set(KEYS)
    assert all(value is None for value in draft.values())
    draft.update(capital_usdt="100", leverage_min="1", leverage_max="3")
    result = book.save(0, draft)
    assert result["live_order_enabled"] is False
    assert result["desired_state"] == "STOPPED"
    assert "per_trade_loss" in result["missing_fields"]
    assert RuntimePolicyBook(path).read() == result
    with pytest.raises(ValueError):
        book.risk_policy(RiskScope("a", "LIVE", "BTCUSDT", "s"))


def test_concurrent_policy_updates_do_not_overwrite(tmp_path):
    book = RuntimePolicyBook(tmp_path / "policy.sqlite3")
    draft = book.read()["policy"]

    def save(_):
        try:
            return book.save(0, draft)["revision"]
        except ValueError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(map(str, pool.map(save, range(2)))) == ["1", "conflict"]


@pytest.mark.parametrize("patch", [
    {"capital_usdt": True}, {"capital_usdt": "1e5"},
    {"leverage_min": "4", "leverage_max": "3"},
    {"allowed_symbols": ["BTCUSDT"], "selected_symbols": ["ETHUSDT"]},
    {"allowed_symbols": [[], "BTCUSDT"]},
    {"capital_usdt": "100", "max_total_margin": "101"},
    {"liquidation_buffer_fraction": "1"}, {"max_positions": True},
])
def test_invalid_policy_leaves_revision_unchanged(tmp_path, patch):
    book = RuntimePolicyBook(tmp_path / "policy.sqlite3")
    draft = book.read()["policy"] | patch
    with pytest.raises(ValueError):
        book.save(0, draft)
    assert book.read()["revision"] == 0
