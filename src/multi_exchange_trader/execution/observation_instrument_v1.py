"""Explicit SDK display metadata with UNKNOWN actual fees; never financial evidence.

SDK 1.231.0 uses 0.001 as missing-fee instrument representation. This version
uses that convention only when explicitly selected for read-only monitoring.
The marker is persisted data, not proof of an original owner, lease or source.
"""

import importlib
from decimal import Decimal
from typing import Any

from .monitoring_models import MonitoringError

ACTUAL_ACCOUNT_FEE_V1 = "ACTUAL_ACCOUNT_FEE_V1"
SDK1231_OBSERVATION_UNKNOWN_FEES_V1 = "SDK1231_OBSERVATION_UNKNOWN_FEES_V1"


def validate_metadata_profile_v1(profile: str) -> str:
    if type(profile) is not str or profile not in (
        ACTUAL_ACCOUNT_FEE_V1, SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
    ):
        raise MonitoringError("MONITORING_METADATA_PROFILE_REQUIRED")
    return profile


def observation_metadata_marker_v1() -> dict[str, str]:
    return {
        "monitoring_metadata_profile": SDK1231_OBSERVATION_UNKNOWN_FEES_V1,
        "actual_account_fee_status": "UNKNOWN",
        "sdk_fee_convention": "0.001",
        "financial_use": "FORBIDDEN",
    }


def require_instrument_metadata_profile_v1(instrument: Any, expected_profile: str) -> None:
    """Validate native metadata only; an original worker must separately own it."""
    validate_metadata_profile_v1(expected_profile)
    p = importlib.import_module("nautilus_trader.core.nautilus_pyo3")
    cython = importlib.import_module("nautilus_trader.model.instruments")
    if type(instrument) not in (p.CryptoPerpetual, cython.CryptoPerpetual):
        raise MonitoringError("MONITORING_METADATA_PROFILE_MISMATCH")
    info = instrument.info
    if info is not None and (type(info) is not dict or any(type(key) is not str for key in info)):
        raise MonitoringError("MONITORING_METADATA_PROFILE_MISMATCH")
    marker = observation_metadata_marker_v1()
    if expected_profile == ACTUAL_ACCOUNT_FEE_V1:
        if info is not None and any(key in info for key in marker):
            raise MonitoringError("MONITORING_METADATA_PROFILE_MISMATCH")
        return
    if (type(info) is not dict or len(info) != len(marker)
            or any(type(value) is not str for value in info.values()) or info != marker
            or type(instrument.maker_fee) is not Decimal or type(instrument.taker_fee) is not Decimal
            or instrument.maker_fee != Decimal("0.001") or instrument.taker_fee != Decimal("0.001")):
        raise MonitoringError("MONITORING_METADATA_PROFILE_MISMATCH")


def convert_observation_instrument_v1(row: dict[str, Any], symbol: str, stamp_ms: int) -> Any:
    """Use raw public instrument geometry without inventing a private fee response."""
    from .native_monitor_http import _instrument_parameters_v1

    p = importlib.import_module("nautilus_trader.core.nautilus_pyo3")
    return p.CryptoPerpetual(**_instrument_parameters_v1(row, symbol, stamp_ms),
        maker_fee=Decimal("0.001"), taker_fee=Decimal("0.001"),
        info=observation_metadata_marker_v1())
