"""Explicit equity-epoch observations, never ledger or financial qualification.

Only the retained original assembler and Session can add observations. Persisted
values survive restarts, but are historical data until required against their
current original read. REST query exhaustion is not event completeness. Fees and
funding remain in equity/episode net once; only external transfers are neutralized.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Any, Literal, NoReturn, cast

from .account_reconciliation_v1 import _body, _primitive
from .bybit_linear_model_inputs_v1 import bybit_source_ref_sha256_v1
from .bybit_linear_source_assembler_v1 import (
    BybitLinearSourceAssemblerV1,
    BybitLinearVenueReadV1,
    _Read,
)
from .history import ExecutionRecord, HistorySnapshot, amount, validate_history_snapshot
from .history_receipts import AuthenticatedHistoryReceipt
from .journal import CommandKind, CommandState, ProjectionRecord, RevisionConflict

PROJECTION_KEY = "account-loss-metrics-v1"
RULES_REVISION: Literal["EQUITY_EPOCH_TRANSFER_NEUTRAL_CLOSED_POSITION_NET_V1"] = (
    "EQUITY_EPOCH_TRANSFER_NEUTRAL_CLOSED_POSITION_NET_V1"
)
_BLOCKERS = (
    "VENUE_EVENT_COMPLETENESS_UNPROVEN",
    "OBSERVATION_BOUNDARY_UNPROVEN",
    "ACCOUNT_EXPOSURE_RISK_UNRECONCILED",
)


class AccountLossMetricsError(ValueError):
    """Fixed local codes; provider content and credentials never enter errors."""


def _fail(code: str = "ACCOUNT_LOSS_METRICS_INVALID") -> NoReturn:
    raise AccountLossMetricsError(code)


def _json(value: object) -> str:
    result = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    if len(result.encode()) > 65536:
        _fail("ACCOUNT_LOSS_METRICS_BOUND_EXCEEDED")
    return result


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _history_hash(snapshot: HistorySnapshot) -> str:
    return _digest(_primitive(asdict(snapshot)))


@dataclass(frozen=True)
class _Episode:
    position_id: str
    symbol: str
    side: str
    quantity: str
    net: str


@dataclass(frozen=True)
class AccountLossMetricsV1:
    """Calculated observations; copying or restoring data grants no currentness."""

    revision: int
    activation_id: str
    observation_id: str
    activation_at: datetime
    initial_equity: Decimal
    cumulative_net_transfers: Decimal
    performance_equity: Decimal
    observed_high_water: Decimal
    observed_drawdown: Decimal
    observed_max_drawdown: Decimal
    observed_closed_episodes: int
    observed_consecutive_losses: int
    watermark_ms: int
    cash_balance: Decimal
    observed_at: datetime
    source_hashes: tuple[str, ...]
    open_episodes: tuple[_Episode, ...]
    last_closed_positions: tuple[tuple[str, str], ...]
    rules_revision: Literal["EQUITY_EPOCH_TRANSFER_NEUTRAL_CLOSED_POSITION_NET_V1"] = field(
        default=RULES_REVISION, init=False
    )
    consecutive_losses: None = field(default=None, init=False)
    daily_loss: None = field(default=None, init=False)
    ledger_qualified: Literal[False] = field(default=False, init=False)
    blockers: tuple[str, ...] = field(default=_BLOCKERS, init=False)


def _payload(value: AccountLossMetricsV1) -> dict[str, Any]:
    result = asdict(value)
    for key in ("activation_at", "observed_at"):
        result[key] = result[key].isoformat()
    for key in (
        "initial_equity",
        "cumulative_net_transfers",
        "performance_equity",
        "observed_high_water",
        "observed_drawdown",
        "observed_max_drawdown",
        "cash_balance",
    ):
        result[key] = str(result[key])
    return {"schema_version": 1, "metrics": result}


def _decode(record: ProjectionRecord) -> AccountLossMetricsV1:
    try:
        document = json.loads(record.payload_json)
        if (
            type(document) is not dict
            or set(document) != {"schema_version", "metrics"}
            or document["schema_version"] != 1
        ):
            _fail()
        data = document["metrics"]
        if type(data) is not dict:
            _fail()
        value = AccountLossMetricsV1(
            record.revision,
            data["activation_id"],
            data["observation_id"],
            datetime.fromisoformat(data["activation_at"]),
            amount(data["initial_equity"]),
            amount(data["cumulative_net_transfers"]),
            amount(data["performance_equity"]),
            amount(data["observed_high_water"]),
            amount(data["observed_drawdown"]),
            amount(data["observed_max_drawdown"]),
            data["observed_closed_episodes"],
            data["observed_consecutive_losses"],
            data["watermark_ms"],
            amount(data["cash_balance"]),
            datetime.fromisoformat(data["observed_at"]),
            tuple(data["source_hashes"]),
            tuple(_Episode(**episode) for episode in data["open_episodes"]),
            cast(
                "tuple[tuple[str, str], ...]",
                tuple(tuple(item) for item in data["last_closed_positions"]),
            ),
        )
        if (
            _json(_payload(value)) != record.payload_json
            or value.revision != data["revision"]
            or type(value.activation_id) is not str
            or not 1 <= len(value.activation_id) <= 128
            or value.activation_at.utcoffset() is None
            or value.observed_at.utcoffset() is None
            or value.observed_at < value.activation_at
            or any(
                type(n) is not int or n < 0
                for n in (
                    value.revision,
                    value.watermark_ms,
                    value.observed_closed_episodes,
                    value.observed_consecutive_losses,
                )
            )
            or value.observed_consecutive_losses > value.observed_closed_episodes
            or value.initial_equity <= 0
            or value.observed_drawdown < 0
            or value.observed_max_drawdown < value.observed_drawdown
            or value.observed_high_water - value.performance_equity != value.observed_drawdown
            or len(value.open_episodes) > 100
            or len(value.last_closed_positions) > 100
            or len({e.symbol for e in value.open_episodes}) != len(value.open_episodes)
        ):
            _fail()
        for episode in value.open_episodes:
            if (
                any(
                    type(text) is not str or not text or len(text) > 128
                    for text in (episode.symbol, episode.position_id)
                )
                or episode.side not in ("Buy", "Sell")
                or amount(episode.quantity) <= 0
            ):
                _fail()
            amount(episode.net)
        return value
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise AccountLossMetricsError("ACCOUNT_LOSS_METRICS_PERSISTENCE_INVALID") from None


class AccountLossMetricsOwnerV1:
    """Server-internal activation and original-source observation owner."""

    def __init__(self, assembler: BybitLinearSourceAssemblerV1) -> None:
        if type(assembler) is not BybitLinearSourceAssemblerV1:
            _fail()
        self.assembler = assembler
        self._owners = (assembler, assembler.session, assembler.journal)
        self._current: (
            tuple[
                AccountLossMetricsV1,
                BybitLinearVenueReadV1,
                _Read,
                tuple[tuple[AuthenticatedHistoryReceipt, object], ...],
            ]
            | None
        ) = None
        self._busy: object | None = None

    def read_v1(self) -> AccountLossMetricsV1 | None:
        """Historical data only, including after a restart; never a live receipt."""
        record = self.assembler.journal.get_projection(PROJECTION_KEY)
        return None if record is None else _decode(record)

    def _enter(self) -> object:
        if self._busy is not None:
            self._busy, self._current = None, None
            _fail("ACCOUNT_LOSS_METRICS_REENTRY")
        token = object()
        self._busy, self._current = token, None
        return token

    def _require(self, read: BybitLinearVenueReadV1, token: object) -> _Read:
        assembler = self.assembler
        if (
            assembler is not self._owners[0]
            or assembler.session is not self._owners[1]
            or assembler.journal is not self._owners[2]
            or self._busy is not token
        ):
            _fail("ACCOUNT_LOSS_METRICS_SOURCE_REQUIRED")
        assembler.require_current_v1(read)
        slot = assembler._current
        if (
            self._busy is not token
            or slot is None
            or slot.result is not read
            or assembler.journal._own_pending_closed_v2
            or assembler.journal._scope
            != (
                slot.scope.product_scope.account.account_id,
                slot.scope.product_scope.account.environment,
            )
        ):
            _fail("ACCOUNT_LOSS_METRICS_SOURCE_REQUIRED")
        wallet_slot = assembler.session._linear_wallet_current
        if wallet_slot is None or wallet_slot.capture is None:
            _fail("ACCOUNT_LOSS_METRICS_SOURCE_REQUIRED")
        # The original unfiltered wallet response must cover USDT alone. A
        # selected USDT DTO does not prove absence of other account assets.
        assert wallet_slot is not None and wallet_slot.capture is not None
        accounts = _body(wallet_slot.capture.raw_body)["list"]
        if len(accounts) != 1 or any(row.get("coin") != "USDT" for row in accounts[0]["coin"]):
            _fail("ACCOUNT_LOSS_METRICS_ACCOUNT_ASSETS_UNSUPPORTED")
        assert slot is not None
        return slot

    def _save(
        self,
        event: dict[str, Any],
        value: AccountLossMetricsV1,
        read: BybitLinearVenueReadV1,
        token: object,
        expected_revision: int,
        history_links: tuple[tuple[AuthenticatedHistoryReceipt, object], ...] = (),
    ) -> AccountLossMetricsV1:
        journal = self.assembler.journal
        payload = _payload(value)
        # Bound/validate derived data before committing it, including overflow.
        _decode(ProjectionRecord(PROJECTION_KEY, value.revision, _json(payload)))
        slot = self._require(read, token)
        if any(
            self.assembler.session._history_receipts.get(receipt) is not original
            for receipt, original in history_links
        ):
            _fail("ACCOUNT_LOSS_METRICS_HISTORY_REQUIRED")
        saved = journal.apply_event(
            "account-loss-v1-" + _digest(event),
            event,
            PROJECTION_KEY,
            expected_revision,
            payload,
        )
        result = _decode(saved)
        if self._busy is not token or self.assembler._current is not slot:
            _fail("ACCOUNT_LOSS_METRICS_SOURCE_REQUIRED")
        self._current = (result, read, slot, history_links)
        return result

    def activate_v1(
        self, read: BybitLinearVenueReadV1, *, activation_id: str, expected_revision: int
    ) -> AccountLossMetricsV1:
        """Explicit operator action; existing epochs are never automatically reset."""
        token = self._enter()
        try:
            self._require(read, token)
            if (
                type(activation_id) is not str
                or not 1 <= len(activation_id) <= 128
                or type(expected_revision) is not int
                or expected_revision != 0
            ):
                _fail()
            prior = self.read_v1()
            if prior is not None:
                _fail("ACCOUNT_LOSS_METRICS_EPOCH_ALREADY_ACTIVE")
            wallet, full = read.venue.wallet, read.full_reservations
            if (
                read.venue.held_positions
                or read.venue.held_orders
                or full.components
                or read.venue.selected_profile.selected_position_quantity.value != 0
                or any(
                    getattr(full, name) != 0
                    for name in ("risk_amount", "margin_amount", "position_count")
                )
                or any(
                    getattr(wallet, name).value != 0
                    for name in (
                        "total_position_im",
                        "total_order_im",
                        "locked",
                        "bonus",
                        "borrow_amount",
                        "spot_borrow",
                        "accrued_interest",
                        "unrealised_pnl",
                    )
                )
                or any(
                    record.reservation is not None
                    or record.reservation_v2 is not None
                    or record.state
                    in (CommandState.PENDING, CommandState.SENDING, CommandState.UNKNOWN)
                    or not record.terminal_confirmed
                    and record.kind in (CommandKind.ENTRY, CommandKind.EXIT, CommandKind.PROTECTION)
                    for record in self.assembler.journal.list_commands()
                )
            ):
                _fail("ACCOUNT_LOSS_METRICS_FLAT_UNRESERVED_EPOCH_REQUIRED")
            stamp = wallet.source.envelope_time_ms
            if stamp is None or wallet.equity.value <= 0:
                _fail()
            hashes = tuple(bybit_source_ref_sha256_v1(s) for s in read.venue.source_records)
            event = {
                "action": "ACTIVATE_EQUITY_EPOCH_V1",
                "activation_id": activation_id,
                "equity": str(wallet.equity.value),
                "sources": hashes,
            }
            value = AccountLossMetricsV1(
                1,
                activation_id,
                _digest(event),
                wallet.source.received_at,
                wallet.equity.value,
                Decimal(0),
                wallet.equity.value,
                wallet.equity.value,
                Decimal(0),
                Decimal(0),
                0,
                0,
                stamp,
                wallet.wallet_balance.value,
                wallet.source.received_at,
                hashes,
                (),
                (),
            )
            return self._save(event, value, read, token, 0)
        finally:
            if self._busy is token:
                self._busy = None

    def _histories(
        self,
        read: BybitLinearVenueReadV1,
        token: object,
        receipts: tuple[AuthenticatedHistoryReceipt, ...],
    ) -> tuple[HistorySnapshot, ...]:
        slot = self._require(read, token)
        if type(receipts) is not tuple or not 1 <= len(receipts) <= 4:
            _fail("ACCOUNT_LOSS_METRICS_HISTORY_REQUIRED")
        result = tuple(
            self.assembler.session.require_history_receipt(
                receipt, binding=slot.binding, scope=slot.scope.product_scope.account
            ).snapshot
            for receipt in receipts
        )
        for snapshot in result:
            validate_history_snapshot(snapshot)
            if snapshot.transport_injected:
                _fail("ACCOUNT_LOSS_METRICS_HISTORY_REQUIRED")
        if len({s.symbol for s in result}) != len(result):
            _fail("ACCOUNT_LOSS_METRICS_HISTORY_REQUIRED")
        return result

    def observe_v1(
        self,
        read: BybitLinearVenueReadV1,
        *,
        history_receipts: tuple[AuthenticatedHistoryReceipt, ...],
        expected_revision: int,
    ) -> AccountLossMetricsV1:
        """Append a contiguous bounded suffix, never replay the epoch's whole age."""
        token = self._enter()
        try:
            previous = self.read_v1()
            if previous is None:
                _fail("ACCOUNT_LOSS_METRICS_EPOCH_REQUIRED")
            histories = self._histories(read, token, history_receipts)
            history_links = tuple(
                (receipt, self.assembler.session._history_receipts[receipt])
                for receipt in history_receipts
            )
            if type(expected_revision) is not int or expected_revision < 0:
                _fail()
            event = {
                "action": "OBSERVE_EQUITY_EPOCH_V1",
                "activation_id": previous.activation_id,
                "windows": [(s.window.start_ms, s.window.end_ms) for s in histories],
                "histories": [_history_hash(s) for s in histories],
                "sources": [bybit_source_ref_sha256_v1(s) for s in read.venue.source_records],
                "commands": [
                    {
                        "command_key": record.command_key,
                        "client_order_id": record.client_order_id,
                        "kind": record.kind.value,
                        "payload_hash": record.payload_hash,
                    }
                    for record in self.assembler.journal.list_commands()
                    if any(
                        e.client_order_id == record.client_order_id
                        for s in histories
                        for e in s.executions
                    )
                ],
            }
            if previous.observation_id == _digest(event):
                return self._save(event, previous, read, token, expected_revision, history_links)
            if expected_revision != previous.revision:
                raise RevisionConflict("projection revision differs from expected revision")
            end = read.venue.wallet.source.envelope_time_ms
            if (
                end is None
                or end <= previous.watermark_ms
                or any(
                    s.window.start_ms != previous.watermark_ms + 1 or s.window.end_ms != end
                    for s in histories
                )
            ):
                _fail("ACCOUNT_LOSS_METRICS_CONTIGUOUS_SUFFIX_REQUIRED")
            value = self._reduce(previous, read, histories, _digest(event))
            # Re-require original handles after all reduction, before the one CAS.
            self._histories(read, token, history_receipts)
            return self._save(event, value, read, token, expected_revision, history_links)
        finally:
            if self._busy is token:
                self._busy = None

    def _reduce(
        self,
        previous: AccountLossMetricsV1,
        read: BybitLinearVenueReadV1,
        histories: tuple[HistorySnapshot, ...],
        observation_id: str,
    ) -> AccountLossMetricsV1:
        first = histories[0]
        transactions = first.transactions
        if len(transactions) > 64 or any(s.transactions != transactions for s in histories[1:]):
            _fail("ACCOUNT_LOSS_METRICS_ACCOUNT_HISTORY_MISMATCH")
        executions: dict[str, ExecutionRecord] = {}
        for snapshot in histories:
            for item in snapshot.executions:
                if item.execution_id in executions:
                    _fail("ACCOUNT_LOSS_METRICS_EXECUTION_DUPLICATE")
                executions[item.execution_id] = item
        used: set[str] = set()
        episodes = {e.symbol: e for e in previous.open_episodes}
        last_closed = dict(previous.last_closed_positions)
        balance, transfers = previous.cash_balance, previous.cumulative_net_transfers
        streak, closed = previous.observed_consecutive_losses, previous.observed_closed_episodes
        with localcontext() as context:
            context.prec = 4096
            pending = list(transactions)
            while pending:
                stamp = min(row.time_ms for row in pending)
                group = [row for row in pending if row.time_ms == stamp]
                pending = [row for row in pending if row.time_ms != stamp]
                while group:
                    matches = [row for row in group if balance + row.change == row.cash_balance]
                    if len(matches) != 1:
                        _fail("ACCOUNT_LOSS_METRICS_CASH_OR_SEQUENCE_AMBIGUOUS")
                    row = matches[0]
                    group.remove(row)
                    balance = row.cash_balance
                    if row.is_transfer:
                        transfers += row.cash_flow
                        continue
                    if row.transaction_type == "SETTLEMENT":
                        episode = episodes.get(row.symbol)
                        funding = [
                            e
                            for e in executions.values()
                            if e.execution_type == "Funding"
                            and (e.symbol, e.time_ms, e.fee)
                            == (row.symbol, row.time_ms, -row.funding)
                        ]
                        if episode is None or len(funding) != 1 or funding[0].execution_id in used:
                            _fail("ACCOUNT_LOSS_METRICS_FUNDING_ATTRIBUTION_REQUIRED")
                        used.add(funding[0].execution_id)
                        episodes[row.symbol] = _Episode(
                            episode.position_id,
                            episode.symbol,
                            episode.side,
                            episode.quantity,
                            str(amount(episode.net) + row.funding),
                        )
                        continue
                    execution = executions.get(row.trade_id)
                    if (
                        execution is None
                        or execution.execution_type != "Trade"
                        or execution.execution_id in used
                        or (
                            execution.venue_order_id,
                            execution.symbol,
                            execution.time_ms,
                            execution.fee,
                        )
                        != (row.venue_order_id, row.symbol, row.time_ms, row.fee)
                    ):
                        _fail("ACCOUNT_LOSS_METRICS_EXECUTION_COST_REQUIRED")
                    orders = [
                        order
                        for s in histories
                        for order in s.orders
                        if order.venue_order_id == row.venue_order_id
                    ]
                    command = next(
                        (
                            record
                            for record in self.assembler.journal.list_commands()
                            if record.client_order_id == execution.client_order_id
                        ),
                        None,
                    )
                    if (
                        len(orders) != 1
                        or command is None
                        or orders[0].client_order_id != execution.client_order_id
                        or orders[0].side != execution.side
                        or orders[0].position_idx != 0
                        or hashlib.sha256(command.payload_json.encode()).hexdigest()
                        != command.payload_hash
                        or command.kind
                        not in (CommandKind.ENTRY, CommandKind.EXIT, CommandKind.PROTECTION)
                    ):
                        _fail("ACCOUNT_LOSS_METRICS_ORIGINAL_COMMAND_REQUIRED")
                    data = json.loads(command.payload_json)
                    if data.get("schema") == "RISK_V2_DURABLE_INTENT":
                        data = data["fields"]
                    position = data.get("position_id")
                    if type(position) is not str or not position or len(position) > 128:
                        _fail("ACCOUNT_LOSS_METRICS_ORIGINAL_COMMAND_REQUIRED")
                    used.add(execution.execution_id)
                    episode = episodes.get(row.symbol)
                    entry = command.kind is CommandKind.ENTRY
                    if entry:
                        if (
                            orders[0].reduce_only
                            or execution.closed_quantity != 0
                            or row.cash_flow != 0
                            or last_closed.get(row.symbol) == position
                            or episode is not None
                            and (episode.position_id, episode.side) != (position, execution.side)
                        ):
                            _fail("ACCOUNT_LOSS_METRICS_EPISODE_BASIS_REQUIRED")
                        quantity = execution.quantity + (
                            Decimal(0) if episode is None else amount(episode.quantity)
                        )
                        net = -row.fee + (Decimal(0) if episode is None else amount(episode.net))
                        episodes[row.symbol] = _Episode(
                            position, row.symbol, execution.side, str(quantity), str(net)
                        )
                    else:
                        if (
                            episode is None
                            or episode.position_id != position
                            or not orders[0].reduce_only
                            or execution.side == episode.side
                            or execution.closed_quantity != execution.quantity
                            or execution.quantity > amount(episode.quantity)
                        ):
                            _fail("ACCOUNT_LOSS_METRICS_EPISODE_BASIS_REQUIRED")
                        quantity = amount(episode.quantity) - execution.quantity
                        net = amount(episode.net) + row.cash_flow - row.fee
                        if quantity:
                            episodes[row.symbol] = _Episode(
                                position, row.symbol, episode.side, str(quantity), str(net)
                            )
                        else:
                            episodes.pop(row.symbol)
                            last_closed[row.symbol] = position
                            streak = streak + 1 if net < 0 else 0
                            closed += 1
            if set(executions) != used or balance != read.venue.wallet.wallet_balance.value:
                _fail("ACCOUNT_LOSS_METRICS_CASH_OR_EXECUTION_GAP")
            performance = read.venue.wallet.equity.value - transfers
            high = max(previous.observed_high_water, performance)
            drawdown = high - performance
            hashes = (
                observation_id,
                *[bybit_source_ref_sha256_v1(s) for s in read.venue.source_records],
                *[_history_hash(s) for s in histories],
            )
            result = AccountLossMetricsV1(
                previous.revision + 1,
                previous.activation_id,
                observation_id,
                previous.activation_at,
                previous.initial_equity,
                transfers,
                performance,
                high,
                drawdown,
                max(previous.observed_max_drawdown, drawdown),
                closed,
                streak,
                histories[0].window.end_ms,
                balance,
                read.venue.wallet.source.received_at,
                tuple(hashes),
                tuple(sorted(episodes.values(), key=lambda e: e.symbol)),
                tuple(sorted(last_closed.items())),
            )
        return result

    def require_current_v1(self, value: AccountLossMetricsV1) -> AccountLossMetricsV1:
        current = self._current
        if current is None or value is not current[0]:
            _fail("ACCOUNT_LOSS_METRICS_CURRENT_REQUIRED")
        token = self._enter()
        try:
            slot = self._require(current[1], token)
            saved = self.read_v1()
            # Registry identity alone does not enforce the Session receipt TTL.
            # Reuse the original binding/scope and genuine history validator.
            for receipt, _original in current[3]:
                self.assembler.session.require_history_receipt(
                    receipt,
                    binding=current[2].binding,
                    scope=current[2].scope.product_scope.account,
                )
            # All callbacks precede this finite original-owner link check.
            if (
                saved != value
                or slot is not current[2]
                or self._busy is not token
                or self.assembler is not self._owners[0]
                or self.assembler.session is not self._owners[1]
                or self.assembler.journal is not self._owners[2]
                or self.assembler._current is not slot
                or self.assembler.session._closed
                or self.assembler.session._binding is not slot.binding
                or self.assembler.journal._own_pending_closed_v2
                or self.assembler.catalog_producer._current is not slot.catalog_current
                or any(
                    self.assembler.session._linear_private_state_v1(kind)[1] is not original
                    for (kind, _sources), original in zip(
                        slot.sources, slot.private_slots, strict=True
                    )
                )
                or any(
                    self.assembler.public_marks._slots.get(mark.instrument.symbol) is not original
                    for mark, original in zip(current[1].marks, slot.mark_slots, strict=True)
                )
                or any(
                    self.assembler.session._history_receipts.get(receipt) is not original
                    for receipt, original in current[3]
                )
            ):
                _fail("ACCOUNT_LOSS_METRICS_CURRENT_REQUIRED")
            self._current = current
            return value
        finally:
            if self._busy is token:
                self._busy = None
