from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from kairospy.application.support.launch.application.lifecycle import TradingLifecycle
from kairospy.application.system.application.resources import TradingSystemResources
from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.support.launch.application.runtime import LaunchRuntimeResult
from kairospy.application.usecases.reference.application.component import ReferenceApplication
from kairospy.application.usecases.workspace.domain.workspace import KairosWorkspace
from kairospy.application.usecases.strategy.protocol import Strategy
from kairospy.infrastructure.persistence.application.reference import SqliteReferenceStore
from kairospy.infrastructure.messaging import InMemoryMessageBus
from kairospy.application.support.composition.application.resources import DriverName, ExchangeName, reference_access


RuntimeLauncher = Callable[..., LaunchRuntimeResult]


@dataclass(frozen=True, slots=True)
class ComposedLaunch:
    """The resource graph handed from composition to launch application.

    Composition selects and wires concrete resources. It does not start the
    runtime, acquire account leases, or write launch artifacts.
    """

    mode: RuntimeMode
    launch_id: str
    strategy: Strategy
    launch_directory: Path
    normalized_config: Mapping[str, object]
    resources: TradingSystemResources
    lifecycle: object | None
    build_result: Callable[[LaunchRuntimeResult], object]


def reference_runtime(
    start: str | Path | None,
    *,
    default_venue: str | None = None,
    default_market: str | None = None,
) -> ReferenceApplication:
    workspace = KairosWorkspace.resolve(start)
    venue = optional_default_text(default_venue)
    market = optional_default_text(default_market)
    source = None
    if venue is None or venue.casefold() == ExchangeName.binance.value:
        source = reference_access(
            "exchange",
            ExchangeName.binance.value,
            market=market or "spot",
            driver_name=DriverName.ccxt,
        )
    return ReferenceApplication(
        SqliteReferenceStore(workspace.reference_root),
        default_venue=venue or ExchangeName.binance.value,
        default_market=market or "spot",
        source=source,
    )


def in_memory_message_bus() -> InMemoryMessageBus:
    """Create the default per-launch bus at the composition boundary."""

    return InMemoryMessageBus()


def optional_default_text(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    return None


__all__ = ["ComposedLaunch", "RuntimeLauncher", "in_memory_message_bus", "optional_default_text", "reference_runtime"]
