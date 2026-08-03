from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from kairospy.application.support.runtime.domain.modes import RuntimeMode
from kairospy.application.support.runtime.domain.lines import RuntimeEventLine
from kairospy.application.support.runtime.domain.components import RuntimeComponents
from kairospy.application.usecases.strategy.protocol import Strategy
from kairospy.infrastructure.integrations.application.market import MarketStreamConnection
from kairospy.application.support.runtime.domain.connections import ConnectionManager

from kairospy.application.support.runtime.application.launch.lifecycle import TradingLifecycle


@dataclass(frozen=True, slots=True)
class RuntimeAssembly:
    """Concrete runtime assembly supplied by the composition root."""

    services: Callable[[RuntimeComponents, object], object]
    projectors: Callable[[str, object, object], object]
    output: Callable[..., object]


@dataclass(frozen=True, slots=True)
class TradingRuntimeResources:
    source: RuntimeEventLine | None = None
    components: RuntimeComponents | None = None
    data: object | None = None
    account: object | None = None
    reference: object | None = None
    trading_execution: object | None = None
    connection_scope: ConnectionManager | None = None
    market_stream_connections: Mapping[str, MarketStreamConnection] | None = None
    market_request_connections: Mapping[str, object] | None = None
    account_connections: Mapping[str, object] | None = None
    execution_connections: Mapping[str, object] | None = None
    reference_connections: Mapping[str, object] | None = None
    assembly: RuntimeAssembly | None = None

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


__all__ = ["RuntimeAssembly", "TradingRuntimeResources", "TradingLaunchSpec"]
