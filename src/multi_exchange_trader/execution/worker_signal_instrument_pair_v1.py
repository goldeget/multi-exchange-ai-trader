"""Read-only, explicitly unmapped public-catalog/DEMO-instrument snapshot."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from multi_exchange_trader.trading_product import InstrumentKey

from ..account.instrument_envelope import HttpsOrigin
from . import risk_v2_contracts as c
from .runtime import RuntimeBlocked

_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_DEMO_ORIGIN = HttpsOrigin("https", "api-demo.bybit.com", 443)


@dataclass(frozen=True)
class WorkerSignalRiskInstrumentPairV1:
    """One current signal/plan key observed from two owners, with no mapping claim."""

    signal_id: str
    signal_catalog_revision: str
    plan_catalog_revision: str
    public_catalog_revision: str
    policy_revision: int
    publication_generation: int
    instrument: InstrumentKey
    public_source_environment: Literal["PUBLIC_MAINNET"]
    public_source_id: str
    public_endpoint: str
    public_specification_revision: str
    public_raw_row_sha256: str
    public_response_time: datetime
    public_received_at: datetime
    demo_origin: HttpsOrigin
    demo_target_fingerprint: str
    demo_native_specification_revision: str
    demo_native_semantics_sha256: str
    demo_receipt_issued_at: datetime
    status: Literal["OBSERVED_PAIR_UNMAPPED"] = field(default="OBSERVED_PAIR_UNMAPPED", init=False)
    financial_authority: Literal[False] = field(default=False, init=False)
    trading_enabled: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        try:
            if (type(self.signal_id) is not str or not self.signal_id
                    or type(self.signal_catalog_revision) is not str
                    or _DIGEST.fullmatch(self.signal_catalog_revision) is None
                    or type(self.plan_catalog_revision) is not str
                    or _DIGEST.fullmatch(self.plan_catalog_revision) is None
                    or type(self.public_catalog_revision) is not str
                    or _DIGEST.fullmatch(self.public_catalog_revision) is None
                    or self.signal_catalog_revision != self.plan_catalog_revision
                    or self.signal_catalog_revision != self.public_catalog_revision
                    or type(self.policy_revision) is not int or self.policy_revision < 0
                    or type(self.publication_generation) is not int or self.publication_generation < 0
                    or type(self.instrument) is not InstrumentKey
                    or self.instrument.venue != "BYBIT" or self.instrument.product != "LINEAR"
                    or type(self.public_source_environment) is not str
                    or self.public_source_environment != "PUBLIC_MAINNET"
                    or type(self.public_source_id) is not str or not self.public_source_id
                    or type(self.public_endpoint) is not str
                    or self.public_endpoint != "/v5/market/instruments-info"
                    or type(self.public_specification_revision) is not str
                    or _DIGEST.fullmatch(self.public_specification_revision) is None
                    or type(self.public_raw_row_sha256) is not str
                    or _DIGEST.fullmatch(self.public_raw_row_sha256) is None
                    or type(self.demo_origin) is not HttpsOrigin or self.demo_origin != _DEMO_ORIGIN
                    or type(self.demo_target_fingerprint) is not str
                    or not self.demo_target_fingerprint
                    or type(self.demo_native_specification_revision) is not str
                    or _DIGEST.fullmatch(self.demo_native_specification_revision) is None
                    or type(self.demo_native_semantics_sha256) is not str
                    or _DIGEST.fullmatch(self.demo_native_semantics_sha256) is None):
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_INSTRUMENT_PAIR_INVALID")
            response = c._utc_v2(self.public_response_time)
            received = c._utc_v2(self.public_received_at)
            issued = c._utc_v2(self.demo_receipt_issued_at)
            if response > received:
                raise RuntimeBlocked("WORKER_SIGNAL_RISK_INSTRUMENT_PAIR_INVALID")
        except RuntimeBlocked:
            raise
        except (AttributeError, TypeError, ValueError, OverflowError):
            raise RuntimeBlocked("WORKER_SIGNAL_RISK_INSTRUMENT_PAIR_INVALID") from None
        object.__setattr__(self, "public_response_time", response)
        object.__setattr__(self, "public_received_at", received)
        object.__setattr__(self, "demo_receipt_issued_at", issued)
