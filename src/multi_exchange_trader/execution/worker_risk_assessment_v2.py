"""Opaque, worker-owned read-only Risk V2 PLAN assessment observations.

This milestone records current owner facts and typed missing-source blockers. It
does not calculate an assessment or grant financial, reservation, or order rights.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, NoReturn, Self, SupportsIndex

from . import risk_v2_contracts as c
from .runtime import RuntimeBlocked

WorkerRiskAssessmentBlockerV2 = Literal[
    "RISK_V2_COST_FUNDING_AND_MINIMUM_RULE_ISSUER_REQUIRED",
    "RISK_V2_ACCOUNT_LOSS_HISTORY_ISSUER_REQUIRED",
    "RISK_V2_FULL_HELD_RISK_DOMAIN_ISSUER_REQUIRED",
    "RISK_V2_MARGIN_LIQUIDATION_READBACK_ISSUER_REQUIRED",
    "RISK_V2_PLAN_ONLY_NO_FINANCIAL_AUTHORITY",
]

WORKER_RISK_ASSESSMENT_BLOCKERS_V2: tuple[WorkerRiskAssessmentBlockerV2, ...] = (
    "RISK_V2_COST_FUNDING_AND_MINIMUM_RULE_ISSUER_REQUIRED",
    "RISK_V2_ACCOUNT_LOSS_HISTORY_ISSUER_REQUIRED",
    "RISK_V2_FULL_HELD_RISK_DOMAIN_ISSUER_REQUIRED",
    "RISK_V2_MARGIN_LIQUIDATION_READBACK_ISSUER_REQUIRED",
    "RISK_V2_PLAN_ONLY_NO_FINANCIAL_AUTHORITY",
)


@dataclass(frozen=True)
class WorkerRiskAssessmentPlanV2:
    """Source-pinned PLAN observation; its fixed blockers prevent numeric approval."""

    scope: c.RiskScopeV2
    signal_id: str
    signal_policy_revision: int
    strategy_publication_generation: int
    policy_revision: str
    policy_sha256: str
    source_profile: Literal["UTA2_V1", "DEMO_UTA1_V1"]
    source_generation: str
    catalog_revision: str
    venue_source_sha256: str
    mark_set_sha256: str
    reservation_snapshot_id: str
    reservation_sha256: str
    evaluation_reservation_snapshot_id: str
    evaluation_reservation_sha256: str
    observed_at: datetime
    valid_until: datetime
    blockers: tuple[WorkerRiskAssessmentBlockerV2, ...] = field(
        default=WORKER_RISK_ASSESSMENT_BLOCKERS_V2, init=False
    )
    stage: Literal["PLAN"] = field(default="PLAN", init=False)
    numeric_assessment: None = field(default=None, init=False)
    financial_authority: Literal[False] = field(default=False, init=False)
    trading_enabled: Literal[False] = field(default=False, init=False)


class WorkerRiskAssessmentHandleV2:
    """Unforgeable reference consumed only by its original assessment owner."""

    __slots__ = ()

    def __new__(cls) -> Self:
        raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_HANDLE_REQUIRED")

    def __copy__(self) -> NoReturn:
        raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_HANDLE_REQUIRED")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_HANDLE_REQUIRED")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise RuntimeBlocked("WORKER_RISK_ASSESSMENT_HANDLE_REQUIRED")
