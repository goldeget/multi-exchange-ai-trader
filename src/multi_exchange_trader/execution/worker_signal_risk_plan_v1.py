"""Read-only binding of an approved closed-bar signal to a current DEMO plan."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from typing import Literal

from . import bybit_demo_uta1_profile_v1 as demo
from . import risk_v2_contracts as c
from .bybit_linear_entry_producer_v1 import BybitLinearPreparedEntryV1
from .runtime import RuntimeBlocked
from .worker_strategy_signal_v1 import WorkerStrategySignalDataV1


@dataclass(frozen=True)
class WorkerSignalRiskPlanV1:
    """Evaluation snapshot only; it is not an entry decision or submission handle."""

    signal: WorkerStrategySignalDataV1
    prepared_entry: BybitLinearPreparedEntryV1
    assessed_at: datetime
    valid_until: datetime
    financial_authority: Literal[False] = field(default=False, init=False)
    trading_enabled: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if type(self.signal) is not WorkerStrategySignalDataV1 or type(self.prepared_entry) is not BybitLinearPreparedEntryV1:
            raise RuntimeBlocked("WORKER_SIGNAL_RISK_INPUT_REQUIRED")
        signal, prepared = self.signal, self.prepared_entry
        try:
            assessed_at = c._utc_v2(self.assessed_at)
            valid_until = c._utc_v2(self.valid_until)
            direction = signal.direction
            risk_inputs = prepared.inputs.risk_inputs
            proposal = risk_inputs.proposal
            scope_payload = c._scope_payload_v2(signal.scope)
            if (scope_payload != c._scope_payload_v2(risk_inputs.policy.scope)
                    or proposal.scope != signal.scope
                    or risk_inputs.policy.revision != str(signal.policy_revision)
                    or prepared.policy_revision != signal.policy_revision
                    or prepared.publication_generation != signal.publication_generation
                    or signal.scope.product_scope.product != "LINEAR"
                    or signal.scope.product_scope.account.environment != "DEMO"
                    or type(prepared.source_read.venue) is not demo.BybitDemoUta1VenueStateV1):
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_SOURCE_MISMATCH")
            if (direction.side not in ("LONG", "SHORT") or direction.stop_price is None
                    or direction.take_profit_price is None):
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_DIRECTION_REQUIRED")
            if (proposal.side != direction.side
                    or proposal.entry.side != ("BUY" if direction.side == "LONG" else "SELL")
                    or Fraction(proposal.entry.limit_price) != direction.entry_reference_price
                    or Fraction(proposal.stop_price) != direction.stop_price):
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_PROPOSAL_MISMATCH")
            if direction.side == "LONG":
                if not direction.stop_price < direction.entry_reference_price < direction.take_profit_price:
                    raise RuntimeBlocked("WORKER_SIGNAL_RISK_PROTECTION_MISMATCH")
            elif not direction.take_profit_price < direction.entry_reference_price < direction.stop_price:
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_PROTECTION_MISMATCH")
            expected_expiry = min(signal.valid_until, prepared.valid_until, proposal.entry.valid_until)
            if (valid_until != c._utc_v2(expected_expiry) or assessed_at < signal.observed_at
                    or assessed_at < signal.bar_watermark or assessed_at >= valid_until):
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_EXPIRED")
        except RuntimeBlocked:
            raise
        except (AttributeError, TypeError, ValueError, ArithmeticError):
            raise RuntimeBlocked("WORKER_SIGNAL_RISK_INPUT_REQUIRED") from None
        object.__setattr__(self, "assessed_at", assessed_at)
        object.__setattr__(self, "valid_until", valid_until)


def bind_worker_signal_risk_plan_v1(
    signal: WorkerStrategySignalDataV1,
    prepared_entry: BybitLinearPreparedEntryV1,
    *,
    now: datetime,
) -> WorkerSignalRiskPlanV1:
    """Bind a verified signal and an already-current model PLAN without side effects."""
    if type(signal) is not WorkerStrategySignalDataV1 or type(prepared_entry) is not BybitLinearPreparedEntryV1:
        raise RuntimeBlocked("WORKER_SIGNAL_RISK_INPUT_REQUIRED")
    expiry = min(signal.valid_until, prepared_entry.valid_until,
        prepared_entry.inputs.risk_inputs.proposal.entry.valid_until)
    return WorkerSignalRiskPlanV1(signal, prepared_entry, now, expiry)
