from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from kairospy.application.support.launch.modes import RuntimeMode
from kairospy.application.support.runtime.contracts import AccountRuntime, ExecutionRuntime, MarketRuntime, ReferenceRuntime
from kairospy.application.support.runtime.lines import RuntimeEventLine
from kairospy.application.support.runtime.components import RuntimeComponents
from kairospy.application.usecases.strategy.protocol import Strategy
from kairospy.application.support.runtime.connections import ConnectionManager

from .lifecycle import TradingLifecycle


@dataclass(frozen=True, slots=True)
class TradingRuntimeResources:
    source: RuntimeEventLine | None = None
    components: RuntimeComponents | None = None
    data: MarketRuntime | None = None
    account: AccountRuntime | None = None
    reference: ReferenceRuntime | None = None
    trading_execution: ExecutionRuntime | None = None
    connections: ConnectionManager | None = None

    def runtime_components(self) -> RuntimeComponents:
        if self.components is not None:
            return self.components
        return RuntimeComponents(
            market=self.data,
            account=self.account,
            account_catalog=self.account,
            execution=self.trading_execution,
            reference=self.reference,
        )


@dataclass(frozen=True, slots=True)
class TradingLaunchSpec:
    launch_id: str
    mode: RuntimeMode | str
    strategy: Strategy
    resources: TradingRuntimeResources
    launch_directory: Path
    normalized_config: Mapping[str, object]
    lifecycle: TradingLifecycle | None = None

    def __post_init__(self) -> None:
        if not self.launch_id.strip():
            raise ValueError("launch_id is required")
        object.__setattr__(self, "mode", RuntimeMode(self.mode))


__all__ = ["TradingRuntimeResources", "TradingLaunchSpec"]
