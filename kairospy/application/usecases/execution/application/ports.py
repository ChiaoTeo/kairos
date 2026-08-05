"""Execution application resource ports."""

from __future__ import annotations

from typing import Protocol

from kairospy.application.usecases.market.application.commands import DriverName
from kairospy.domain.account import AccountSegment
from kairospy.domain.order import OrderState
from .component import CancelOrderCommand, PlanOrderCommand, SubmitOrderCommand


class AccountOrderExecutor(Protocol):
    """Consumer-owned order capability for one AccountSegment."""

    def plan_order(self, command: PlanOrderCommand) -> OrderState: ...
    def submit_order(self, command: SubmitOrderCommand) -> OrderState: ...
    def cancel_order(self, command: CancelOrderCommand) -> OrderState: ...


class OrderCommandResources(Protocol):
    def account_query_access(self, scope: AccountSegment, driver_name: DriverName, *, credential: str | None = None) -> object: ...
    def execution_access(self, scope: AccountSegment, driver_name: DriverName, *, credential: str | None = None) -> object: ...


__all__ = ["AccountOrderExecutor", "OrderCommandResources"]
