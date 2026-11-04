"""Source-backed derived DEMO observations, never a verified midnight baseline.

Two stable REST queries and matching wallets cannot detect every omitted prefix
or prove a venue ledger cutoff. Their values remain conditional observations.
The legacy AccountLedger, financial qualification and LIVE permission are untouched.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Literal

from .account_binding import (
    AuthenticatedAccountBinding,
    _HistoryReceiptSlot,
    _LinearAccountInfoSlot,
)
from .bybit_linear_model_inputs_v1 import BybitModelSourceRefV1, bybit_source_ref_sha256_v1
from .bybit_linear_source_assembler_v1 import BybitLinearSourceAssemblerV1
from .history import (
    ExecutionRecord,
    HistorySnapshot,
    OrderHistoryRecord,
    TransactionRecord,
    _symbol,
    canonical_history_decimal,
    milliseconds,
    utc,
    validate_history_snapshot,
)
from .history_receipts import AuthenticatedHistoryReceipt
from .monitoring_budget import MonitoringReadBudget
from .protection import ProtectionScope

_ASSUMPTIONS = (
    "RETURNED_TRANSACTION_MEMBERSHIP_IS_ASSUMED_COMPLETE_FOR_THE_FIXED_UTC_WINDOW",
    "MATCHING_WALLETS_ARE_ASSUMED_TO_SHARE_THE_OBSERVED_TRANSACTION_CUTOFF",
    "TRANSACTION_TIMESTAMP_DEFINES_THE_OBSERVED_UTC_DAY",
)
_LIMITS = (
    "NOT_A_DIRECT_UTC_MIDNIGHT_OBSERVATION",
    "MISSING_PREFIX_CAN_REMAIN_UNDETECTED",
    "REST_DELIVERY_AND_WALLET_CUTOFF_FINALITY_UNPROVEN",
    "PAGE_EXHAUSTION_AND_REPEATED_EQUALITY_ARE_NOT_VENUE_EVENT_COMPLETENESS",
    "ACCOUNT_WIDE_ASSET_AND_EXPOSURE_QUALIFICATION_NOT_ISSUED",
)


class DerivedDailyCashError(ValueError):
    """Fixed local codes without provider payloads or credentials."""


@dataclass(frozen=True)
class DerivedDailyCashObservationV1:
    scope: ProtectionScope
    history_symbol: str
    day: date
    start_ms: int
    through_ms: int
    observation_started_at: datetime
    observed_at: datetime
    valid_until: datetime
    derived_opening_cash: Decimal
    ending_cash: Decimal
    day_net: Decimal
    daily_observed_loss: Decimal
    net_transfers: Decimal
    funding_received: Decimal
    trading_fees_paid: Decimal
    transaction_count: int
    wallet_sources: tuple[BybitModelSourceRefV1, BybitModelSourceRefV1]
    wallet_source_hashes: tuple[str, str]
    history_snapshot_ids: tuple[str, str]
    history_content_hashes: tuple[str, str]
    history_observed_at: tuple[datetime, datetime]
    history_server_observed_at: tuple[datetime, datetime]
    quality_flags: tuple[str, ...]
    method: Literal["DERIVED_UTC_OPENING_CASH_FROM_TX_CHAIN_V1"] = field(
        default="DERIVED_UTC_OPENING_CASH_FROM_TX_CHAIN_V1", init=False
    )
    observational_assumptions: tuple[str, ...] = field(default=_ASSUMPTIONS, init=False)
    direct_midnight_observation: Literal[False] = field(default=False, init=False)
    venue_event_complete: Literal[False] = field(default=False, init=False)
    ledger_qualified: Literal[False] = field(default=False, init=False)
    financial_authority: Literal[False] = field(default=False, init=False)
    live_permission: Literal[False] = field(default=False, init=False)


def _history_hash(snapshot: HistorySnapshot) -> str:
    """Typed source content, explicitly not an unavailable raw-page hash."""
    validate_history_snapshot(snapshot)
    groups: list[list[dict[str, object]]] = []
    record_groups: tuple[
        tuple[tuple[ExecutionRecord | OrderHistoryRecord | TransactionRecord, ...], str], ...
    ] = (
        (snapshot.executions, "execution_id"),
        (snapshot.orders, "venue_order_id"),
        (snapshot.transactions, "transaction_id"),
    )
    for records, identity in record_groups:
        group: list[dict[str, object]] = []
        for record in sorted(records, key=lambda row: str(getattr(row, identity))):
            row: dict[str, object] = {}
            for item in fields(record):
                value = getattr(record, item.name)
                row[item.name] = (
                    canonical_history_decimal(value) if type(value) is Decimal else value
                )
            group.append(row)
        groups.append(group)
    payload = (
        snapshot.scope.account_id,
        snapshot.scope.environment,
        snapshot.symbol,
        snapshot.window.start_ms,
        snapshot.window.end_ms,
        groups,
    )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _cash_chain(snapshot: HistorySnapshot, ending: Decimal) -> tuple[Decimal, ...]:
    """Walk every returned row backwards from independently observed cash."""
    balance = ending
    net = transfers = funding = fees = Decimal(0)
    with localcontext() as context:
        context.prec = 100
        groups: dict[int, list[TransactionRecord]] = {}
        # Validation retains the original parser's supported USDT activity rules.
        validate_history_snapshot(snapshot)
        for record in snapshot.transactions:
            groups.setdefault(record.time_ms, []).append(record)
        for stamp in sorted(groups, reverse=True):
            pending = list(groups[stamp])
            while pending:
                matches = [r for r in pending if r.cash_balance == balance]
                if len(matches) != 1:
                    raise DerivedDailyCashError("DERIVED_CASH_CHAIN_MISSING_OR_AMBIGUOUS")
                record = matches[0]
                pending.remove(record)
                balance -= record.change
                if record.is_transfer:
                    transfers += record.cash_flow
                else:
                    net += record.cash_flow + record.funding - record.fee
                    funding += record.funding
                    fees += record.fee
        if balance + net + transfers != ending:
            raise DerivedDailyCashError("DERIVED_CASH_RECONCILIATION_FAILED")
    return balance, net, transfers, funding, fees


@dataclass(frozen=True)
class _Current:
    result: DerivedDailyCashObservationV1
    binding: AuthenticatedAccountBinding
    receipts: tuple[AuthenticatedHistoryReceipt, AuthenticatedHistoryReceipt]
    epoch: object
    started_mono: float
    max_age_seconds: float
    wallet_slot: _LinearAccountInfoSlot
    wallet_generation: object
    history_registry: dict[object, _HistoryReceiptSlot]
    history_slots: tuple[_HistoryReceiptSlot, _HistoryReceiptSlot]
    history_generation: object


class DerivedDailyCashObserverV1:
    """Own current handles; retired Wallet A is historical data, never authority."""

    def __init__(self, *, assembler: BybitLinearSourceAssemblerV1) -> None:
        if type(assembler) is not BybitLinearSourceAssemblerV1:
            raise DerivedDailyCashError("DERIVED_CASH_ORIGINAL_OWNER_REQUIRED")
        self.assembler = assembler
        self._assembler = assembler
        self._session, self._journal = assembler.session, assembler.journal
        self._clock, self._owner_guard = assembler.clock, assembler.owner_guard
        self._monotonic = self._session._monotonic
        self._session_clock = self._session._clock
        self._current: _Current | None = None
        self._receipts: list[AuthenticatedHistoryReceipt] = []
        self._busy: object | None = None
        self._epoch = object()

    def _retire(self) -> None:
        self._current = None
        self._epoch = object()
        receipts, self._receipts = self._receipts, []
        for receipt in receipts:
            self._session.discard_history_receipt(receipt)

    def _guard(
        self, epoch: object, binding: AuthenticatedAccountBinding, scope: ProtectionScope
    ) -> None:
        if (
            self._epoch is not epoch
            or self.assembler is not self._assembler
            or self.assembler.session is not self._session
            or self.assembler.journal is not self._journal
            or self.assembler.clock is not self._clock
            or self.assembler.owner_guard is not self._owner_guard
            or self._session._monotonic is not self._monotonic
            or self._session._clock is not self._session_clock
            or (self._journal.scope.account_id, self._journal.scope.environment)
            != (scope.account_id, scope.environment)
        ):
            raise DerivedDailyCashError("DERIVED_CASH_ORIGINAL_OWNER_RETIRED")
        self._owner_guard()
        self._session.require_observation_binding(binding, scope=scope)
        if self._epoch is not epoch:
            raise DerivedDailyCashError("DERIVED_CASH_ORIGINAL_OWNER_RETIRED")

    def _check_age(self, slot: _Current) -> None:
        now = utc(self._clock())
        elapsed = self._session._mono() - slot.started_mono
        if (
            not slot.result.observation_started_at <= now < slot.result.valid_until
            or now.date() != slot.result.day
            or not 0 <= elapsed < slot.max_age_seconds
        ):
            raise DerivedDailyCashError("DERIVED_CASH_EXPIRED")

    async def capture_v1(
        self,
        binding: AuthenticatedAccountBinding,
        *,
        scope: ProtectionScope,
        symbol: str,
        budget: MonitoringReadBudget,
        max_age: timedelta = timedelta(seconds=30),
    ) -> DerivedDailyCashObservationV1:
        if self._busy is not None:
            self._busy = None
            self._retire()
            raise DerivedDailyCashError("DERIVED_CASH_OBSERVATION_OVERLAP")
        self._retire()
        token, epoch = object(), self._epoch
        self._busy = token
        try:
            if (
                type(scope) is not ProtectionScope
                or scope.environment != "DEMO"
                or type(budget) is not MonitoringReadBudget
                or type(max_age) is not timedelta
                or not timedelta(0) < max_age <= timedelta(seconds=30)
            ):
                raise DerivedDailyCashError("DERIVED_CASH_INPUT_INVALID")
            started = utc(self._clock())
            started_mono = self._session._mono()
            symbol = _symbol(symbol)
            midnight = started.replace(hour=0, minute=0, second=0, microsecond=0)
            start_ms = milliseconds(midnight)
            through_ms = milliseconds(
                started.replace(microsecond=started.microsecond // 1000 * 1000)
            )

            def guard() -> None:
                self._guard(epoch, binding, scope)

            guard()
            sources: list[BybitModelSourceRefV1] = []
            cash: list[Decimal] = []
            histories: list[HistorySnapshot] = []
            for _ in range(2):
                source = await self._session._capture_linear_wallet_v1(
                    binding, scope=scope, budget=budget, owner_guard=guard
                )
                guard()
                wallet = self._session._project_linear_wallet_v1(
                    source, binding=binding, scope=scope
                )
                sources.append(source)
                cash.append(wallet.wallet_balance.value)
                receipt = await self._session.read_history_receipt(
                    binding, scope=scope, symbol=symbol, start_ms=start_ms, end_ms=through_ms
                )
                self._receipts.append(receipt)
                guard()
                histories.append(
                    self._session.require_history_receipt(
                        receipt, binding=binding, scope=scope
                    ).snapshot
                )
            hashes = (_history_hash(histories[0]), _history_hash(histories[1]))
            if hashes[0] != hashes[1]:
                raise DerivedDailyCashError("DERIVED_CASH_HISTORY_CHANGED")
            if cash[0] != cash[1]:
                raise DerivedDailyCashError("DERIVED_CASH_WALLET_CUTOFF_UNRECONCILED")
            opening, net, transfers, funding, fees = _cash_chain(histories[1], cash[1])
            limits = _LIMITS + (
                ("EMPTY_OBSERVED_WINDOW_DOES_NOT_PROVE_NO_ACTIVITY",)
                if not histories[1].transactions
                else ()
            )
            now = utc(self._clock())
            expiry = min(started + max_age, *(s.request_started_at + max_age for s in sources))
            if now.date() != midnight.date() or now >= expiry:
                raise DerivedDailyCashError("DERIVED_CASH_EXPIRED")
            result = DerivedDailyCashObservationV1(
                scope,
                symbol,
                midnight.date(),
                start_ms,
                through_ms,
                started,
                now,
                expiry,
                opening,
                cash[1],
                net,
                max(Decimal(0), net.copy_negate()),
                transfers,
                funding,
                fees,
                len(histories[1].transactions),
                (sources[0], sources[1]),
                (bybit_source_ref_sha256_v1(sources[0]), bybit_source_ref_sha256_v1(sources[1])),
                (histories[0].snapshot_id, histories[1].snapshot_id),
                hashes,
                (histories[0].observed_at, histories[1].observed_at),
                (histories[0].server_observed_at, histories[1].server_observed_at),
                limits,
            )
            wallet_slot = self._session._linear_wallet_current
            registry = self._session._history_receipts
            if len(self._receipts) != 2:
                raise DerivedDailyCashError("DERIVED_CASH_CURRENT_OBSERVATION_REQUIRED")
            first_receipt, last_receipt = self._receipts
            first_slot, last_slot = registry.get(first_receipt), registry.get(last_receipt)
            if wallet_slot is None or first_slot is None or last_slot is None:
                raise DerivedDailyCashError("DERIVED_CASH_CURRENT_OBSERVATION_REQUIRED")
            self._current = _Current(
                result,
                binding,
                (first_receipt, last_receipt),
                epoch,
                started_mono,
                max_age.total_seconds(),
                wallet_slot,
                self._session._linear_wallet_generation,
                registry,
                (first_slot, last_slot),
                self._session._history_generation,
            )
            self._busy = None
            return self.require_current_v1(result)
        except BaseException:
            self._retire()
            raise
        finally:
            if self._busy is token:
                self._busy = None

    def require_current_v1(
        self, result: DerivedDailyCashObservationV1
    ) -> DerivedDailyCashObservationV1:
        slot = self._current
        if self._busy is not None or slot is None or result is not slot.result:
            self._retire()
            raise DerivedDailyCashError("DERIVED_CASH_CURRENT_OBSERVATION_REQUIRED")
        token = object()
        self._busy = token
        try:
            self._guard(slot.epoch, slot.binding, result.scope)
            self._check_age(slot)
            # Only B is current. A remains the original historical source/hash.
            self._session._require_linear_private_source_v1(
                "WALLET", result.wallet_sources[1], binding=slot.binding, scope=result.scope
            )
            for receipt, expected_id, expected_hash in zip(
                slot.receipts,
                result.history_snapshot_ids,
                result.history_content_hashes,
                strict=True,
            ):
                snapshot = self._session.require_history_receipt(
                    receipt, binding=slot.binding, scope=result.scope
                ).snapshot
                if snapshot.snapshot_id != expected_id or _history_hash(snapshot) != expected_hash:
                    raise DerivedDailyCashError("DERIVED_CASH_HISTORY_CHANGED")
            self._guard(slot.epoch, slot.binding, result.scope)
            self._check_age(slot)
            # Pure retained links after the last guard/clock/monotonic callback.
            # No fresh validator, serializer, hash, SQL or callback follows.
            wallet = slot.wallet_slot
            first_history, last_history = slot.history_slots
            if (
                self._current is not slot
                or self._epoch is not slot.epoch
                or self.assembler is not self._assembler
                or self.assembler.session is not self._session
                or self.assembler.journal is not self._journal
                or self.assembler.clock is not self._clock
                or self.assembler.owner_guard is not self._owner_guard
                or self._session._clock is not self._session_clock
                or self._session._monotonic is not self._monotonic
                or self._session._closed
                or self._session._binding is not slot.binding
                or self._session._identity is not wallet.identity
                or self._session._linear_wallet_current is not wallet
                or self._session._linear_wallet_generation is not slot.wallet_generation
                or wallet.generation is not slot.wallet_generation
                or wallet.binding is not slot.binding
                or wallet.source is not result.wallet_sources[1]
                or wallet.phase != "ISSUED"
                or wallet.retired
                or self._session._history_receipts is not slot.history_registry
                or self._session._history_generation is not slot.history_generation
                or self._session._history_binding is not slot.binding
                or slot.history_registry.get(slot.receipts[0]) is not first_history
                or slot.history_registry.get(slot.receipts[1]) is not last_history
                or first_history.generation is not slot.history_generation
                or last_history.generation is not slot.history_generation
                or first_history.binding is not slot.binding
                or last_history.binding is not slot.binding
                or first_history.view is None
                or last_history.view is None
                or self._journal._own_pending_closed_v2
                or (self._journal.scope.account_id, self._journal.scope.environment)
                != (result.scope.account_id, result.scope.environment)
            ):
                raise DerivedDailyCashError("DERIVED_CASH_CURRENT_OBSERVATION_REQUIRED")
            return result
        except BaseException:
            self._retire()
            raise
        finally:
            if self._busy is token:
                self._busy = None
