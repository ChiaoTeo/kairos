from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, TypeAlias, Union, cast

from kairospy.investment.apps.account.application import AccountApplication
from kairospy.investment.apps.capital.application import CapitalApplication
from kairospy.investment.apps.execution.application import ExecutionApplication
from kairospy.investment.apps.market.application import MarketApplication
from kairospy.strategy.apps.notification.application import NotificationApplication
from kairospy.investment.apps.portfolio.application import PortfolioApplication
from kairospy.investment.apps.risk.application import RiskApplication
from kairospy.investment.apps.reference.application import ReferenceApplication

from .clock import StrategyClock
from .account import AccountEvent
from .commands import StrategyCommand
from .events import ClockEvent, SystemEvent
from .identity import StrategyIdentity
from .logging import StrategyLogger
from .market import BarEvent, GreeksEvent, MarketEvent, QuoteEvent, TradeEvent
from .execution import ExecutionEvent
from .risk import RiskEvent
from .results import CommandResult
from .state import StrategyState
from kairospy.primitives.runtime import InstanceIdRead, LaunchIdRead, StrategyIdRead

if TYPE_CHECKING:
    from kairospy.strategy.apps.agent.application import AgentApplication, AgentEvent
    from kairospy.strategy.apps.decisions.application import (
        StrategyDecisionApplication,
    )


StrategyEvent: TypeAlias = Union[
    "AgentEvent",
    MarketEvent,
    AccountEvent,
    RiskEvent,
    ExecutionEvent,
    ClockEvent,
    SystemEvent,
]


class StrategyContext(Protocol):
    """Stable, application-oriented surface exposed to strategy code."""

    strategy_id: StrategyIdRead
    launch_id: LaunchIdRead
    instance_id: InstanceIdRead
    identity: StrategyIdentity
    params: Mapping[str, object]
    state: StrategyState
    logger: StrategyLogger
    reference: ReferenceApplication
    market: MarketApplication
    account: AccountApplication
    portfolio: PortfolioApplication
    capital: CapitalApplication
    risk: RiskApplication
    execution: ExecutionApplication
    agent: "AgentApplication"
    clock: StrategyClock
    notifications: NotificationApplication
    decisions: "StrategyDecisionApplication"

    @property
    def event(self) -> StrategyEvent | None: ...


class StrategyProtocol(Protocol):
    strategy_id: str

    def on_start(self, ctx: StrategyContext) -> None: ...
    def on_market(self, ctx: StrategyContext, event: MarketEvent) -> None: ...
    def on_account(self, ctx: StrategyContext, event: AccountEvent) -> None: ...
    def on_risk(self, ctx: StrategyContext, event: RiskEvent) -> None: ...
    def on_execution(self, ctx: StrategyContext, event: ExecutionEvent) -> None: ...
    def on_agent(self, ctx: StrategyContext, event: "AgentEvent") -> None: ...
    def on_clock(self, ctx: StrategyContext, event: ClockEvent) -> None: ...
    def on_system(self, ctx: StrategyContext, event: SystemEvent) -> None: ...
    async def on_command(
        self, ctx: StrategyContext, command: StrategyCommand
    ) -> CommandResult: ...
    def on_end(self, ctx: StrategyContext) -> None: ...


class Strategy:
    """Convenience base implementing the complete typed lifecycle.

    Runtime dispatch remains domain-oriented and invokes ``on_market`` once.
    The default implementation then selects one optional typed market hook.
    Override ``on_market`` to take full control, or override the typed hooks
    for the common case.
    """

    strategy_id = "strategy"
    log_on_market = False

    def on_start(self, ctx: StrategyContext) -> None:
        return None

    def on_market(self, ctx: StrategyContext, event: MarketEvent) -> None:
        if event.kind == "quote_updated":
            return self.on_quote(ctx, cast(QuoteEvent, event))
        if event.kind == "bar_completed":
            return self.on_bar(ctx, cast(BarEvent, event))
        if event.kind == "trade_occurred":
            return self.on_trade(ctx, cast(TradeEvent, event))
        if event.kind == "greeks_updated":
            return self.on_greeks(ctx, cast(GreeksEvent, event))

    def on_quote(self, ctx: StrategyContext, event: QuoteEvent) -> None:
        """Handle one quote when using the default market dispatcher."""

        return None

    def on_bar(self, ctx: StrategyContext, event: BarEvent) -> None:
        """Handle one bar when using the default market dispatcher."""

        return None

    def on_trade(self, ctx: StrategyContext, event: TradeEvent) -> None:
        """Handle one trade when using the default market dispatcher."""

        return None

    def on_greeks(self, ctx: StrategyContext, event: GreeksEvent) -> None:
        """Handle one option-greeks observation using the default dispatcher."""

        return None

    def on_account(self, ctx: StrategyContext, event: AccountEvent) -> None:
        return None

    def on_risk(self, ctx: StrategyContext, event: RiskEvent) -> None:
        return None

    def on_execution(self, ctx: StrategyContext, event: ExecutionEvent) -> None:
        return None

    def on_agent(self, ctx: StrategyContext, event: "AgentEvent") -> None:
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
