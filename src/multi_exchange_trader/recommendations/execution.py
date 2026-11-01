"""Describe analysis, local PAPER effects and the separate fixed DEMO connectivity test."""

from typing import Any


def demo_capability(registered: bool, *, blocked: bool = False) -> dict[str, Any]:
    return {"registered": registered, "status": "REGISTERED_UNVERIFIED" if registered else "NOT_CONFIGURED",
            "execution": "DEMO", "permission": "DEMO_ORDER_CLIENT", "verified_at": None,
            "permission_verified": False, "order_submission_possible": registered and not blocked,
            "blocked_by_unresolved_attempt": blocked,
            "kind": "FIXED_CONNECTIVITY_ROUND_TRIP", "symbol": "BTCUSDT",
            "market_type": "perpetual", "side": "BUY_THEN_REDUCE_ONLY_SELL", "quantity": "0.001",
            "follows_recommendation_side": False, "verified_closed": False}


def demo_state(payload: dict[str, Any]) -> str:
    if payload.get("demo_close_failed") is True:
        return "DEMO_CLOSE_FAILED"
    if payload.get("demo_round_trip") is True:
        return "DEMO_SUBMISSION_ACKNOWLEDGED"
    if payload.get("demo_attempt_started") is True or payload.get("demo_uncertain") is True:
        return "DEMO_UNCERTAIN"
    return "NOT_STARTED"


def approval_metadata(payload: dict[str, Any], registered: bool, *, globally_blocked: bool = False) -> dict[str, Any]:
    effects: list[dict[str, Any]] = [{"source": "SYNTHETIC", "execution": "PAPER", "kind": "RULE_POSITION"}]
    state = demo_state(payload)
    applies = payload.get("exchange") == "bybit" and (registered or state != "NOT_STARTED")
    eligible = payload.get("symbol") == "BTCUSDT" and payload.get("market_type") == "perpetual"
    if applies:
        effect = demo_capability(registered, blocked=globally_blocked)
        effect.update(receipt_status="SUBMISSION_ACKNOWLEDGED" if state == "DEMO_SUBMISSION_ACKNOWLEDGED" else state,
                      target_supported=eligible)
        effects.append(effect)
    if state == "NOT_STARTED":
        state = "DEMO_REGISTERED_UNVERIFIED" if applies else "PAPER_ONLY"
    if applies and not eligible:
        state = "DEMO_TARGET_UNSUPPORTED"
    elif applies and globally_blocked and state == "DEMO_REGISTERED_UNVERIFIED":
        state = "DEMO_BLOCKED_BY_UNRESOLVED_ATTEMPT"
    return {"approval_effects": effects, "approval_state": state,
            "approval_blocked": state in {"DEMO_CLOSE_FAILED", "DEMO_UNCERTAIN", "DEMO_TARGET_UNSUPPORTED",
                                           "DEMO_BLOCKED_BY_UNRESOLVED_ATTEMPT"}}
