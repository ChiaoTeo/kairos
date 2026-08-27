from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from kairospy.strategy import (
    StrategyContext as StrategyContextContract,
    StrategyIdentity,
    StrategyLogger,
    StrategyState,
)
from kairospy.strategy.api.clock import StrategyClock
from kairospy.investment.apps.account.application import AccountApplication
from kairospy.strategy.apps.agent.application import AgentApplication
from kairospy.investment.apps.capital.application import CapitalApplication
from kairospy.investment.apps.execution.application import ExecutionApplication
from kairospy.investment.apps.market.application import MarketApplication
from kairospy.strategy.apps.notification.application import NotificationApplication
from kairospy.investment.apps.portfolio.application import PortfolioApplication
from kairospy.investment.apps.risk.application import RiskApplication
from kairospy.investment.apps.reference.application import ReferenceApplication

if TYPE_CHECKING:
    from kairospy.strategy.apps.decisions.application import StrategyDecisionApplication


class StrategyContext(StrategyContextContract):
    """Thin strategy facade holding already-composed business applications."""

    def __init__(
        self,
        strategy_id: str,
        *,
        reference: ReferenceApplication,
        market: MarketApplication,
        account: AccountApplication,
        portfolio: PortfolioApplication,
        capital: CapitalApplication,
        risk: RiskApplication,
        execution: ExecutionApplication,
        agent: AgentApplication | None = None,
        notifications: NotificationApplication | None = None,
        launch_id: str = "",
        instance_id: str = "",
        params: Mapping[str, object] | None = None,
        state_path: Path | None = None,
        state: Mapping[str, object] | None = None,
        logger: StrategyLogger | None = None,
        clock: StrategyClock | None = None,
    ) -> None:
        if not strategy_id.strip():
            raise ValueError("strategy_id is required")
        self.strategy_id = strategy_id
        self.launch_id = launch_id
        self.instance_id = instance_id
        self.identity = StrategyIdentity(strategy_id, launch_id, instance_id)
        self.params = MappingProxyType(dict(params or {}))
        self._event: object | None = None
        self.state = StrategyState(
            state_path,
            strategy_id=strategy_id,
            instance_id=instance_id,
            initial=state,
        )
        self.logger = logger or StrategyLogger(
            fields={"strategy_id": strategy_id, "instance_id": instance_id}
        )
        self.clock = clock or StrategyClock(lambda *args: None, lambda *args: None)
        self.reference = reference
        self.market = market
        self.account = account
        self.portfolio = portfolio
        self.capital = capital
        self.risk = risk
        self.execution = execution
        self.agent = agent or AgentApplication.disabled()
        if notifications is None:
            notifications = NotificationApplication.disabled(
                strategy_id=strategy_id,
                launch_id=launch_id,
                instance_id=instance_id,
            )
        self.notifications = notifications
        self.decisions = cast("StrategyDecisionApplication", None)

    def _bind(self, event: object | None) -> StrategyContext:
        self._event = event
        metadata = getattr(event, "metadata", None)
        sequence = getattr(metadata, "sequence", None)
        occurred_at_unix_nanos = getattr(metadata, "occurred_at_unix_nanos", None)
        occurred_at = getattr(metadata, "occurred_at", None)
        self.market.bind_event(sequence, occurred_at_unix_nanos)
        self.execution.bind_event(sequence, occurred_at_unix_nanos)
        self.agent._bind_event(sequence, occurred_at)
        self.notifications.bind_event(occurred_at)
        return self

    @property
    def event(self):
        return self._event

    @property
    def now(self) -> datetime | None:
        if self.clock.now is not None:
            return self.clock.now
        metadata = getattr(self._event, "metadata", None)
        return None if metadata is None else metadata.occurred_at
