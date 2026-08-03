from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from kairospy.application.support.runtime.application.launch.lifecycle import TradingLifecycle
from kairospy.application.support.runtime.application.launch.resources import TradingRuntimeResources
from kairospy.application.support.runtime.domain.modes import RuntimeMode
from kairospy.application.support.runtime.application.launch import RuntimeLaunchResult
from kairospy.application.usecases.reference.application.runtime import ReferenceCatalogService
from kairospy.application.support.system.application.workspace import KairosWorkspace
from kairospy.application.usecases.strategy.protocol import Strategy
from kairospy.infrastructure.persistence.application.reference import SqliteReferenceStore


RuntimeLauncher = Callable[..., RuntimeLaunchResult]


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
    resources: TradingRuntimeResources
    lifecycle: object | None
    build_result: Callable[[RuntimeLaunchResult], object]


def reference_runtime(
    start: str | Path | None,
    *,
    default_venue: str | None = None,
    default_market: str | None = None,
) -> ReferenceCatalogService:
    workspace = KairosWorkspace.resolve(start)
    return ReferenceCatalogService(
        SqliteReferenceStore(workspace.reference_root),
        default_venue=optional_default_text(default_venue),
        default_market=optional_default_text(default_market),
    )


def optional_default_text(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    return None


__all__ = ["ComposedLaunch", "RuntimeLauncher", "optional_default_text", "reference_runtime"]
