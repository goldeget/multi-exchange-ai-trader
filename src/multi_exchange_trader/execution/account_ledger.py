"""Exact account-wide USDT realized accounting, without invented qualification.

The daily baseline is an explicit 00:00 UTC wallet cash observation. It is not
zero capital and is not inferred from current equity or the first transaction.
Cash-chain reconciliation establishes arithmetic consistency only; REST history
can lag and does not attest event completeness. Drawdown, consecutive trade
losses, unrealized exposure/stop risk, and ownership require separate evidence.
No function here manufactures AccountEvidence.ledger_qualified=True.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Literal

from .history import HistoryError, HistorySnapshot, amount, milliseconds, utc
from .protection import ProtectionScope


@dataclass(frozen=True)
class DailyCashBaseline:
    scope: ProtectionScope
    at: datetime
    cash_balance: Decimal
    source_id: str
    currency: str = "USDT"


@dataclass(frozen=True)
class DailyRealizedLoss:
    scope: ProtectionScope
    snapshot_id: str
    baseline_source_id: str
    loss_day: date
    start_at: datetime
    through_ms: int
    observed_at: datetime
    opening_cash_balance: Decimal
    ending_cash_balance: Decimal
    realized_cash_flow: Decimal
    funding_received: Decimal
    trading_fees_paid: Decimal
    net_realized: Decimal
    daily_loss: Decimal
    net_transfers: Decimal
    transaction_count: int
    loss_accounting: Literal["UTC_REALIZED_NET_FEES_FUNDING"] = field(
        default="UTC_REALIZED_NET_FEES_FUNDING", init=False)
    window_complete: Literal[True] = field(default=True, init=False)
    ledger_qualified: Literal[False] = field(default=False, init=False)
    qualification_blockers: tuple[str, ...] = field(default=(
        "VENUE_EVENT_COMPLETENESS_UNPROVEN", "ACCOUNT_EXPOSURE_RISK_UNRECONCILED",
        "DRAWDOWN_AND_TRADE_LOSS_SEQUENCE_UNPROVEN"), init=False)


def daily_realized_loss(history: HistorySnapshot, baseline: DailyCashBaseline, *,
                        now: datetime, max_age: timedelta = timedelta(seconds=30)) -> DailyRealizedLoss:
    """Reconcile every transaction, including external activity; exclude only transfers.

    Window completeness means exact midnight-to-watermark query coverage. It is
    deliberately different from venue-event completeness/ledger qualification.
    The baseline/source object is a caller-supplied observation, never authority.
    """
    utc(now)
    if (type(history) is not HistorySnapshot or type(baseline) is not DailyCashBaseline
            or type(max_age) is not timedelta or not timedelta(0) < max_age <= timedelta(minutes=5)):
        raise HistoryError("LEDGER_INPUT_INVALID")
    utc(baseline.at)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if (baseline.scope != history.scope or baseline.currency != "USDT" or baseline.at != midnight
            or type(baseline.source_id) is not str or not baseline.source_id.strip()
            or len(baseline.source_id) > 256 or type(baseline.cash_balance) is not Decimal):
        raise HistoryError("LEDGER_EXPLICIT_UTC_BASELINE_REQUIRED")
    opening = amount(str(baseline.cash_balance))
    now_ms = milliseconds(now.replace(microsecond=now.microsecond // 1000 * 1000))
    if (not history.page_complete or not history.transactions_account_wide
            or history.window.start_ms != milliseconds(midnight)
            or history.window.end_ms > now_ms
            or not timedelta(0) <= now - history.observed_at <= max_age
            or not timedelta(0) <= now - history.server_observed_at <= max_age
            or now_ms - history.window.end_ms
                > int(max_age.total_seconds() * 1000)):
        raise HistoryError("LEDGER_COMPLETE_CURRENT_UTC_WINDOW_REQUIRED")
    transactions = history.transactions
    if len({row.transaction_id for row in transactions}) != len(transactions):
        raise HistoryError("LEDGER_DUPLICATE_TRANSACTION")
    # Execution history is symbol-scoped, transaction history is account-wide.
    # A missing counterpart is ingestion lag/incompleteness, never a free trade.
    economic = [row for row in transactions if row.transaction_type in {"TRADE", "ADL", "LIQUIDATION"}]
    by_trade = {row.trade_id: row for row in economic}
    if any(not row.trade_id for row in economic) or len(by_trade) != len(economic):
        raise HistoryError("LEDGER_AMBIGUOUS_TRADE_ID")
    for execution in history.executions:
        if execution.execution_type == "Funding":
            continue
        if execution.execution_type not in {"Trade", "AdlTrade", "BustTrade"}:
            # Also guard caller-constructed snapshots: Delivery and unknown kinds
            # cannot escape both the trade and funding reconciliation paths.
            raise HistoryError("LEDGER_EXECUTION_TYPE_UNSUPPORTED")
        row = by_trade.get(execution.execution_id)
        if (row is None or row.venue_order_id != execution.venue_order_id
                or row.symbol != execution.symbol or row.fee != execution.fee):
            raise HistoryError("LEDGER_EXECUTION_COST_UNRECONCILED")
    with localcontext() as context:
        context.prec = 100
        execution_funding: dict[tuple[str, int], Decimal] = {}
        transaction_funding: dict[tuple[str, int], Decimal] = {}
        for execution in history.executions:
            if execution.execution_type == "Funding":
                key = (execution.symbol, execution.time_ms)
                execution_funding[key] = execution_funding.get(key, Decimal(0)) + execution.fee
        for transaction in transactions:
            if transaction.transaction_type == "SETTLEMENT":
                key = (transaction.symbol, transaction.time_ms)
                transaction_funding[key] = (
                    transaction_funding.get(key, Decimal(0)) + transaction.funding)
        for key, fee in execution_funding.items():
            # Bybit documents opposite signs in the two endpoints. A timestamp
            # mismatch remains unresolved rather than joining unrelated settlements.
            if key not in transaction_funding or fee != -transaction_funding[key]:
                raise HistoryError("LEDGER_EXECUTION_FUNDING_UNRECONCILED")
        cash = funding = fees = transfers = Decimal(0)
        balance = opening
        # Equal-time rows have no venue sequence. Follow balance links only when
        # uniquely resolvable; guessing an order could hide an omitted event.
        pending = sorted(transactions, key=lambda row: row.time_ms)
        while pending:
            time_ms = pending[0].time_ms
            same_time = [row for row in pending if row.time_ms == time_ms]
            pending = [row for row in pending if row.time_ms != time_ms]
            while same_time:
                matches = [row for row in same_time if balance + row.change == row.cash_balance]
                if len(matches) != 1:
                    raise HistoryError("LEDGER_CASH_CHAIN_MISSING_OR_AMBIGUOUS")
                row = matches[0]
                same_time.remove(row)
                if not history.window.start_ms <= row.time_ms <= history.window.end_ms:
                    raise HistoryError("LEDGER_TRANSACTION_OUTSIDE_DAY")
                if row.currency != "USDT" or row.change != row.cash_flow + row.funding - row.fee:
                    raise HistoryError("LEDGER_CASH_EQUATION_INVALID")
                balance = row.cash_balance
                if row.is_transfer:
                    if row.fee != 0 or row.funding != 0:
                        raise HistoryError("LEDGER_TRANSFER_AMBIGUOUS")
                    transfers += row.cash_flow
                elif row.transaction_type in {"TRADE", "SETTLEMENT", "LIQUIDATION", "ADL"}:
                    if row.category != "linear":
                        raise HistoryError("LEDGER_ACCOUNT_ACTIVITY_UNSUPPORTED")
                    cash += row.cash_flow
                    funding += row.funding
                    fees += row.fee
                else:
                    raise HistoryError("LEDGER_TRANSACTION_TYPE_UNSUPPORTED")
        net = cash + funding - fees
        if opening + net + transfers != balance:
            raise HistoryError("LEDGER_CASH_RECONCILIATION_FAILED")
        return DailyRealizedLoss(history.scope, history.snapshot_id, baseline.source_id,
            midnight.date(), midnight, history.window.end_ms, history.observed_at,
            opening, balance, cash, funding, fees, net, max(Decimal(0), -net), transfers,
            len(transactions))
