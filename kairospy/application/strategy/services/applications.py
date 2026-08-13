from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kairospy.application.account import AccountApplication
from kairospy.application.execution import ExecutionApplication
from kairospy.application.market import MarketApplication, SubscriptionRequest
from kairospy.application.reference import ReferenceApplication
from kairospy.application.risk import RiskApplication
from kairospy.domain_types import AccountId
from kairospy.infrastructure.contracts.reference_client import ReferenceClient

if TYPE_CHECKING:
    from kairospy.infrastructure.contracts.account import AccountMmapProjection
    from kairospy.infrastructure.contracts.execution import ExecutionMmapProjection
    from kairospy.infrastructure.contracts.risk import RiskMmapProjection
    from kairospy.infrastructure.transport.commands import (
        ExecutionCommandClient,
        MarketCommandClient,
    )
    from kairospy.infrastructure.transport.market import MmapMarketSnapshotReader
    from kairospy.strategy.results import CommandResult


@dataclass(frozen=True, slots=True)
class StrategyApplications:
    """The concrete business applications exposed by StrategyContext."""

    reference: ReferenceApplication
    market: MarketApplication
    account: AccountApplication
    risk: RiskApplication
    execution: ExecutionApplication


def compose_strategy_applications(
    *,
    strategy_id: str,
    instance_id: str,
    market_commands: MarketCommandClient,
    execution_commands: ExecutionCommandClient | None,
    market_snapshots: MmapMarketSnapshotReader,
    reference_client: ReferenceClient | None = None,
    account_projections: Mapping[AccountId, AccountMmapProjection] | None = None,
    execution_projection: ExecutionMmapProjection | None = None,
    risk_projection: RiskMmapProjection | None = None,
    subscription_observer: Callable[[SubscriptionRequest, CommandResult], None]
    | None = None,
) -> StrategyApplications:
    applications = StrategyApplications(
        reference=ReferenceApplication(reference_client),
        market=MarketApplication(
            market_commands,
            market_snapshots,
            strategy_id=strategy_id,
            instance_id=instance_id,
            subscription_observer=subscription_observer,
        ),
        account=AccountApplication(account_projections or {}),
        risk=RiskApplication(risk_projection),
        execution=ExecutionApplication(
            execution_commands,
            execution_projection,
            strategy_id=strategy_id,
            instance_id=instance_id,
        ),
    )
    return applications
