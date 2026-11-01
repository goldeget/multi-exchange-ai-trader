"""Local read composition. Each ledger retains its own funds and execution scope."""

import sqlite3
from typing import Any, Protocol


class PaperReads(Protocol):
    def scenarios(self) -> list[str]: ...
    def state(self, scenario: str) -> dict[str, Any]: ...


class ResearchReads(Protocol):
    def runs(self) -> list[dict[str, Any]]: ...


def read_overview(paper: PaperReads, research: ResearchReads, *, spot: PaperReads | None = None) -> dict[str, Any]:
    ledgers: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    rows: list[dict[str, Any]] = []
    try:
        scenarios = paper.scenarios()
    except (ValueError, TypeError, sqlite3.Error):
        scenarios = []
        errors.append({"component": "manual_paper", "code": "PAPER_READ_UNAVAILABLE"})
    for scenario in scenarios:
        try:
            state = paper.state(scenario)
            ledgers.append({"id": scenario, "source": "SYNTHETIC", "execution": "PAPER",
                            "product": "SYNTHETIC_PERPETUAL", "state": state})
        except (ValueError, TypeError, sqlite3.Error):
            errors.append({"component": "manual_paper", "id": scenario,
                           "code": "PAPER_READ_UNAVAILABLE"})
    if spot is not None:
        try:
            spot_ids = spot.scenarios()
        except (ValueError, TypeError, sqlite3.Error):
            spot_ids = []
            errors.append({"component": "manual_spot", "code": "SPOT_READ_UNAVAILABLE"})
        for scenario in spot_ids:
            try:
                state = spot.state(scenario)
                ledgers.append({"id": scenario, "source": "SYNTHETIC", "execution": "PAPER",
                                "product": "SPOT", "state": state})
            except (ValueError, TypeError, sqlite3.Error):
                errors.append({"component": "manual_spot", "id": scenario, "code": "SPOT_READ_UNAVAILABLE"})
    try:
        rows = research.runs()
    except (ValueError, TypeError, sqlite3.Error):
        errors.append({"component": "automation", "code": "RESEARCH_READ_UNAVAILABLE"})
    return {"schema_version": 1, "ledgers": ledgers,
            "automation": {"source": "SYNTHETIC", "execution": "PAPER", "runs": rows},
            "account": {"source": "ACCOUNT_READONLY", "execution": "NONE",
                        "state": "NOT_FETCHED", "live_order_enabled": False},
            "errors": errors, "live_order_enabled": False}
