"""Operation-local physical HTTP limits, shared across refresh and native startup."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import NoReturn


class MonitoringBudgetError(ValueError):
    """Fixed local budget codes only."""


@dataclass
class _ReceiptBudget:
    deadline: float
    guard: Callable[[], None]
    owner_guard: Callable[[], None]
    logical: int = 0


class MonitoringReadBudget:
    """A single resume's 200 sends/60s; receipt scopes add 32 GETs/20s."""

    def __init__(self, *, guard: Callable[[], None] = lambda: None) -> None:
        self._guard = guard
        self._last = time.monotonic()
        self.deadline = self._last + 60.0
        self.physical_attempts = 0
        self._terminal_failure: str | None = None
        self._validator: ContextVar[Callable[[dict[str, object], int | None], None] | None] = ContextVar(
            "monitoring_response_validator", default=None
        )
        self._receipt: ContextVar[_ReceiptBudget | None] = ContextVar(
            "monitoring_receipt", default=None
        )

    def _fail(self, code: str) -> NoReturn:
        self._terminal_failure = self._terminal_failure or code
        raise MonitoringBudgetError(self._terminal_failure)

    def _check_limits(self) -> None:
        if self._terminal_failure is not None:
            self._fail(self._terminal_failure)
        receipt = self._receipt.get()
        now = time.monotonic()
        if (
            not math.isfinite(now)
            or now < self._last
            or now >= self.deadline
            or (receipt is not None and now >= receipt.deadline)
        ):
            self._fail("MONITORING_DEADLINE_OR_CLOCK")
        self._last = now

    def check(self) -> None:
        self._check_limits()
        self._guard()
        receipt = self._receipt.get()
        if receipt is not None:
            receipt.guard()

    def read_failed(self) -> NoReturn:
        self._fail("MONITORING_READ_FAILED")

    @contextmanager
    def validating_response(self, validator: Callable[[dict[str, object], int | None], None]
                            ) -> Iterator[None]:
        token = self._validator.set(validator)
        try:
            yield
        finally:
            self._validator.reset(token)

    def validate_response(self, result: dict[str, object], stamp: int | None) -> None:
        validator = self._validator.get()
        if validator is not None:
            try:
                validator(result, stamp)
            except Exception:  # noqa: BLE001 - validation errors are fixed terminal failures.
                self.read_failed()

    @contextmanager
    def receipt(self, *, guard: Callable[[], None] = lambda: None,
                owner_guard: Callable[[], None] = lambda: None) -> Iterator[None]:
        self.check()
        token = self._receipt.set(_ReceiptBudget(
            min(self.deadline, time.monotonic() + 20), guard, owner_guard))
        try:
            yield
        finally:
            self._receipt.reset(token)

    def begin_read(self) -> float:
        receipt = self._receipt.get()
        if receipt is not None and receipt.logical >= 32:
            self._fail("MONITORING_LOGICAL_GET_LIMIT")
        self.check()
        if receipt is not None:
            receipt.logical += 1
        return self.deadline if receipt is None else min(self.deadline, receipt.deadline)

    def before_attempt(self, attempt: int) -> None:
        if attempt >= 3 or self.physical_attempts >= 200:
            self._fail("MONITORING_PHYSICAL_ATTEMPTS")
        self.check()
        self.physical_attempts += 1

    def response(self, status: int, headers: Mapping[str, str]) -> None:
        # A simultaneous target delta must not hide a terminal response.
        if status in (403, 429, 451):
            self._fail("MONITORING_QUOTA_OR_BAN")
        remaining = headers.get("x-bapi-limit-status")
        if remaining is not None and (not remaining.isdigit() or int(remaining) <= 0):
            self._fail("MONITORING_QUOTA_OR_BAN")
        # Bounds/owner checks still run immediately after I/O. Defer only
        # target comparison until the envelope and endpoint rows are validated.
        self._check_limits()
        self._guard()
        receipt = self._receipt.get()
        if receipt is not None:
            receipt.owner_guard()
