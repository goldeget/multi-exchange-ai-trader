"""Never let API lifespan tests open an operator's preserved local PAPER/research DB."""

import pytest


@pytest.fixture(autouse=True)
def isolated_api_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "manual-paper.sqlite3"))
    monkeypatch.setenv("PHASE4_DB_PATH", str(tmp_path / "research.sqlite3"))
    monkeypatch.setenv("PHASE4_DATA_ROOT", str(tmp_path / "datasets"))
