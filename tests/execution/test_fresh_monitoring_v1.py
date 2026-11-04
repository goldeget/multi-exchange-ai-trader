"""Portable rejection contracts; actual fresh native startup is Linux-only."""

from __future__ import annotations

import copy
import importlib
import importlib.util
from types import ModuleType, SimpleNamespace

import pytest

from multi_exchange_trader.execution.monitoring_models import MonitoringError


def fresh_module() -> ModuleType:
    name = "multi_exchange_trader.execution.fresh_monitoring_v1"
    assert importlib.util.find_spec(name) is not None, (
        "Separate genuine FRESH startup contract is absent"
    )
    return importlib.import_module(name)


def test_fresh_monitoring_has_separate_unforgeable_origin_and_receipt_handles() -> None:
    module = fresh_module()
    for kind in (module.FreshMonitoringWitnessV1, module.FreshMonitoringReceiptV1):
        with pytest.raises(MonitoringError):
            kind()
        unissued = object.__new__(kind)
        with pytest.raises((MonitoringError, ValueError)):
            copy.copy(unissued)
        with pytest.raises((MonitoringError, ValueError)):
            copy.deepcopy(unissued)


def test_caller_fresh_status_and_generation_cannot_create_a_native_startup_owner() -> None:
    module = fresh_module()
    fake = SimpleNamespace(
        native_restart_status=SimpleNamespace(status="FRESH_EMPTY_CACHE", generation_id=None)
    )
    with pytest.raises((MonitoringError, ValueError, RuntimeError)):
        module.FreshMonitoringBootstrapV1(fake)


def test_old_target_data_and_missing_receipts_have_no_fresh_read_authority() -> None:
    module = fresh_module()
    with pytest.raises(MonitoringError):
        module.require_fresh_target_v1(object(), object(), "PRE")
