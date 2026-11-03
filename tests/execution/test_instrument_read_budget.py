"""Real shared budget scopes, entirely offline."""

import asyncio

import pytest

from multi_exchange_trader.execution.monitoring_budget import (
    MonitoringBudgetError,
    MonitoringReadBudget,
)


def test_reuse_preserves_identity_counters_and_deadline() -> None:
    budget = MonitoringReadBudget()
    with budget.reuse_or_open_receipt() as group:
        initial = budget.require_active_group(group)
        budget.begin_read()
        with budget.reuse_or_open_receipt() as reused:
            assert reused is group
            view = budget.require_active_group(reused)
            assert view.deadline == initial.deadline
            assert (view.logical_gets, view.remaining_gets) == (1, 31)
    with pytest.raises(MonitoringBudgetError, match="MONITORING_GROUP_REQUIRED"):
        budget.require_active_group(group)


def test_foreign_token_and_constructed_token_are_rejected() -> None:
    first, second = MonitoringReadBudget(), MonitoringReadBudget()
    with first.reuse_or_open_receipt() as group, second.reuse_or_open_receipt():
        with pytest.raises(MonitoringBudgetError, match="MONITORING_GROUP_REQUIRED"):
            second.require_active_group(group)
        with pytest.raises(MonitoringBudgetError, match="MONITORING_GROUP_REQUIRED"):
            type(group)()


def test_legacy_scope_is_joined_without_replacing_guards() -> None:
    calls: list[str] = []
    budget = MonitoringReadBudget()
    with budget.receipt(guard=lambda: calls.append("guard")):
        budget.begin_read()
        with budget.reuse_or_open_receipt() as group:
            assert budget.require_active_group(group).logical_gets == 1
            assert calls


async def test_ended_scope_cannot_survive_copied_child_context() -> None:
    budget = MonitoringReadBudget()
    ready, release = asyncio.Event(), asyncio.Event()

    async def child(group: object) -> None:
        ready.set()
        await release.wait()
        with pytest.raises(MonitoringBudgetError, match="MONITORING_GROUP_REQUIRED"):
            budget.require_active_group(group)  # type: ignore[arg-type]
        with (pytest.raises(MonitoringBudgetError, match="MONITORING_GROUP_REQUIRED"),
              budget.reuse_or_open_receipt()):
            pass

    with budget.reuse_or_open_receipt() as group:
        task = asyncio.create_task(child(group))
        await ready.wait()
    release.set()
    await task
