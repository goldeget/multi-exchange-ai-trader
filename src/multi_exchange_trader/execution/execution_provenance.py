"""Untrusted native candidates and exact data comparisons, never fill authority.

Observation references name data for a future authenticated issuer to inspect.
Neither their presence nor MATCH authenticates transport, history completeness,
ownership or execution. This module has no issuer, release or venue effect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .native_events import NativeFillEvidence, _ns, _text, bounded_decimal
from .protection import ProtectionScope, Side

MismatchCode = Literal[
    "SCOPE_MISMATCH", "EXECUTION_ID_MISMATCH", "CLIENT_ORDER_ID_MISMATCH",
    "VENUE_ORDER_ID_MISMATCH", "INSTRUMENT_ID_MISMATCH", "SIDE_MISMATCH",
    "QUANTITY_MISMATCH", "PRICE_MISMATCH", "COMMISSION_MISMATCH",
    "COMMISSION_CURRENCY_MISMATCH", "TS_EVENT_NS_MISMATCH",
]
_COMPARISONS: tuple[tuple[str, MismatchCode], ...] = (
    ("scope", "SCOPE_MISMATCH"), ("execution_id", "EXECUTION_ID_MISMATCH"),
    ("client_order_id", "CLIENT_ORDER_ID_MISMATCH"),
    ("venue_order_id", "VENUE_ORDER_ID_MISMATCH"), ("instrument_id", "INSTRUMENT_ID_MISMATCH"),
    ("side", "SIDE_MISMATCH"), ("quantity", "QUANTITY_MISMATCH"),
    ("price", "PRICE_MISMATCH"), ("commission", "COMMISSION_MISMATCH"),
    ("commission_currency", "COMMISSION_CURRENCY_MISMATCH"),
    ("ts_event_ns", "TS_EVENT_NS_MISMATCH"),
)


@dataclass(frozen=True)
class NativeExecutionCandidate:
    fill: NativeFillEvidence
    venue_order_id: str

    def __post_init__(self) -> None:
        if type(self.fill) is not NativeFillEvidence:
            raise ValueError("NATIVE_EXECUTION_FILL_REQUIRED")
        self.fill.__post_init__()
        _text(self.venue_order_id)


@dataclass(frozen=True)
class VenueExecutionObservation:
    scope: ProtectionScope
    binding_reference: str
    history_batch_id: str
    execution_id: str
    client_order_id: str
    venue_order_id: str
    instrument_id: str
    side: Side
    quantity: Decimal
    price: Decimal
    commission: Decimal
    commission_currency: str
    ts_event_ns: int

    def __post_init__(self) -> None:
        if type(self.scope) is not ProtectionScope:
            raise ValueError("EXECUTION_OBSERVATION_SCOPE_REQUIRED")
        for value in (self.scope.account_id, self.binding_reference, self.history_batch_id,
                      self.execution_id, self.client_order_id, self.venue_order_id,
                      self.instrument_id):
            _text(value)
        if type(self.side) is not str or self.side not in ("BUY", "SELL"):
            raise ValueError("EXECUTION_OBSERVATION_SIDE_REQUIRED")
        for value, positive in ((self.quantity, True), (self.price, True),
                                (self.commission, False)):
            if type(value) is not Decimal:
                raise ValueError("EXECUTION_OBSERVATION_DECIMAL_REQUIRED")
            bounded_decimal(value, positive=positive)
        if (type(self.commission_currency) is not str
                or re.fullmatch(r"[A-Za-z0-9]{1,32}", self.commission_currency) is None):
            raise ValueError("EXECUTION_OBSERVATION_CURRENCY_REQUIRED")
        _ns(self.ts_event_ns)


@dataclass(frozen=True)
class ExecutionComparison:
    state: Literal["MATCH", "CONFLICT"]
    mismatch_codes: tuple[MismatchCode, ...]
    credit_authorized: Literal[False] = False

    def __post_init__(self) -> None:
        allowed = tuple(code for _field, code in _COMPARISONS)
        if (self.credit_authorized is not False or type(self.mismatch_codes) is not tuple
                or any(type(code) is not str or code not in allowed for code in self.mismatch_codes)
                or len(set(self.mismatch_codes)) != len(self.mismatch_codes)
                or self.state != ("CONFLICT" if self.mismatch_codes else "MATCH")):
            raise ValueError("EXECUTION_COMPARISON_INVALID")


@dataclass(frozen=True)
class FillQuarantineRecord:
    candidate: NativeExecutionCandidate
    reason: Literal["AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED"]

    def __post_init__(self) -> None:
        if (type(self.candidate) is not NativeExecutionCandidate
                or self.reason != "AUTHENTICATED_EXECUTION_CORROBORATION_REQUIRED"):
            raise ValueError("EXECUTION_QUARANTINE_INVALID")
        self.candidate.__post_init__()


def compare_execution_candidate(candidate: NativeExecutionCandidate,
                                observation: VenueExecutionObservation) -> ExecutionComparison:
    """Compare identity/economics only; receive/init times and references grant nothing."""
    if (type(candidate) is not NativeExecutionCandidate
            or type(observation) is not VenueExecutionObservation):
        raise ValueError("EXECUTION_COMPARISON_RECORDS_REQUIRED")
    candidate.__post_init__()
    observation.__post_init__()
    codes = tuple(code for field, code in _COMPARISONS
        if getattr(candidate if field == "venue_order_id" else candidate.fill, field)
        != getattr(observation, field))
    return ExecutionComparison("CONFLICT" if codes else "MATCH", codes)
