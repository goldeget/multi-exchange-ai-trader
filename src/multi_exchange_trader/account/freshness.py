"""Age rules for the read-only account screen.

The numbers below are supervision bounds so an old read cannot look current.
They are not a claim about the best trading interval.
"""

STALE_AFTER_SECONDS = 45
UNUSABLE_AFTER_SECONDS = 120
CACHE_TTL_SECONDS = 8.0
VIEW_DEADLINE_SECONDS = 20.0
POLL_INTERVAL_SECONDS = 15


def freshness_label(age_seconds: int, status: str) -> str:
    """Return the Korean freshness sentence for one read."""
    if status != "ok":
        return "현재 확인 불가"
    if age_seconds >= UNUSABLE_AFTER_SECONDS:
        return "현재 확인 불가"
    if age_seconds >= STALE_AFTER_SECONDS:
        return "갱신 지연"
    return "방금 확인함"


def order_decision_allowed(
    *,
    status: str,
    age_seconds: int,
    sequential: bool,
    marked_usable: bool,
) -> bool:
    """Return whether a later approval step may treat this read as current.

    Balance, positions, and orders are read one after another, so they are not
    one exchange snapshot. A stale or failed read is never usable. This phase
    keeps ``marked_usable`` false, so the screen cannot authorize an order.
    """
    if status != "ok" or sequential or age_seconds >= STALE_AFTER_SECONDS:
        return False
    return marked_usable
