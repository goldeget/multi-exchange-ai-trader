"""Server-owned support contract. Availability is separate from execution authority."""

import importlib.util
import platform
import sys
from typing import Any

from .paper.contract import VERSION
from .recommendations.execution import demo_capability


def capabilities(*, account_registered: bool = False,
                 research_v2: bool = False,
                 native_runtime_available: bool | None = None,
                 spot_qualified: bool = False,
                 practice_registered: bool = False, practice_blocked: bool = False) -> dict[str, Any]:
    if native_runtime_available is None:
        native_runtime_available = (sys.platform == "linux" and platform.machine() == "x86_64"
                                    and importlib.util.find_spec("nautilus_trader") is not None)
    return {
        "schema_version": 1,
        "live_order_enabled": False,
        "demo_order_enabled": practice_registered and not practice_blocked,
        "recommendations": {"source": "RULE", "local_ledger_execution": "PAPER",
                            "demo_connectivity_test": demo_capability(practice_registered, blocked=practice_blocked)},
        "account_readonly": {"bybit": account_registered},
        "manual_paper": {
            "source": "SYNTHETIC", "execution": "PAPER", "config_revision": VERSION,
            "available": native_runtime_available,
            "runtime_check": "PREREQUISITES_ONLY",
            "unavailable_reason": None if native_runtime_available else "native_sandbox_unavailable",
            "synthetic_perpetual": {"enter": True, "close": True, "protect": True,
                                    "order_preview": True, "leverage_min": 1, "leverage_max": 20},
            "spot_market": {"supported": spot_qualified, "reason": None if spot_qualified else "not_qualified",
                            "config_revision": "SPOT-PAPER-v2"},
            "spot_limit": {"supported": spot_qualified, "reason": None if spot_qualified else "not_qualified",
                           "config_revision": "SPOT-PAPER-v2"},
        },
        "automation": {"contract": "SYNTHETIC_AUTO_V2" if research_v2 else "SYNTHETIC_AUTO_V1",
                       "available": native_runtime_available,
                       "unavailable_reason": None if native_runtime_available else "native_sandbox_unavailable",
                       "fixed_leverage": None if research_v2 else 10,
                       "entry_pause": research_v2, "editable_leverage": research_v2},
        "analysis": {"sources": ["RULE"], "llm_enabled": False},
        "evaluation": {"editable_config": research_v2, "available": native_runtime_available,
                       "unavailable_reason": None if native_runtime_available else "native_sandbox_unavailable"},
        "ai": {"mode": "OFFLINE_TEST", "external_calls": False,
               "reason": "provider_not_configured"},
    }
