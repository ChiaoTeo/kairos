from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from kairospy.strategy import (
    StrategyContext as StrategyContextContract,
    StrategyIdentity,
    StrategyLogger,
    StrategyState,
)
from kairospy.strategy.clock import StrategyClock
from kairospy.domain_types import AccountId

from ..domain.messages import RawEventEnvelope
from ..protocol import EventStream
from .applications import StrategyApplications

if TYPE_CHECKING:
    from kairospy.infrastructure.contracts.reference_client import ReferenceClient
    from kairospy.infrastructure.contracts.execution import ExecutionMmapProjection
    from kairospy.infrastructure.contracts.risk import RiskMmapProjection
    from kairospy.infrastructure.contracts.account import AccountMmapProjection
    from kairospy.infrastructure.transport.commands import (
        ExecutionCommandClient,
        MarketCommandClient,
    )
    from kairospy.infrastructure.transport.market import MmapMarketSnapshotReader


@dataclass(frozen=True, slots=True)
class StrategyClientBundle:
    """Private process dependencies assembled for one strategy instance."""

    market_commands: MarketCommandClient
    execution_commands: ExecutionCommandClient | None
    market_snapshots: MmapMarketSnapshotReader
    market_events: EventStream
    application_events: tuple[EventStream, ...] = ()
    reference_client: ReferenceClient | None = None
    account_projections: Mapping[AccountId, AccountMmapProjection] = field(
        default_factory=dict
    )
    execution_projection: ExecutionMmapProjection | None = None
    risk_projection: RiskMmapProjection | None = None
    history_root: Path | None = None
    state_path: Path | None = None
    backtest_market: Callable[[RawEventEnvelope], object] | None = None
    backtest_account_mark: Callable[[RawEventEnvelope], object] | None = None
    backtest_time_advance: Callable[[int], object] | None = None


class StrategyContext(StrategyContextContract):
    """Thin strategy facade holding already-composed business applications."""

    def __init__(
        self,
        strategy_id: str,
        *,
        applications: StrategyApplications,
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
        self.reference = applications.reference
        self.market = applications.market
        self.account = applications.account
        self.risk = applications.risk
        self.execution = applications.execution

    def _bind(self, event: object | None) -> StrategyContext:
        self._event = event
        metadata = getattr(event, "metadata", None)
        sequence = getattr(metadata, "sequence", None)
        occurred_at_unix_nanos = getattr(metadata, "occurred_at_unix_nanos", None)
        self.market.bind_event(sequence)
        self.execution.bind_event(sequence, occurred_at_unix_nanos)
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
