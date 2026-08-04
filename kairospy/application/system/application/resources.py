"""System-owned launch resources and launch specification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from kairospy.application.support.launch.application.lifecycle import TradingLifecycle
from kairospy.application.support.launch.application.resources import LaunchAssembly
from kairospy.application.system.protocol import SystemBusinessFactory
from kairospy.application.support.messaging import MessageBus
from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.usecases.strategy.protocol import Strategy


@dataclass(frozen=True, slots=True)
class TradingSystemResources:
    input_streams: tuple[object, ...] = ()
    data: object | None = None
    account: object | None = None
    reference: object | None = None
    trading_execution: object | None = None
    # Actor composition owns and interprets this dependency. System only carries it through.
    connection_scope: object | None = None
    message_bus: MessageBus | None = None
    assembly: LaunchAssembly | None = None
    business: SystemBusinessFactory | None = None


@dataclass(frozen=True, slots=True)
class TradingLaunchSpec:
    launch_id: str
    mode: RuntimeMode | str
    strategy: Strategy
    resources: TradingSystemResources
    launch_directory: Path
    normalized_config: Mapping[str, object]
    lifecycle: TradingLifecycle | None = None

    def __post_init__(self) -> None:
        if not self.launch_id.strip():
            raise ValueError("launch_id is required")
        object.__setattr__(self, "mode", RuntimeMode(self.mode))


__all__ = ["TradingLaunchSpec", "TradingSystemResources"]
