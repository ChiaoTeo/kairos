from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from kairospy.application.modes import RuntimeMode
from kairospy.application.ports import AccountPort, MarketDataPort, ReferencePort, TradingExecutionPort
from kairospy.application.protocol import RuntimeEventLine
from kairospy.application.strategy import Strategy
from kairospy.application.system.resources.connections import ConnectionManager

from .lifecycle import TradingLifecycle


@dataclass(frozen=True, slots=True)
class TradingRuntimeResources:
    source: RuntimeEventLine | None = None
    data: MarketDataPort | None = None
    account: AccountPort | None = None
    reference: ReferencePort | None = None
    trading_execution: TradingExecutionPort | None = None
    connections: ConnectionManager | None = None


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
