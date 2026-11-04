"""Shared local approval bytes; never a financial authorization."""

import hashlib
import importlib
import json
from types import ModuleType
from typing import Any


def api() -> ModuleType:
    return importlib.import_module("multi_exchange_trader.execution.strategy_publication_v1")



def artifact(name: str = "fixture-alpha", implementation: bytes = b"fixture implementation") -> Any:
    configuration = b'{"window":12,"fixture_only":true}'
    record = {
        "schema_version": 1, "action": "APPROVE_STRATEGY_V1",
        "reference": {"strategy_id": name, "revision": "r1"},
        "implementation_sha256": hashlib.sha256(implementation).hexdigest(),
        "config_sha256": hashlib.sha256(configuration).hexdigest(),
        "config_schema_version": 1, "engine_contract_version": "fixture-engine-v1",
        "permitted_instruments": [{"venue": "BYBIT", "product": "LINEAR", "symbol": "ETHUSDT"}],
        "approval_reference": "fixture-explicit-approval", "approval_source_reference": "fixture-record",
        "approval_environments": ["DEMO"],
    }
    return api().StrategyArtifactBytesV1(implementation, configuration, json.dumps(record).encode())

