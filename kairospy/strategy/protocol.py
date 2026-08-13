from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, TypeAlias

from kairospy.application.account import AccountApplication, AccountEvent
from kairospy.application.execution import ExecutionApplication, ExecutionEvent
from kairospy.application.market import MarketApplication, MarketEvent
from kairospy.application.reference import ReferenceApplication
from kairospy.application.risk import RiskApplication, RiskEvent

from .clock import StrategyClock
from .commands import StrategyCommand
from .events import ClockEvent, SystemEvent
from .identity import StrategyIdentity
from .logging import StrategyLogger
from .results import CommandResult
from .state import StrategyState


StrategyEvent: TypeAlias = (
    MarketEvent | AccountEvent | RiskEvent | ExecutionEvent | ClockEvent | SystemEvent
)


class StrategyContext(Protocol):
    """Stable, application-oriented surface exposed to strategy code."""

    strategy_id: str
    launch_id: str
    instance_id: str
    identity: StrategyIdentity
    params: Mapping[str, object]
    state: StrategyState
    logger: StrategyLogger
    reference: ReferenceApplication
    market: MarketApplication
    account: AccountApplication
    risk: RiskApplication
    execution: ExecutionApplication
    clock: StrategyClock

    @property
    def event(self) -> StrategyEvent | None: ...


class StrategyProtocol(Protocol):
    strategy_id: str

    def on_start(self, ctx: StrategyContext) -> None: ...
    def on_market(self, ctx: StrategyContext, event: MarketEvent) -> None: ...
    def on_account(self, ctx: StrategyContext, event: AccountEvent) -> None: ...
    def on_risk(self, ctx: StrategyContext, event: RiskEvent) -> None: ...
    def on_execution(self, ctx: StrategyContext, event: ExecutionEvent) -> None: ...
    def on_clock(self, ctx: StrategyContext, event: ClockEvent) -> None: ...
    def on_system(self, ctx: StrategyContext, event: SystemEvent) -> None: ...
    async def on_command(
        self, ctx: StrategyContext, command: StrategyCommand
    ) -> CommandResult: ...
    def on_end(self, ctx: StrategyContext) -> None: ...


class Strategy:
    """Convenience base implementing the complete typed lifecycle."""

    strategy_id = "strategy"
    log_on_market = False

    def on_start(self, ctx: StrategyContext) -> None:
        return None

    def on_market(self, ctx: StrategyContext, event: MarketEvent) -> None:
        return None

    def on_account(self, ctx: StrategyContext, event: AccountEvent) -> None:
        return None

    def on_risk(self, ctx: StrategyContext, event: RiskEvent) -> None:
        return None

    def on_execution(self, ctx: StrategyContext, event: ExecutionEvent) -> None:
        return None

    def on_clock(self, ctx: StrategyContext, event: ClockEvent) -> None:
        return None

    def on_system(self, ctx: StrategyContext, event: SystemEvent) -> None:
        return None

    async def on_command(
        self, ctx: StrategyContext, command: StrategyCommand
    ) -> CommandResult:
        return CommandResult(
            command.request_id,
            "rejected",
            error=f"unsupported strategy command: {command.kind}",
            error_code="unsupported_command",
        )

    def on_end(self, ctx: StrategyContext) -> None:
        return None
