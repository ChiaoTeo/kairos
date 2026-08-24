"""Assemble Strategy from dependencies resolved by System composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from kairospy.investment.application import InvestmentApplication
from kairospy.strategy.apps.agent.composition import AgentProcessComposition
from kairospy.strategy.apps.decisions.services import StrategyDecisionJournal
from kairospy.strategy.apps.notification.composition import (
    NotificationProcessComposition,
)
from kairospy.system.apps.workspace.application import InstanceWorkspace, Workspace

from ..application.runtime import StrategyApplication
from ..services.journal import StrategyLifecycleJournal
from ..services.loader import StrategyEntrypoint
from ..services.rest import StrategyControlServer


@dataclass(frozen=True, slots=True)
class StrategyProcessComposition:
    """Fully assembled one-instance Strategy process."""

    entrypoint: StrategyEntrypoint
    investment: InvestmentApplication
    application: StrategyApplication
    control: StrategyControlServer
    notifications: NotificationProcessComposition
    agent: AgentProcessComposition


def compose_strategy_runtime(
    workspace: Workspace,
    *,
    entrypoint: StrategyEntrypoint,
    instance: InstanceWorkspace,
    investment: InvestmentApplication,
    agent: AgentProcessComposition,
    notifications: NotificationProcessComposition,
    logger: Any,
    config: Any,
    backtest: Any | None,
    params: Mapping[str, object] | None = None,
) -> StrategyProcessComposition:
    """Build only Strategy-owned runtime state from resolved dependencies."""

    lifecycle_routes = config.notifications.get("lifecycle_routes", ())
    if not isinstance(lifecycle_routes, (list, tuple)):
        raise ValueError("notifications.lifecycle_routes must be an array")
    if config.replay_start is not None:
        notifications.application.bind_event(config.replay_start)

    application = StrategyApplication(
        entrypoint.strategy,
        launch_id=instance.launch_id,
        instance_id=instance.instance_id,
        reference=investment.reference,
        market=investment.market,
        account=investment.account,
        portfolio=investment.portfolio,
        capital=investment.capital,
        risk=investment.risk,
        execution=investment.execution,
        agent=agent.application,
        agent_events=None if agent.events is None else agent.events.events,
        agent_synchronize=agent.synchronize if instance.mode == "backtest" else None,
        notifications=notifications.application,
        decision_journal=StrategyDecisionJournal(
            instance.artifact("strategy-decisions.jsonl")
        ),
        decision_notification_routes=tuple(str(route) for route in lifecycle_routes),
        journal=StrategyLifecycleJournal(instance.lifecycle_journal()),
        state_path=instance.state("strategy", "state.json"),
        backtest=backtest,
        params=params,
        logger=logger,
        replay_end=config.replay_end,
    )
    return StrategyProcessComposition(
        entrypoint,
        investment,
        application,
        StrategyControlServer(
            application,
            workspace.paths.launch_socket(
                instance.mode,
                instance.launch_id,
                instance.instance_id,
            ),
        ),
        notifications,
        agent,
    )


__all__ = ["StrategyProcessComposition", "compose_strategy_runtime"]
