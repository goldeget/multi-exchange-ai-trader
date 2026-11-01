"""Never let API lifespan tests open an operator's preserved local PAPER/research DB."""

import pytest


@pytest.fixture(autouse=True)
def isolated_api_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "manual-paper.sqlite3"))
    monkeypatch.setenv("PHASE4_DB_PATH", str(tmp_path / "research.sqlite3"))
    monkeypatch.setenv("PHASE4_DATA_ROOT", str(tmp_path / "datasets"))
    monkeypatch.setenv("RECOMMENDATION_DB_PATH", str(tmp_path / "recommendations.sqlite3"))
    # Never read the operator .env or inherited Bybit keys during tests.
    monkeypatch.setenv("BYBIT_ENV_FILE", "off")
    monkeypatch.delenv("BYBIT_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET", raising=False)
    monkeypatch.delenv("BYBIT_DEMO", raising=False)
    monkeypatch.delenv("BYBIT_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_DEMO_API_SECRET", raising=False)
    monkeypatch.delenv("BYBIT_TESTNET", raising=False)
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
