"""Observed DEMO transaction empty/null compatibility; page exhaustion is not venue completeness."""
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_history import (
    MS,
    NOW,
    SCOPE,
    START,
    baseline,
    execution,
    order,
    pages,
    reader,
    transaction,
)

from multi_exchange_trader.execution.account_ledger import daily_realized_loss
from multi_exchange_trader.execution.history import (
    HistoryError,
    validate_history_snapshot,
)
from multi_exchange_trader.execution.history_store import HistoryStore

TX = "/v5/account/transaction-log"
CURSOR = "mock:" + "C" * 22


async def read(data: dict[str, Any], *, max_pages: int = 20) -> tuple[Any, list[Any]]:
    session, adapter, requests = await reader(data, max_pages=max_pages)
    try:
        return await adapter.read_history("BTCUSDT", start_ms=START, end_ms=MS), requests
    finally:
        await session.aclose()


async def test_observed_seven_transactions_then_empty_null_preserves_full_history_store(tmp_path: Path) -> None:
    executions, orders, transactions = [], [], []
    for index in range(1, 7):
        stamp = START + index * 1000
        executions.append(execution(execId=f"e{index}", orderId=f"o{index}", execTime=str(stamp)))
        orders.append(order(orderId=f"o{index}", createdTime=str(stamp - 500), updatedTime=str(stamp)))
        transactions.append(transaction(id=f"t{index}", tradeId=f"e{index}", orderId=f"o{index}",
            transactionTime=str(stamp), cashBalance=str(Decimal(1000) - Decimal("2.1") * index)))
    transactions.append(transaction(id="transfer-7", tradeId="", orderId="", symbol="", category="",
        type="TRANSFER_IN", transactionTime=str(START + 7000), cashFlow="10", funding="0", fee="0",
        change="10", cashBalance="997.4"))
    data = pages(executions=executions, orders=orders)

    def tx_page(request: Any) -> dict[str, Any]:
        if "cursor" not in request.url.params:
            return {"list": transactions, "nextPageCursor": CURSOR}
        assert request.url.params["cursor"] == CURSOR and len(CURSOR) == 27
        return {"list": [], "nextPageCursor": None}

    data[TX] = tx_page
    evidence, requests = await read(data)
    assert len([request for request in requests if request.url.path == TX]) == 2
    assert len(evidence.transactions) == 7 and len(evidence.executions) == 6 and len(evidence.orders) == 6
    assert evidence.page_complete and evidence.transactions_account_wide
    assert not evidence.venue_event_complete and not evidence.flat_verified
    validate_history_snapshot(evidence)
    loss = daily_realized_loss(evidence, baseline(), now=NOW)
    assert loss.daily_loss == Decimal("12.6") and not loss.ledger_qualified
    with HistoryStore(tmp_path / "history.sqlite3", SCOPE) as store:
        receipt = store.ingest(evidence)
        restored = store._stored_observation(receipt.observation_key)
        assert restored.transactions == evidence.transactions
        assert store.execution_evidence(receipt.observation_key, "e6").record == evidence.executions[-1]
        assert not restored.venue_event_complete and not restored.flat_verified


async def test_first_empty_transaction_null_does_not_prove_complete_empty_history() -> None:
    data = pages()
    data[TX]["nextPageCursor"] = None
    with pytest.raises(HistoryError, match="HISTORY_TEXT_INVALID"):
        await read(data)


@pytest.mark.parametrize("shape", [
    "missing", "nonempty-null", "boolean", "integer", "list", "mapping", "blank", "control", "nonascii",
])
async def test_null_compatibility_does_not_accept_unknown_or_malformed_cursor(shape: str) -> None:
    malformed: dict[str, Any] = {"list": []}
    values: dict[str, Any] = {"nonempty-null": None, "boolean": False, "integer": 0, "list": [],
        "mapping": {}, "blank": " ", "control": "bad\n", "nonascii": "é"}
    if shape != "missing":
        malformed["nextPageCursor"] = values[shape]
    if shape == "nonempty-null":
        malformed["list"] = [transaction()]
    data = pages()
    data[TX] = malformed
    with pytest.raises(HistoryError, match="HISTORY_TEXT_INVALID"):
        await read(data)


@pytest.mark.parametrize("length", [256, 257])
async def test_existing_cursor_length_boundary_and_verbatim_query_are_preserved(length: int) -> None:
    cursor = "C" * length
    data = pages()

    def tx_page(request: Any) -> dict[str, Any]:
        if "cursor" not in request.url.params:
            return {"list": [transaction()], "nextPageCursor": cursor}
        assert request.url.params["cursor"] == cursor
        return {"list": [], "nextPageCursor": None}

    data[TX] = tx_page
    if length == 257:
        with pytest.raises(HistoryError, match="HISTORY_TEXT_INVALID"):
            await read(data)
    else:
        evidence, requests = await read(data)
        assert len(evidence.transactions) == 1
        assert len([request for request in requests if request.url.path == TX]) == 2


@pytest.mark.parametrize("path", ["/v5/execution/list", "/v5/order/history"])
async def test_observed_null_terminal_is_not_generalized_to_other_endpoints(path: str) -> None:
    data = pages()
    data[path]["nextPageCursor"] = None
    with pytest.raises(HistoryError, match="HISTORY_TEXT_INVALID"):
        await read(data)


@pytest.mark.parametrize("shape", ["cycle", "empty-with-cursor", "cap"])
async def test_original_pagination_completeness_guards_still_block(shape: str) -> None:
    data = pages()

    def tx_page(request: Any) -> dict[str, Any]:
        return {"list": [] if shape == "empty-with-cursor" else [transaction()], "nextPageCursor": CURSOR}

    data[TX] = tx_page
    with pytest.raises(HistoryError, match="HISTORY_TRUNCATED" if shape == "cap" else "HISTORY_CURSOR_INVALID"):
        await read(data, max_pages=1 if shape == "cap" else 20)
