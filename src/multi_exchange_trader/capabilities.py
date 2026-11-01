"""Server-owned support contract. Availability is separate from execution authority."""

import importlib.util
import platform
import sys
from typing import Any

from .paper.contract import VERSION


def capabilities(*, account_registered: bool = False,
                 research_v2: bool = False,
                 native_runtime_available: bool | None = None,
                 spot_qualified: bool = False) -> dict[str, Any]:
    if native_runtime_available is None:
        native_runtime_available = (sys.platform == "linux" and platform.machine() == "x86_64"
                                    and importlib.util.find_spec("nautilus_trader") is not None)
    return {
        "schema_version": 1,
        "live_order_enabled": False,
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
                       "fixed_leverage": None if research_v2 else 10,
                       "entry_pause": research_v2, "editable_leverage": research_v2},
        "analysis": {"sources": ["RULE"], "llm_enabled": False},
        "evaluation": {"editable_config": research_v2},
        "ai": {"mode": "OFFLINE_TEST", "external_calls": False,
               "reason": "provider_not_configured"},
    }
