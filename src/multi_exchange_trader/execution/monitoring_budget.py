"""Operation-local physical HTTP limits, shared across refresh and native startup."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


class MonitoringBudgetError(ValueError):
    """Fixed local budget codes only."""


@dataclass
class _ReceiptBudget:
    deadline: float
    guard: Callable[[], None]
    logical: int = 0


class MonitoringReadBudget:
    """A single resume's 200 sends/60s; receipt scopes add 32 GETs/20s."""

    def __init__(self, *, guard: Callable[[], None] = lambda: None) -> None:
        self._guard = guard
        self._last = time.monotonic()
        self.deadline = self._last + 60.0
        self.physical_attempts = 0
        self._receipt: ContextVar[_ReceiptBudget | None] = ContextVar(
            "monitoring_receipt", default=None
        )

    def check(self) -> None:
        self._guard()
        receipt = self._receipt.get()
        if receipt is not None:
            receipt.guard()
        now = time.monotonic()
        if (
            not math.isfinite(now)
            or now < self._last
            or now >= self.deadline
            or (receipt is not None and now >= receipt.deadline)
        ):
            raise MonitoringBudgetError("MONITORING_DEADLINE_OR_CLOCK")
        self._last = now

    @contextmanager
    def receipt(self, *, guard: Callable[[], None] = lambda: None) -> Iterator[None]:
        self.check()
        token = self._receipt.set(_ReceiptBudget(min(self.deadline, time.monotonic() + 20), guard))
        try:
            yield
        finally:
            self._receipt.reset(token)

    def begin_read(self) -> float:
        self.check()
        receipt = self._receipt.get()
        if receipt is not None:
            if receipt.logical >= 32:
                raise MonitoringBudgetError("MONITORING_LOGICAL_GET_LIMIT")
            receipt.logical += 1
        return self.deadline if receipt is None else min(self.deadline, receipt.deadline)

    def before_attempt(self, attempt: int) -> None:
        self.check()
        if attempt >= 3 or self.physical_attempts >= 200:
            raise MonitoringBudgetError("MONITORING_PHYSICAL_ATTEMPTS")
        self.physical_attempts += 1

    def response(self, status: int, headers: Mapping[str, str]) -> None:
        self.check()
        if status in (403, 429, 451):
            raise MonitoringBudgetError("MONITORING_QUOTA_OR_BAN")
        remaining = headers.get("x-bapi-limit-status")
        if remaining is not None and (not remaining.isdigit() or int(remaining) <= 0):
            raise MonitoringBudgetError("MONITORING_QUOTA_OR_BAN")
